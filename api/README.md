# OpenActs API

Read-only FastAPI service for the active PostgreSQL corpus projection. The
authored corpus remains canonical; this service never imports or edits it.

## Run locally

From the repository root:

```sh
cp api/.env.example api/.env
# Set OPENACTS_API_DATABASE_URL in api/.env.
make api-check
make api-run
```

Then inspect `http://127.0.0.1:8000/healthz`, `/readyz`, or `/v1/meta`.

## Reusable-code catalog

No shared utilities yet.
