# OpenActs web

Next.js reader for the versioned OpenActs API. FastAPI remains the only corpus
backend; this project does not read corpus files or provide API routes.

## Local development

Use Node 22, the version CI runs and the one pinned in `.nvmrc`:

```sh
nvm use
```

This matters for more than parity. npm 11 writes a `package-lock.json` that
npm 10 rejects as out of sync, so running `npm install` on a newer Node will
produce a lock file that fails CI.

Install dependencies once from the repository root:

```sh
npm ci --prefix web
```

Use the root environment and start PostgreSQL, FastAPI, and the frontend:

```sh
make dev
```

The reader runs at `http://localhost:3000` and calls the API configured by
`NEXT_PUBLIC_OPENACTS_API_URL` in the root `.env`.

Run all frontend checks with:

```sh
make web-check
```
