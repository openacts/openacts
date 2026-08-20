# OpenActs API contract

**API version:** `v1`
**Status:** Alpha

This document defines the public read contract between the OpenActs FastAPI
service and its consumers. The canonical Act, Provision, Source, and Citation
records remain defined by [`data-model.md`](data-model.md) and its JSON Schemas.
API response models are generated views of one canonical corpus release; they
are not another editable corpus.

If this document and the implementation disagree, the conflict must be resolved before release. None silently overrides
the others.

## 1. Reader boundary

The API serves the known-law reader defined by
[decision 0003](decisions/0003-known-law-lookup-first.md): locate an Act or
Provision by identity, title, citation, or wording; read it in legal order; and
verify it against Source metadata. It is public and read-only.

The API does not edit or promote corpus records, authenticate users, host Source
PDFs, produce legal summaries or answers, expose behavioural analytics, or
guarantee that displayed wording is current beyond the evidence recorded in the
Act and Source. The Next.js frontend consumes this contract directly and does
not add a second corpus backend.

## 2. Versions and release consistency

Application, API, canonical schema, and corpus versions are independent:

- the application revision is the exact deployed Git commit;
- the API version is the URL prefix, initially `/v1`;
- canonical records carry their own `schema_version`; and
- `corpus-vX.Y.Z` identifies an immutable tagged corpus release.

Every request that reads corpus data resolves the active release once before
performing any other corpus query. Every subsequent query is constrained to
that resolved release. Activation during a request cannot produce a response
containing records from different releases.

Every `/v1` success body has this envelope:

```json
{
  "meta": {
    "api_version": "v1",
    "application_revision": "0123456789abcdef0123456789abcdef01234567",
    "corpus_release": "corpus-v0.2.0"
  },
  "data": {}
}
```

`application_revision` is a full Git commit. A successful corpus response has a
non-null `corpus_release`. An error produced before an active release can be
resolved uses `null` for `corpus_release`.

Later examples abbreviate the required `meta` object as `{}` so their endpoint-
specific fields remain visible; it always has the section 2 shape on the wire.

Every `/v1` response also emits `OpenActs-Application-Revision` and, when one
was resolved, `OpenActs-Corpus-Release`. This preserves release identity on a
bodyless `304 Not Modified` response. Browser CORS configuration exposes those
headers and `ETag` to the approved frontend origin.

## 3. Representation conventions

- JSON is UTF-8 with `Content-Type: application/json`.
- Canonical records appear with their canonical field names and values.
- Generated values never alter exact legal text, visible labels, Source spans,
  or Citation character ranges.
- IDs are stable opaque strings. Clients must not parse legal meaning from them
  and must percent-encode them when constructing a path segment.
- A missing optional value is represented by `null` when the response model
  declares it nullable. Empty collections are `[]`, not `null`.
- Lists that express legal structure use canonical legal order. Other ordering
  rules are stated per endpoint and always have a stable ID tie-breaker.
- Plain-text excerpts contain no HTML or highlighting markup.

### Act summary

An Act summary contains:

```json
{
  "act_id": "ng-federal-act-2023-37",
  "official_title": "Nigeria Data Protection Act, 2023",
  "short_title": "Nigeria Data Protection Act",
  "year": 2023,
  "number": "37",
  "citation": "Act No. 37 of 2023",
  "text_kind": "as_enacted",
  "status": "unknown",
  "checked_through_date": null
}
```

`short_title`, `number`, `citation`, and `checked_through_date` may be `null`.
`text_kind` applies the canonical default `as_enacted` when the authored field
is absent. `checked_through_date` preserves the research boundary for any known
status.

### Provision summary

A Provision summary contains:

```json
{
  "provision_id": "ng-federal-act-2023-37:section-25",
  "act_id": "ng-federal-act-2023-37",
  "node_type": "section",
  "display_label": "25.",
  "heading": "Rights of a data subject"
}
```

`display_label` and `heading` may be `null`.

## 4. Health and metadata

### `GET /healthz`

Liveness checks only whether the application process can serve HTTP. It does
not query PostgreSQL and therefore cannot be taken as corpus readiness.

```json
{
  "status": "ok",
  "application_revision": "0123456789abcdef0123456789abcdef01234567"
}
```

It returns `200 OK` and `Cache-Control: no-store`.

### `GET /readyz`

Readiness checks PostgreSQL connectivity, the existence of one active corpus
release, projection compatibility, and supported canonical schema versions.

```json
{
  "status": "ready",
  "application_revision": "0123456789abcdef0123456789abcdef01234567",
  "corpus_release": "corpus-v0.2.0"
}
```

It returns `200 OK` when ready. A failed check returns the typed
`projection_unavailable` `503` response defined in section 8. Both responses
use `Cache-Control: no-store`.

### `GET /v1/meta`

This endpoint returns the complete public release identity:

```json
{
  "meta": {
    "api_version": "v1",
    "application_revision": "0123456789abcdef0123456789abcdef01234567",
    "corpus_release": "corpus-v0.2.0"
  },
  "data": {
    "corpus_commit": "fedcba9876543210fedcba9876543210fedcba98",
    "canonical_schema_versions": ["0.1.0"]
  }
}
```

Schema versions are unique and sorted. This response does not contain an
import timestamp or generated-at timestamp because those values would make an
otherwise immutable representation unstable.

## 5. Acts and contents

### `GET /v1/acts`

Query parameters:

| Name | Default | Constraint |
|---|---:|---:|
| `offset` | `0` | integer at least `0` |
| `limit` | `50` | integer from `1` through `100` |

Acts are ordered by normalized official title, then descending year, then
`act_id`. The response contains:

```json
{
  "meta": {},
  "data": {
    "items": [],
    "pagination": {
      "offset": 0,
      "limit": 50,
      "total": 2
    }
  }
}
```

Each item is an Act summary. An offset beyond the final result returns `200 OK`
with an empty `items` array.

### `GET /v1/acts/{act_id}`

The response contains the unchanged canonical Act record and the unchanged
canonical Source records referenced by that Act:

```json
{
  "meta": {},
  "data": {
    "act": {},
    "sources": []
  }
}
```

Sources are ordered by their first appearance in `act.source_refs` and are
deduplicated by `source_id`. Their retrieval metadata is exposed, but Source
PDF bytes and private cache locations are not.

### `GET /v1/acts/{act_id}/contents`

The response is a compact flat outline in Act-local legal sequence. Each item
contains:

```json
{
  "provision_id": "ng-federal-act-2023-37:section-25",
  "parent_provision_id": "ng-federal-act-2023-37:part-6",
  "node_type": "section",
  "display_label": "25.",
  "heading": "Rights of a data subject",
  "order": 1,
  "sequence": 94,
  "depth": 1,
  "has_content": true,
  "has_children": true
}
```

The endpoint wraps those nodes as:

```json
{
  "meta": {},
  "data": {
    "items": []
  }
}
```

`sequence` is a generated one-based position across the whole Act. `depth` is
zero for a root Provision. The list includes structural nodes without content
because they remain part of the legal hierarchy. It does not duplicate content
blocks.

Two optional query parameters bound the result. Both may be combined, and
omitting both returns every node of the Act.

| Parameter | Rule | Effect |
|---|---|---|
| `parent_provision_id` | a canonical Provision ID | Only that node's direct children |
| `max_depth` | integer, zero or greater | Only nodes at or above that depth |

An unbounded outline is large: the 1999 Constitution returns 2,487 nodes and
about 800 KB. `max_depth=1` returns 87 nodes, and a `parent_provision_id`
returns that node's children alone.

A `parent_provision_id` that is well formed but names no Provision in the Act
returns an empty list rather than `404`; these are filters, not lookups. A
malformed value, or a negative `max_depth`, is `400 invalid_request`. A missing
Act is still `404 act_not_found`.

## 6. Provisions and Sources

### `GET /v1/provisions/{provision_id}`

The response contains everything required to render the requested Provision in
context without a second corpus query:

```json
{
  "meta": {},
  "data": {
    "act": {},
    "provision": {},
    "descendants": [],
    "ancestors": [],
    "navigation": {
      "previous": null,
      "next": null
    },
    "sources": [],
    "citations": []
  }
}
```

- `act` is the owning Act summary.
- `provision` is the unchanged requested canonical Provision.
- `descendants` contains unchanged canonical descendant Provisions in preorder
  legal sequence and excludes the requested Provision itself.
- `ancestors` contains Provision summaries from the root through the immediate
  parent.
- `previous` and `next` are the immediately adjacent Provision summaries in
  the generated Act-local sequence, or `null` at either boundary.
- `sources` contains unchanged canonical Source records referenced by the
  requested Provision or its descendants, deduplicated and ordered by
  `source_id`.
- `citations` contains outgoing Citations originating in the requested
  Provision or its descendants. Each item contains the unchanged canonical
  `citation`, its target Act summary, and its nullable target Provision summary:

```json
{
  "citation": {},
  "target": {
    "act": {},
    "provision": null
  }
}
```

Citations are ordered by source Provision sequence and then `citation_id`.

A container Provision may have no content blocks and a non-empty descendant
list. An Act without resolved Citations returns an empty `citations` array.
Backlinks are not part of v1.

### `GET /v1/sources/{source_id}`

The response contains one unchanged canonical Source record:

```json
{
  "meta": {},
  "data": {
    "source": {}
  }
}
```

This endpoint does not serve, proxy, sign, or redirect to Source PDF bytes. A
private cached PDF being unavailable does not make its already projected Source
metadata unreadable.

## 7. Exact and lexical search

### `POST /v1/search`

Search input is carried only in a JSON body:

```json
{
  "query": "section 25 Nigeria Data Protection Act",
  "act_id": null,
  "limit": 20
}
```

After Unicode NFC normalization and surrounding-whitespace removal, `query`
must contain from 1 through 256 Unicode code points. Internal whitespace may be
collapsed for matching but never changes returned legal text. `act_id` is
optional; when supplied, it must resolve to an Act in the active release.
`limit` defaults to 20 and accepts integers from 1 through 50. Unknown fields
are rejected.

Search resolves and orders candidates in this precedence:

1. exact `act_id` or `provision_id`;
2. exact Act `citation`;
3. exact official or short title;
4. exact Act alias;
5. exact Provision reference within `act_id` or an Act named unambiguously in
   the query; and
6. PostgreSQL lexical matching.

Case, surrounding whitespace, and conventional citation punctuation do not
prevent an otherwise exact title, alias, or citation match. A bare Provision
reference such as `section 25` is not treated as globally exact unless
`act_id` or one unambiguous Act name supplies its scope.

An exact resource appears once even when several exact rules match it. Every
exact result precedes every lexical result. Lexical results are ordered by
descending PostgreSQL rank, then `act_id`, then Act-local Provision sequence.
The internal numeric rank is not an API field.

The response contains:

```json
{
  "meta": {},
  "data": {
    "items": [
      {
        "kind": "provision",
        "match_kind": "exact_provision_reference",
        "act": {},
        "provision": {},
        "breadcrumb": [],
        "excerpt": "A data subject has the right to..."
      }
    ]
  }
}
```

`kind` is `act` or `provision`. `match_kind` is one of `exact_act_id`,
`exact_provision_id`, `exact_act_citation`, `exact_act_title`,
`exact_act_alias`, `exact_provision_reference`, or `lexical`. `provision` is
`null` for an Act result. `breadcrumb` contains Provision summaries from root
to parent and is empty for an Act result. `excerpt` is nullable, plain text,
and at most 320 Unicode code points. It is drawn only from exact canonical
wording.

No match is a successful `200 OK` response with an empty `items` array. Search
responses use `Cache-Control: no-store` and have no `ETag`.

## 8. Errors

The API maps validation, lookup, projection, and unexpected failures into one
typed shape:

```json
{
  "meta": {
    "api_version": "v1",
    "application_revision": "0123456789abcdef0123456789abcdef01234567",
    "corpus_release": "corpus-v0.2.0"
  },
  "error": {
    "code": "provision_not_found",
    "message": "Provision not found.",
    "retryable": false,
    "request_id": "01JEXAMPLE"
  }
}
```

| Status | Code | Retryable | Use |
|---:|---|---|---|
| `400` | `invalid_request` | `false` | Malformed JSON, unknown fields, invalid IDs, or out-of-range input. |
| `404` | `act_not_found` | `false` | A well-formed Act ID is absent from the active release. |
| `404` | `provision_not_found` | `false` | A well-formed Provision ID is absent from the active release. |
| `404` | `source_not_found` | `false` | A well-formed Source ID is absent from the active release. |
| `503` | `projection_unavailable` | `true` | PostgreSQL, the active release, or projection compatibility is unavailable. |
| `500` | `internal_error` | `true` | An unexpected server failure; a wasted retry is safer than declaring an unknown failure permanent. |

FastAPI's default `422` response is replaced by `invalid_request` so callers
never receive two validation vocabularies. Error messages are stable and safe
for display; they never include SQL, driver output, filesystem paths, search
input, canonical text excerpts, or stack traces. Every error response uses
`Cache-Control: no-store`.

## 9. Caching, CORS, and operational privacy

Successful corpus `GET` responses emit a strong `ETag` derived from the
representation, application revision, and corpus release, with:

```text
Cache-Control: public, max-age=0, must-revalidate
```

A matching `If-None-Match` returns `304 Not Modified` without a body and retains
the identity headers from section 2. Health, readiness, search, and error
responses use `Cache-Control: no-store`.

Production browser CORS permits only the configured frontend origin, allows no
credentials, and exposes `ETag`, `OpenActs-Application-Revision`, and
`OpenActs-Corpus-Release`. Development and deliberately approved preview
origins are explicit configuration values; wildcard production origins are not
accepted.

Operational logs contain only the route template, status, latency, request ID,
application revision, and resolved corpus release. They exclude request and
response bodies, raw request paths, query strings, SQL text and parameters,
canonical wording, and search excerpts. An unexpected failure records its
exception class, stack, and safe operation name without logging those excluded
values. No proxy, error reporter, analytics service, or database slow-query log
may capture search bodies or bound search parameters.

## 10. Contract evolution

Additive response fields may be introduced within `/v1`; clients must ignore
unknown response fields. Removing or renaming a field, changing its meaning or
type, or changing an established ordering rule requires `/v2`. Request bodies
continue to reject unknown fields so caller mistakes fail visibly.

Activating another corpus release does not change the API version. Supporting a
new canonical schema version requires an explicit projection mapping and
readiness compatibility check before that release can be activated.

## 11. Deferred work

This contract does not choose the physical SQL schema, migration mechanism,
connection-pool size, ranking expression, dependency versions, Docker layout,
deployment hostname, first corpus release tag, or frontend presentation. Those
are implemented and verified in later phases of
[decision 0021](decisions/0021-postgresql-read-projection.md).

The following remain out of scope for v1: corpus writes, authentication and
accounts, public PDF hosting, bulk export endpoints, Citation backlinks,
behavioural analytics, application caching, Redis, a separate search service,
embeddings, conceptual search, and generated legal explanations or answers.

## 12. Implementation acceptance

The implementation is conformant only when automated tests prove:

1. every endpoint returns the documented envelope and release identity;
2. one response cannot mix records from two corpus releases;
3. hierarchy, descendants, ancestry, legal sequence, and navigation are
   deterministic;
4. exact matches precede lexical matches and duplicate resources are removed;
5. invalid, missing, and unavailable states map to the documented typed errors;
6. ETag revalidation returns `304` with release identity;
7. search inputs and SQL parameters are absent from captured logs; and
8. FastAPI performs no database writes while serving requests.
