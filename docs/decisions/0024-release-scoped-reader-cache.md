# 0024 — Release-scoped reader cache

**Status:** Accepted
**Date:** 20 August 2026

## Context

[`docs/frontend-spec.md`](../frontend-spec.md) §2 carries two rules that together
prevent the reader from caching anything:

- "The frontend has no Route Handlers, Server Actions, corpus files, database
  access, or proxy endpoints."
- "Initial reader GET requests use uncached fetching so an explicitly activated
  corpus release cannot remain hidden behind an uncoordinated frontend cache."

Every reader page therefore calls the API on every request. Decision
[0008](0008-vercel-frontend-workspace-api.md) puts the frontend on Vercel and the
API on the Workspace VPS, so each of those calls crosses the internet. Bounding
the outline query removed most of the *bytes* — a Provision page fell from
808 KB to 6 KB — but not the round trip.

The corpus does not change between releases. Decisions
[0014](0014-change-driven-corpus-releases.md) and
[0021](0021-postgresql-read-projection.md) make a release immutable and its
activation an explicit, atomic operation: `make projection-execute`. Nothing
about a published Act changes until someone runs it.

Measured on a production build: with corpus reads cached, a Provision page
served in 32 ms instead of 140 ms, and continued serving complete legal text
with the API process killed outright.

## Decision

Corpus reads are cached, and the cache is invalidated by release activation.

Every corpus fetch is tagged. All of them carry `corpus`; Act-scoped reads also
carry `act:<act_id>`, so finer invalidation is available later without another
decision. Nothing expires on a timer, because nothing changes on a timer.

`POST /api/revalidate` on the frontend purges tags. It authenticates with a
shared secret compared in constant time, accepts a list of tags, and returns
what it purged. **It reads no corpus data, proxies nothing, and returns no
legal text.** That boundary is the decision: this endpoint exists to forget, not
to serve.

`openacts-projection --execute` calls it after activation commits. A failure to
reach it is reported as a failure of the activation command, not swallowed — a
release that is live in the database but invisible in the reader is exactly the
state §2 was written to prevent, and it must be loud.

`await connection()` is removed from the reader routes. It was there to keep
`next build` from prerendering against an unavailable API; measurement shows the
build stays safe without it, because routes with dynamic parameters and no
`generateStaticParams` render on demand rather than at build time.

## Why

Neither ban in §2 has a reason that covers this.

The Route Handler ban comes from decision
[0007](0007-nextjs-tailwind-frontend.md), which states its purpose: "FastAPI
remains the only corpus backend. Next.js Route Handlers and Server Actions do
not duplicate, proxy, or mutate corpus operations." A cache-purge endpoint
duplicates no corpus operation, proxies nothing, and mutates nothing. It never
touches the corpus at all.

The caching ban exists so an activated release "cannot remain hidden behind an
**uncoordinated** frontend cache." A cache that activation itself clears is
coordinated. The word was doing the work all along.

Two alternatives were rejected. **Rebuilding the frontend on activation** would
serve pages statically from the edge, but it makes a corpus release trigger an
application deploy, and decisions 0014 and 0008 deliberately keep those
independent and independently rollbackable; it also stops scaling past beta,
at roughly 15,000 Provision pages for ten Acts. **Keying the cache on a
per-request `GET /v1/meta`** breaks no rule and needs no decision, but keeps one
API round trip on every page view, which is the cost this decision exists to
remove.

## Consequences

`docs/frontend-spec.md` §2 is amended on both counts: it gains the cache rule
tied to activation, and it names the revalidation endpoint as the single
permitted Route Handler, with the prohibition on serving corpus data restated so
the exception cannot widen.

Deployment gains one required secret shared between the projection tool and the
frontend. Without it the endpoint refuses every request and the reader will
serve a stale release after activation — so it is a deployment prerequisite,
not an optional hardening step.

The reader now depends on activation completing its purge. That dependency is
the price of not calling the API on every request, and it is why the purge
failing must fail the command that caused it.
