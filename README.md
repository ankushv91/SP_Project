# Development

Install the project and its development dependencies with `uv sync --dev`.

Run the API test suite and generate the coverage report with `uv run pytest`.
The test command enforces more than 80% coverage for the `app` package.

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
are stopped. To remove the database data as well, run `docker compose down -v`.

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
configure AWS credentials using a supported AWS SDK credential provider. The
API fails to start if the secret cannot be retrieved or has no non-empty
`SecretString`.
