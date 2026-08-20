# OpenActs frontend specification

**Status:** Approved for phased implementation  
**Architecture:** [Decision 0007](decisions/0007-nextjs-tailwind-frontend.md)  
**API contract:** [OpenActs API contract](api-contract.md)

## 1. Product job

The frontend is a public reader for a person who knows, or approximately knows,
the Nigerian Act, Provision, citation, title, or wording they need. It must let
that person locate, read, verify, navigate, and share exact legal text.

It is not a legal-advice product, chatbot, corpus editor, unrestricted wiki, or
second backend. It does not summarize or reinterpret legal wording.

## 2. Runtime boundary

The frontend is a Next.js App Router application using strict TypeScript and
Tailwind CSS. FastAPI remains the only corpus backend.

- Server Components fetch public Act, Provision, Source, and release data
  directly from FastAPI.
- Client Components are limited to search, copy actions, and interactions that
  require browser state.
- The frontend has no Route Handlers, Server Actions, corpus files, database
  access, or proxy endpoints.
- Initial reader GET requests use uncached fetching so an explicitly activated
  corpus release cannot remain hidden behind an uncoordinated frontend cache.
- Search calls `POST /v1/search` directly from the browser and never places the
  query in a URL, persistent browser storage, analytics, or application logs.

The public API origin is configured through
`NEXT_PUBLIC_OPENACTS_API_URL`. The frontend revision is supplied through
`OPENACTS_WEB_REVISION` locally and the deployment provider's Git revision in
production.

## 3. Routes

| Route | API dependency | Responsibility |
|---|---|---|
| `/` | `GET /v1/acts`, `POST /v1/search` | Known-law search, available Acts, and release identity |
| `/acts` | `GET /v1/acts` | Paginated Act index |
| `/acts/[actId]` | `GET /v1/acts/{act_id}`, `GET /v1/acts/{act_id}/contents` | Act identity, status, Sources, and top-level contents |
| `/acts/[actId]/[provisionPath]` | `GET /v1/provisions/{provision_id}` | Exact legal text, context, Citations, Sources, and navigation |
| `/provisions/[provisionId]` | — | Identity route; permanently redirects to the Act-scoped Provision route |
| `/sources/[sourceId]` | `GET /v1/sources/{source_id}` | Source provenance and recorded retrieval locations |

Provision routes are Act-scoped and readable under
[Decision 0022](decisions/0022-readable-provision-routes.md). The frontend
splits a canonical `provision_id` on its first colon to build the route and
rejoins the two segments to call the API. That single separator is the only
structure it may assume: it does not parse, order, compare, or derive legal
meaning from the path, and a pair that does not resolve renders the application
not-found page. Every other ID stays opaque and percent-encoded.

Every Provision renders whatever content blocks it carries. A container — a
chapter, part, division, cross-heading, schedule, or schedule part — then lists
its direct children, one level only, instead of rendering the subtree beneath
it. A section, and everything below a section, renders its descendants as
continuous legal text.

The rule follows the record rather than the node type. Containers are usually
wordless, but two schedule parts in the Constitution carry a table directly, and
259 of its sections carry no wording of their own while the subsections beneath
them do.

Act pagination may use URL query parameters. Search text may not.

## 4. Reader presentation

The visual system is fixed by
[Decision 0023](decisions/0023-reader-visual-system.md): three typefaces with one
role each, the eight existing colour tokens unchanged, a marginal rail carrying
identity on the left and provenance on the right, and an explicit prohibition on
cards, badges, and borders as the default grouping device.

The visual direction is a modern public reference instrument rather than a
government facsimile or generic application dashboard.

- Legal wording has the strongest visual priority and a readable line length.
- A restrained Nigerian green is functional accent colour, not decoration.
- A provenance rail keeps Source pages, text fidelity, text kind, status, and
  corpus release visibly separate from the wording they qualify.
- Desktop layouts may place context beside the reading column. Mobile layouts
  use one ordered column without removing context.
- Navigation uses links, content uses semantic headings and landmarks, and
  tables remain real HTML tables.
- Motion is brief, interruptible, and absent where it does not explain a state
  change. Reduced-motion preferences are honored.

The frontend never combines Source authority, transcription fidelity, and
legal currentness into one verified state. It shows `text_kind`, `status`, and
`checked_through_date` explicitly and does not call unknown wording current.

## 5. Legal content rendering

The Provision page renders the requested Provision followed by its descendants
in API order. Containers with no content remain visible when their heading or
descendants carry legal structure.

The renderer must support every canonical content block:

- text, quoted text, formula, and signature blocks;
- recursive lists with exact labels, marker styles, and start values; and
- tables with captions, row groups, header relationships, row and column spans,
  blank cells, nested content, notes, and layout status.

Source-significant line breaks are preserved. Wide tables scroll horizontally
on narrow screens rather than shrinking into unreadability.

Citation links are derived from canonical Unicode code-point ranges. The
renderer must not insert, delete, normalize, or otherwise change the displayed
legal wording while linking a cited range.

## 6. Interaction states

Every data-backed route has loading, success, not-found, unavailable, and
unexpected-error states.

Search additionally has idle, typing, submitting, results, no-results, and
invalid-input states. A newer submission aborts any older in-flight search.
Exact and lexical matches are visibly distinguished without inventing a score.

Typed API errors map to user-facing recovery:

- missing Acts, Provisions, and Sources render the application not-found page;
- `projection_unavailable` explains that the corpus is temporarily unavailable
  and offers retry;
- unexpected failures show a retry action and the safe request ID; and
- invalid search input remains beside the search field.

## 7. Accessibility and privacy

The implementation targets WCAG 2.2 AA.

- Provide a skip link, visible focus, keyboard operation, semantic landmarks,
  correctly associated form labels, and at least 44-pixel touch targets.
- Page titles identify the Act or Provision being read.
- Status and errors are not conveyed by colour alone.
- Search result changes are announced without moving focus unexpectedly.
- Tables preserve caption, scope, and explicit header associations.
- Zoom and text reflow remain usable at 200 percent.

OpenActs adds no behavioural analytics, tracking cookies, session replay,
interest profiles, recent-search persistence, or query-bearing URLs.

## 8. Verification

Tests are added with the implementation slice they protect.

- Unit tests cover API error mapping, path encoding, Citation range
  segmentation, recursive blocks, and table semantics.
- Component tests cover search and copy interactions.
- Browser integration uses the real FastAPI service and PostgreSQL projection
  for search, Act, Provision, Source, navigation, and not-found journeys.
- Automated accessibility checks cover the home, Act, Provision, table, search
  results, and error states.
- A JavaScript-disabled browser check proves that public Act and Provision
  wording is present in returned HTML.
- Visual review covers desktop, small-laptop, and mobile widths, including long
  headings, deep hierarchy, and wide tables.
- Frontend lint, unit tests, TypeScript checks, and production build run through
  `make check` and hosted CI.

## 9. Delivery order

1. Frontend foundation, configuration, local development, checks, and CI.
2. Home, Act index, Act detail, metadata, and contents.
3. Provision renderer, Citations, provenance, and navigation.
4. Private exact and lexical search.
5. Source pages, sharing controls, responsive polish, and accessibility.
6. Real browser integration and visual quality gate.
7. Vercel frontend deployment and Workspace API deployment.

## 10. Out of scope

- Corpus writes, contribution UI, accounts, and authentication.
- Generated summaries, explanations, legal answers, or conceptual search.
- Public Source PDF hosting or a PDF viewer.
- Citation backlinks, saved items, recent searches, and user preferences.
- Behavioural analytics, advertising, and session replay.
- A component library, state library, animation library, or frontend cache
  invalidation system without a demonstrated need.
