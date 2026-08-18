# OpenActs API

Read-only FastAPI service for the active PostgreSQL corpus projection. The
authored corpus remains canonical; this service never imports or edits it.

## Run locally

From the repository root:

```sh
cp .env.example .env
```

Set `POSTGRES_PASSWORD` and `OPENACTS_API_PASSWORD` in `.env`, then run:

```sh
make api-check
make db-up
make projection-execute RELEASE=corpus-v0.0.0 ALLOW_BOOTSTRAP=1
make dev
```

Then inspect `http://127.0.0.1:8000/healthz`, `/readyz`, or `/v1/meta`.
`make dev` starts the Compose-managed PostgreSQL service before the API. Corpus
activation is an explicit one-time step for each fresh development database.

`make integration-test` uses the separate `openacts_test` database in that same
PostgreSQL container. It does not replace the active development projection.

## Reusable-code catalog

No shared utilities yet.
