# 0023 — Reader visual system

**Status:** Accepted
**Date:** 20 August 2026

## Context

[`docs/frontend-spec.md`](../frontend-spec.md) §4 states a visual direction — "a
modern public reference instrument rather than a government facsimile or generic
application dashboard", restrained Nigerian green as functional accent, a
provenance rail separated from the wording it qualifies, legal wording with the
strongest visual priority. [`docs/core/plan.md`](../core/plan.md) §11.2 adds one
prohibition: "Avoid surrounding statutory text with dashboard cards or
model-generated summaries."

Neither document specifies a system. Typefaces, scale, spacing, and grouping
devices exist only as accumulated choices in `web/src/app/globals.css` and in
each page's Tailwind classes, so there is nothing to review a design against and
nothing to keep pages consistent as routes are added.

The built reader currently contradicts both statements. The Act index is a card
grid; `text_kind`, `status`, and `checked_through_date` render as three bordered
pills; bordered boxes are the default grouping device on every page. That is the
dashboard vocabulary §11.2 names.

Decision [0007](0007-nextjs-tailwind-frontend.md) constrains the answer: Tailwind
with a small project theme, semantic accessible HTML underneath, and no component
library or second CSS architecture.

## Decision

### Type roles

Three faces, one role each. Legal wording is the only content set in the reading
face, which is what gives it visual priority.

| Role | Face | Used for |
|---|---|---|
| Reading | Newsreader | Statutory wording, Act and Provision titles, section headings |
| Interface | Public Sans | Navigation, controls, labels, prose that is not law |
| Identifier | Roboto Mono | Provision and Source identifiers, digests, release tags, page numbers, the provenance rail |

Public Sans is retained. It is the United States Web Design System typeface,
maintained by that design system and derived from Libre Franklin, and it is
built and tested for public-sector text at small sizes — a settled, accessible
standard rather than an untested preference. Roboto Mono is the USWDS monospace
token and is adopted with it, which makes the identifier role a deliberate
choice instead of the ad-hoc stock `font-mono` currently used for revisions and
digests.

This adopts two USWDS typefaces, not USWDS itself. Decision 0007 rules out a
component library or a second CSS architecture and that stands: no USWDS
package, stylesheet, component, grid, or spacing scale enters the project.

§4's objection to a government facsimile is answered by layout rather than by
avoiding a typeface. What makes the current reader look institutional is the
cards, pills, and boxes that the rest of this decision removes.

Scale, fixed so pages stay consistent:

- statutory wording `1.1875rem` at `1.75` line height, measure capped at `66ch`;
- page title `clamp(2rem, 1.5rem + 2vw, 2.75rem)`;
- section heading `1.5rem`;
- interface text `0.875rem`;
- identifier text `0.75rem`, tabular figures.

### Colour

The eight existing tokens in `globals.css` are sufficient and unchanged. No token
is added. `--action-strong`, currently defined but unused, becomes the colour of
hanging Provision labels. Statutory wording is always `--ink`; nothing else on a
page may be.

### Layout: the marginal rail

The signature device is borrowed from the statute page itself, where the label
sits outside the text block:

```
 label rail │ reading column (66ch)              │ provenance rail
────────────┼────────────────────────────────────┼──────────────────
        1.  │ Supremacy of the Constitution      │ CONSOLIDATED
            │                                    │ status unknown
       (1)  │ This Constitution is supreme and   │ single_reviewed
            │ its provisions shall have binding  │
            │ force on all authorities and       │ SOURCE
            │ persons throughout the Federal     │ PLAC 2021 reprint
            │ Republic of Nigeria.               │ page 23
```

The left rail carries identity: the hanging Provision label, and the year on the
Act index. The right rail carries provenance, in the identifier face, which is
how §4's requirement that provenance stay "visibly separate from the wording
they qualify" is met without a box. One vertical hairline marks the reading
column's left edge; it is the only persistent rule on the page.

Below `48rem` the layout is one ordered column: labels become inline prefixes on
the wording they introduce, and the provenance rail moves above the wording as a
compact strip. Nothing is removed at any width.

### Prohibited

- Cards or bordered containers around statutory text.
- Pill or badge treatments for `text_kind`, `status`, or `text_fidelity`. These
  are three independent claims and render as three separate lines of identifier
  text, never as one combined signal.
- A border as the default grouping device. Whitespace and the rails group;
  a rule is used only where it marks a real boundary.
- Status conveyed by colour alone, at any width.
- Motion other than the placeholder shown while streamed content is pending, and
  that is suppressed under `prefers-reduced-motion`.

### Floor

WCAG 2.2 AA. Visible focus using the existing `--focus` ring, interactive targets
at least 44 pixels, reflow at 320 pixels without two-dimensional scrolling except
in genuine tables, and legibility at 200 percent zoom.

## Why

Recording the system makes a design reviewable in the same way every other
choice in this repository is reviewable, and gives later routes something to
conform to rather than re-deriving spacing and hierarchy per page.

The rail is the one deliberate risk. It comes from the vernacular of the subject
— a statute sets its labels in the margin — rather than from a web template, and
it resolves §4's provenance requirement structurally instead of decoratively.
Spending the boldness there is what allows everything around it to be quiet, and
removing the cards and pills is what §11.2 asks for directly.

Keeping the palette unchanged holds decision 0007's "small project theme" and
keeps the diff to layout and type, where the actual problem is.

## Least certain

The reading face. USWDS pairs Public Sans with Merriweather, so adopting that
serif too would make the whole stack consistent with the standard. Newsreader is
kept because it is already in place, sits lighter on the page at a 66ch measure,
and the reading role is the one place a distinct editorial voice serves the
product rather than working against it. Switching to Merriweather is a one-line
change and affects nothing else in this decision.

## Consequences

`docs/frontend-spec.md` §4 gains a pointer to this decision, as §2 already points
to decision 0007. `web/src/app/layout.tsx` adds Roboto Mono beside the two
families already loaded, `globals.css` gains one font variable, and every
existing page loses its cards and pills. The third family adds one font request
to the initial load and must be checked against the LCP budget in `plan.md`
§11.5.
