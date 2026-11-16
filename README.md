# Development

Install the project and its development dependencies with `uv sync --dev`.

Run the API test suite and generate the coverage report with `uv run pytest`.
The test command enforces more than 80% coverage for the `app` package.

## Run the API and PostgreSQL with Docker Compose

Install Docker Desktop (or Docker Engine with the Compose plugin), then optionally
copy `.env.example` to `.env` and adjust the PostgreSQL credentials. The example
values are for local development only; use secure secrets for any deployment.

Start both services from the repository root:

```powershell
docker compose up --build -d
```

Compose starts PostgreSQL first and waits for its health check before starting
the API. The API connects to the `db` service using the `DATABASE_URL` set in
Compose; it does not use `localhost` to reach PostgreSQL. The database data is
stored in the `postgres_data` named volume.

PostgreSQL uses `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` only
when initializing an empty data volume. Changing these values in `.env` does not
change credentials in an existing database; update the database roles to match,
or remove the volume only if you intend to delete its data.

Confirm that the API can query PostgreSQL:

```powershell
Invoke-RestMethod http://localhost:8000/health-check
```

The response should report `"status": "success"`. The API is also available at
`http://localhost:8000/docs`. To inspect service logs, run
`docker compose logs -f api db`.

Stop and remove the containers and network with:

```powershell
docker compose down
```

The named database volume is retained, so data is preserved when the containers
are stopped. To remove the database data as well, run `docker compose down -v`.
