# 0022 — Readable Act-scoped Provision routes

**Status:** Accepted
**Date:** 20 August 2026

## Context

Three documents specify the public URL shape and they do not agree.

[`docs/core/idea.md`](../core/idea.md) §2.2 and [`docs/core/plan.md`](../core/plan.md)
§7.1 specify citation-shaped routes for people alongside identity routes for
machines: `/acts/nigeria-data-protection-act-2023/section/25` with
`/id/ng-federal-act-2023-37/section-25` beside it, plus a dated
`/as-at/<date>/` route. [`docs/frontend-spec.md`](../frontend-spec.md) §3
specifies `/acts/[actId]` and `/provisions/[provisionId]` using opaque
identifiers, and states that the frontend does not derive legal meaning from an
ID.

Two parts of the older specification are already void. Decisions
[0017](0017-as-enacted-instruments-no-amendment-engine.md) and
[0018](0018-direct-act-provision-corpus.md) removed the Version record, so the
dated `/as-at/<date>/` route has nothing to address. The API exposes no slug,
title, or label lookup: `ACT_ID_PATTERN` and `PROVISION_ID_PATTERN` in
`api/src/openacts_api/routes.py` accept canonical identifiers only, and
`schemas/act.schema.json` records `aliases` but no slug. A title-slug route
therefore requires a new canonical field and a new endpoint.

The remaining disagreement is real. `/provisions/[provisionId]` produces
`/provisions/ng-federal-act-1999-constitution%3Asection-1.subsection-1`. Google's
[URL structure guidance](https://developers.google.com/search/docs/crawling-indexing/url-structure)
says to "use readable words rather than long ID numbers in your URLs" and
requires reserved characters to be percent encoded. That URL is the pattern the
guidance names as the one to avoid, and its percent-encoded colon is not
quotable in a brief, an email, or a footnote.

`idea.md` §2.2 argues this in ranking terms. That overstates it: Google's own
documentation makes no ranking claim for words in URLs. The durable argument is
the other one `idea.md` gives — a URL a reader can recognise as a citation they
could quote. For a product whose purpose is verifiable, shareable statutory
text, that is the property that matters.

A canonical Provision identifier is already `<act_id>:<structural-path>`. Across
all 3,134 Provisions in the corpus today, every identifier contains exactly one
colon, and every path after that colon consists only of RFC 3986 unreserved
characters, so it needs no percent-encoding at all.

## Decision

The canonical public Provision route is Act-scoped and readable:

- Act: `/acts/ng-federal-act-1999-constitution`
- Provision: `/acts/ng-federal-act-1999-constitution/section-1.subsection-1`
- Source: `/sources/sha256:<digest>`

The frontend splits a canonical `provision_id` on its first colon to build the
route and rejoins the two segments with a colon to call the API. It performs no
other interpretation: it does not parse the path, order it, compare it, or infer
hierarchy, wording, or legal meaning from it. A pair that does not recombine
into a Provision in the active release fails at the API as `400 invalid_request`
or `404 provision_not_found` and renders the application not-found page.

`/provisions/<provision_id>` remains as the identity route and permanently
redirects to the canonical Act-scoped route. It is the address to use when only
an opaque identifier is in hand, and it keeps previously shared links working.

Act routes continue to use `act_id`. A title-derived slug such as
`/acts/nigeria-data-protection-act-2023` is deferred, not rejected. Adopting one
requires a canonical slug field, a resolution endpoint, and an alias rule for
retitled Acts; it needs its own decision, triggered by evidence that `act_id` is
inadequate rather than by preference.

Every Provision page declares a canonical link to its Act-scoped route, so the
identity route never competes with it for indexing.

## Why

This satisfies the readable-route requirement in `plan.md` §7.1 and the
quotable-citation argument in `idea.md` §2.2 with no backend change, no new
canonical field, and no second identifier system. The hierarchy is visible in
the path, nothing is percent-encoded, and the reader can see which Act a link
belongs to before following it.

The cost is that the frontend now treats a Provision identifier as having one
known separator rather than as fully opaque, which narrows `frontend-spec.md`
§3. That is bounded and enforced elsewhere: the separator is already guaranteed
by `PROVISION_ID_PATTERN`, the API validates every recomposed identifier before
answering, and a wrong pair is indistinguishable from a missing Provision. The
alternative — publishing every Provision at a percent-encoded identifier URL —
trades a quotable public address for a purity the corpus model does not require.

Deferring the title slug keeps this decision to what today's contract already
supports. `act_id` is words rather than digits, which is what the readability
guidance actually asks for.

## Consequences

`docs/frontend-spec.md` §3 must be updated: the route table gains the Act-scoped
Provision route and the redirecting identity route. `plan.md` §7.1 remains
historically accurate about intent but no longer describes the routes to build,
and its dated-Version route stays void under decisions 0017 and 0018.
