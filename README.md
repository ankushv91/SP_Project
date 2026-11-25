[![Tests and Docker publish](https://github.com/ankushv91/SP_Project/actions/workflows/tests.yml/badge.svg?branch=master)](https://github.com/ankushv91/SP_Project/actions/workflows/tests.yml)

# Development

Install the project and its development dependencies with `uv sync --dev`.

Run the API test suite and generate the coverage report with `uv run pytest`.
The test command enforces more than 80% coverage for the `app` package.

## Publish the API Docker image

When a push or merge reaches `master` and the test job passes, the workflow builds
the root `Dockerfile` and pushes two tags to Docker Hub:

- `DOCKERHUB_USERNAME/sp-project:latest`
- `DOCKERHUB_USERNAME/sp-project:sha-<commit>`

Before the first publish:

1. Create a Docker Hub repository named `sp-project` under your Docker Hub
   account.
2. In Docker Hub, create an access token with permission to read and write
   repositories.
3. In the GitHub repository, open **Settings → Secrets and variables →
   Actions → New repository secret**. Add `DOCKERHUB_USERNAME` with your
   Docker Hub username, and `DOCKERHUB_TOKEN` with the access token value.

Do not commit the access token or put it in `.env`. After a successful publish,
you can pull the image with
`docker pull DOCKERHUB_USERNAME/sp-project:latest` (replace the placeholder
with your Docker Hub username).

## Run the API, PostgreSQL, and Redis with Docker Compose

Install Docker Desktop (or Docker Engine with the Compose plugin). If `.env`
does not exist, copy `.env.example` to create it; otherwise, preserve your
existing `.env` and add or update the documentation settings there. Set
`DOCS_AUTH_USER` and replace the example `DOCS_AUTH_PASSWORD` with a unique,
random value. For example, generate a password with
`python -c "import secrets; print(secrets.token_urlsafe(32))"` and put the
result in `.env`. The example values are for local development only; use secure
secrets for any deployment.

Start the services from the repository root:

```powershell
docker compose up --build -d
```

Compose starts PostgreSQL and Redis and waits for their health checks before
starting the API. The API connects to these services by their Compose names
(`db` and `redis`), not `localhost`. Redis caches todo list responses for 60
seconds and stores atomic sliding-window rate limits per client IP and endpoint.
Creating, updating, or deleting a todo invalidates the todo list cache. Rate
limits are configured in `app/core/rate_limit.py`; requests exceeding a limit
receive HTTP 429 and a `Retry-After` header. If Redis is unavailable, limited
routes return HTTP 503 rather than bypassing the limiter. The database data is
stored in the `postgres_data` named volume.

| Endpoint | Limit |
| --- | ---: |
| `GET /health-check` | 20/minute |
| `POST /auth/register` | 5/minute |
| `POST /auth/token` | 10/minute |
| `GET /auth/me` | 60/minute |
| `GET /todos` | 60/minute |
| `POST /todos` | 30/minute |
| `GET /todos/{todo_id}` | 60/minute |
| `PUT /todos/{todo_id}` | 30/minute |
| `DELETE /todos/{todo_id}` | 30/minute |

## Load-test rate limits with Locust

Install the optional load-test dependencies with `uv sync --group load`, then
start the API, PostgreSQL, and Redis as described above. Run a headless test
against the local API:

```powershell
uv run locust -f locustfile.py --headless --host http://localhost:8000 -u 20 -r 5 -t 30s
```

The script repeatedly calls `/health-check`, reports 429 responses as rate-limit
failures in Locust's statistics, and exits unsuccessfully if the run never
receives a 429. The health-check limit is 20 requests per minute per client IP.

PostgreSQL uses `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` only
when initializing an empty data volume. Changing these values in `.env` does not
change credentials in an existing database; update the database roles to match,
or remove the volume only if you intend to delete its data.

The example password is a placeholder and is rejected by the Nginx container.
The password is used to create a bcrypt Basic Auth credential file inside that
container; it is not committed to the repository. For production, provide it
through your deployment's secret management rather than a checked-in file.

Confirm that the API can query PostgreSQL:

```powershell
Invoke-RestMethod http://localhost:8000/health-check
```

The response should report `"status": "success"`; database failures return
HTTP 503. The API is available at
`http://localhost:8000`; its built-in `/docs`, `/redoc`, and `/openapi.json`
routes are disabled so they cannot bypass authentication. Open the protected
Swagger UI at `http://localhost:8081` (or the port configured by `DOCS_PORT`)
and enter `DOCS_AUTH_USER` and `DOCS_AUTH_PASSWORD` when prompted. To inspect
service logs, run
`docker compose logs -f api db`.

The API generates `openapi.json` during startup, and Compose shares that file
with the standalone Swagger UI. Pydantic request and response models include
examples that appear in the generated schema. After changing API routes or
schemas, rebuild/restart the API; no separate schema-generation command is
needed. Unexpected `.env` settings are logged by name at startup.

## Choose an API version with the Accept header

The API supports `v1` and `v2` at the same endpoint paths. Select a version
with `Accept: application/vnd.sp-project.v1+json` or
`Accept: application/vnd.sp-project.v2+json`. Existing clients that omit
`Accept`, or request generic JSON, continue to use `v1`. Unsupported versions
and unacceptable media types receive HTTP 406. Responses include an
`X-API-Version` header and vary on `Accept`.

After changing the API code, rebuild the API container:

```powershell
docker compose up --build -d api
```

Try both routers from PowerShell:

```powershell
curl.exe -i -H "Accept: application/vnd.sp-project.v1+json" http://localhost:8000/version
curl.exe -i -H "Accept: application/vnd.sp-project.v2+json" http://localhost:8000/version
curl.exe -i -H "Accept: application/vnd.sp-project.v9+json" http://localhost:8000/version
```

The first two requests should return their requested version in both the JSON
body and `X-API-Version` response header. The last should return HTTP 406.
Each router also accepts `Accept: application/json; version=1` or `version=2`.
The generated OpenAPI documents are available at
`http://localhost:8000/openapi/v1.json` and
`http://localhost:8000/openapi/v2.json`. The protected Swagger UI continues to
show the default `v1` schema.

## Collect API logs with Elasticsearch and Kibana

The Compose stack also starts Elasticsearch, Kibana, and Filebeat. Filebeat
reads Docker's container log files but indexes only the API container, using
the `log_source` label. Logs are searchable in the `filebeat-*` index pattern.
This local-development setup disables Elasticsearch security and binds both
web ports to localhost; do not expose these services to an untrusted network.
Elasticsearch data is retained in the `elasticsearch_data` volume.
Compose supplies a development-only Kibana encryption key so alert rules can
be created. You can override it with a unique, random value of at least 32
characters in `.env` as `KIBANA_ENCRYPTION_KEY`; never use the development
default in a deployed environment.

Start or update the stack:

```powershell
docker compose up --build -d
docker compose ps
```

Allow Elasticsearch and Kibana a minute or two to initialize, then open
`http://localhost:5601`.

### Verify the Kibana dashboard and alert

The `kibana-setup` Compose service imports the `API logs` data view and
`API logs overview` dashboard, then creates the `API error logs` rule. It waits
for Kibana and the Filebeat index before provisioning. No manual Kibana setup
is required.

Open `http://localhost:5601`:

1. In **Discover**, select the `API logs` data view and set the time picker to
   **Last 15 minutes**. If it is empty, make a request to
   `http://localhost:8000/health-check`, wait briefly, and refresh.
2. Open **Analytics → Dashboards → API logs overview** to view the saved API
   log table.
3. Open **Observability → Alerts → Manage Rules** and confirm that
   `API error logs` is enabled. It checks every minute for `ERROR` log messages
   in the last five minutes.

To see why setup did not complete, run
`docker compose logs kibana-setup`. Rerun the idempotent setup with
`docker compose run --rm kibana-setup`. The rule appears in Kibana but sends no
email or chat until you configure an action connector.

Useful checks:

```powershell
Invoke-RestMethod http://localhost:9200
docker compose logs --tail 100 filebeat elasticsearch kibana
```

If Elasticsearch exits with a virtual-memory map error on a native Linux
Docker host, set `vm.max_map_count=262144` on that host and restart the stack.
The Kibana alert rule has no external notification action until you configure
a connector.

Verify the docs proxy by opening it in a private browser window (which should
prompt for credentials), then enter the configured username and password. A
request without credentials should receive HTTP 401.
The docs port is bound to the local machine; use HTTPS and managed secrets if
you expose documentation beyond localhost.

## Inspect Todo notification emails

Creating a Todo schedules a notification to `TODO_NOTIFICATION_EMAIL` after
the Todo has been saved. The local Compose stack uses MailHog as a dummy SMTP
server, so no real email is sent. Configure `SMTP_FROM_EMAIL` and
`TODO_NOTIFICATION_EMAIL` in `.env` if needed, then create a Todo through
`POST /todos`. The API log records when the notification is queued and sent;
SMTP delivery errors are logged with the Todo ID. View captured messages at
`http://localhost:8025` (or the port configured by `MAILHOG_UI_PORT`).

Stop and remove the containers and network with:

```powershell
docker compose down
```

The named database volume is retained, so data is preserved when the containers
are stopped. To remove the database and Elasticsearch data as well, run
`docker compose down -v`.

## Load the JWT signing secret from AWS Secrets Manager

Create a secret in AWS Secrets Manager with the name configured by
`AWS_SECRET_NAME` (default: `sp-project/jwt-secret`). Store the JWT signing key
as the secret's plain-text value (`SecretString`), not as JSON; use at least 32
bytes of random key material for the default HS256 algorithm. In the AWS
console, open **Secrets Manager**, choose **Store a new secret** → **Other type
of secret**, enter the key as plain text, and set the secret name. Set
`AWS_REGION` to the region containing the secret. When the API starts, it
retrieves this value using the AWS SDK credential chain; do not put AWS
credentials or the JWT secret in the application environment.

Grant the API's AWS execution role permission to call
`secretsmanager:GetSecretValue` for that secret. For local or Compose runs,
configure a profile with the AWS CLI on the host. Compose passes `AWS_PROFILE`
to the API and mounts the host's AWS configuration directory read-only inside
the container (`%USERPROFILE%\.aws` on Windows or `$HOME/.aws` on Linux/macOS).
Set `AWS_CONFIG_DIR` in `.env` if the directory is elsewhere and `AWS_PROFILE`
to the profile name (or use the `default` profile); never put AWS access keys
in `.env`. Use a development profile with only the permissions needed to read
the configured secret. The API fails to start if the secret cannot be
retrieved or has no non-empty `SecretString`.
