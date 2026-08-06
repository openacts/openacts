# OpenActs canonical data contract

**Contract version:** `0.1.0`
**Status:** Alpha

This document defines the authored OpenActs corpus. It is the human-readable
contract; the JSON Schemas in [`/schemas`](../schemas/) are its machine-readable
counterpart. If prose and schema disagree, the conflict must be resolved before
corpus data is merged. Neither silently overrides the other.

## 1. Boundary

The canonical corpus has four records: **Act**, **Provision**, **Source**, and
**Citation**. Together they let a reader or program:

- find an Act, including the Constitution, and read its enacted text;
- traverse its hierarchy in legal reading order;
- trace every transcribed passage to exact Source pages;
- follow resolved cross-references between Acts and Provisions; and
- reproduce an immutable corpus release.

OpenActs publishes instruments as enacted. An Amendment Act is another Act in
the corpus; Citations may link its wording to the Act or Provision it names.
The alpha does not author amendment operations, apply amendments, generate
consolidated text, or maintain a separate Version record. Corpus releases carry
transcription and metadata corrections.

The canonical model is native to OpenActs. It is not Akoma Ntoso, ELI, a generic
knowledge graph, a database schema, or a frontend response model. An adapter may
be generated later for a real consumer; no external standard controls the data.

The contract does not define extraction jobs, API payloads, search indexes, a
database layout, or a public PDF archive. Commercial legal-provider data is not
a corpus source. Source PDFs used during transcription remain in the gitignored
local content-addressed cache described by decision 0009.

## 2. Authored layout

Sources are corpus-global; each instrument occupies one directory:

```text
corpus/
├── sources.jsonl
└── ng/federal/acts/2023/37/
    ├── act.json
    ├── provisions.jsonl
    └── citations.jsonl
```

| Record | Authored file | Stable key | Main outgoing links |
|---|---|---|---|
| Act | `act.json` | `act_id` | Sources |
| Provision | `provisions.jsonl` | `provision_id` | parent Provision and Sources |
| Source | `corpus/sources.jsonl` | `source_id` | publisher and retrieval locations |
| Citation | `citations.jsonl` | `citation_id` | exact source location and resolved target |

`act.json` is indented JSON. Each JSONL line is one complete record, not a
fragment. `sources.jsonl` is sorted by `source_id`; Provisions are stored in
legal reading order. Citation sequences are assigned once and remain stable.
An empty `citations.jsonl` is valid when no cross-references have been resolved.
Unresolved references remain ordinary Provision text until a target is known.

## 3. Identity and releases

IDs are stored, stable, readable OpenActs identifiers. They are never recycled.

```text
act_id        ng-federal-act-2023-37
act_id        ng-federal-act-1999-constitution
provision_id  ng-federal-act-2023-37:section-25.subsection-2.paragraph-a
source_id     sha256:<64 lowercase hexadecimal characters>
citation_id   citation:ng-federal-act-2023-37:<six-digit-sequence>
```

For a numbered Act, the last Act ID component is its number. An unnumbered Act
uses a stable identifying slug, such as `constitution`; that slug identifies
the instrument and does not create a record subtype.

The Provision ID contains its owning Act and a stable structural name. A later
corpus correction does not change it. When two distinct Provisions would
otherwise receive the same ID, the later assignment receives a stored collision
suffix such as `~2`; the suffix is never removed or recomputed.

The six-digit Citation sequence is assigned once within its source Act. It
expresses identity, not current file order, and is not renumbered after a
Citation is removed or moved.

Legal dates belong to the Act. `corpus-vX.Y.Z` identifies an immutable published
dataset, while an application commit identifies deployed software. Fixing a
transcription creates a new corpus release; it does not invent a legal Version.

## 4. Records

### Act

An Act is the stable discovery and ownership record for one enacted legal
instrument. The Constitution uses this same record and has no subtype, separate
schema, or separate frontend model. This is an OpenActs corpus boundary, not a
claim that every instrument has the same formal legal classification.
The enacted document's language belongs to its authoritative Source rather than
being duplicated on the Act. A later multilingual corpus requires an explicit
Edition decision, as recorded in decision 0018.

An unknown date is explicit: `date` is `null` and `null_reason` states why. An
empty string never means unknown. Act status is discovery metadata, not a claim
that OpenActs has consolidated later amendments into the displayed text.

`status` is a closed enum: `in_force`, `repealed`, `spent`,
`not_yet_commenced`, `mixed`, or `unknown`. Any value other than `unknown`
requires a non-null `checked_through_date` and at least one supporting Source.

`source_refs` records how exact Source files support the enacted text. Exactly
one is the `authoritative_text` used for transcription. Other Sources may be a
certified copy, official mirror, comparison copy, or metadata evidence.

| Fields | Meaning |
|---|---|
| `schema_version`, `record_type`, `act_id` | Contract discriminator and permanent instrument identity. |
| `jurisdiction`, `country_code` | Legal authority, not the document host. |
| `titles`, `year`, `number`, `citation` | Human discovery and citation metadata. `number` is `null` when the instrument has no conventional Act number. |
| `dates` | Assent, publication, commencement, and repeal claims with evidence or a null reason. |
| `aliases` | Known lookup names. |
| `status`, `checked_through_date`, `status_source_ids` | Instrument-level status, research boundary, and evidence. |
| `source_refs` | Sources and their Act-specific evidential roles. |
| `editorial_notes` | Factual notes with Source and decision references. |

### Provision

A Provision is one addressable structural node in an Act. Its parent and
one-based integer `order` reconstruct the hierarchy and legal reading order.
Root Provisions have `parent_provision_id: null`. Children, roots, table of
contents, previous/next links, URL paths, and search labels are generated; they
are not competing authored fields.

The top-level record contains only identity, structure, visible source wording,
provenance, and fidelity:

| Fields | Meaning |
|---|---|
| `schema_version`, `record_type`, `provision_id` | Contract discriminator and stable Provision identity. |
| `node_type` | Structural kind such as part, section, paragraph, schedule, table, or form. |
| `display_label`, `heading` | Exact visible label and heading; either may be `null`. |
| `parent_provision_id`, `order` | The authored hierarchy and sibling order. |
| `source_spans` | Provenance for the label, heading, or structural node. |
| `content_blocks` | Ordered text, list, or table content. |
| `text_fidelity` | How closely this Provision has been checked against its Sources. |

The closed `node_type` vocabulary covers document titles, long titles,
arrangements, preambles, enacting formulae, parts, chapters, divisions,
cross-headings, sections, subsections, paragraphs, subparagraphs,
definitions, schedules and their subdivisions, tables, forms, authentication,
and explanatory notes. It expands only when an actual source document exposes a
missing structural kind.

A proviso is preserved according to its printed structure, not classified by a
special node type. Inline wording remains in its text block; a separately
printed unnumbered proviso may be another ordered text block; and a numbered
one uses its actual paragraph or subparagraph level. This avoids imposing one
shape on the different ways Acts express conditions and exceptions.

`display_label` preserves source-facing labels such as `1.`, `(a)`, `PART II`,
or `SCHEDULE`. It is not normalized search data. `heading` preserves heading
wording. Containers may have no content blocks, but every Provision has at least
one Source span.

#### Content blocks

Provision content is an ordered recursive structure:

- a text block stores text, quoted text, a formula, or a signature;
- a list block stores its printed marker style, start value, items, nested
  content, and Source spans; and
- a table block stores a logical grid with provenance.

Every block has a stable `block_id` within its Provision because a Citation
must identify the exact block and character range containing a reference. Every
block also has its own Source spans. Plain text may contain source-significant
line breaks; presentation styling and HTML are not canonical data.

#### Table content blocks

A table block is defined by
[`table.schema.json`](../schemas/table.schema.json). It contains a caption,
declared column count, ordered row groups, notes, optional physical Source
segments, a layout status, and whole-table Source spans. Row groups map to
header, body, and footer groups. Each row contains cells anchored by a one-based
`column_start`.

Each cell records:

- a stable ID within the Provision;
- `header` or `data` role;
- header scope and explicit `header_cell_ids` for complex relationships;
- positive row and column spans;
- whether it is intentionally blank;
- ordered, provenance-backed content blocks; and
- Source spans covering the physical cell.

Cells covered by another cell's span are omitted rather than represented as
fake blanks. A genuinely empty printed cell has `blank: true`, an empty
`content_blocks` array, and its own Source span. A nonblank cell contains at
least one content block.

For a table split across PDF pages, `source_segments` maps logical row IDs to
physical page fragments. Repeated printed headers are listed separately and are
not duplicated in the logical grid. `layout_status` is
`faithfully_reconstructed`, `reconstruction_uncertain`, or `source_conflict`.

An independently labelled table is a Provision with `node_type: "table"`; an
embedded table is only a content block. A form can use supported text, list, and
table blocks. A diagram, seal, or genuinely visual non-grid layout is not
flattened inaccurately; the schema is extended when a real source requires one.

### Source

A Source is one exact byte sequence, identified by its SHA-256 digest. Different
official PDFs of the same Act are different Sources when their bytes differ.
The same bytes obtained from several official URLs are one Source with several
`locations`.

`document_publisher` names the body responsible for the document. Each location
names the provider from which those bytes were retrieved. Publisher and provider
may be the same, but the model does not assume they are.

| Fields | Meaning |
|---|---|
| `schema_version`, `record_type`, `source_id` | Contract discriminator and global byte identity. |
| `document_title`, `document_publisher`, `language` | Facts about the document itself. |
| `source_class` | Gazette, certified copy, official copy or translation, institutional copy, or secondary copy. |
| `publication` | Optional gazette or publication issue metadata. |
| `media_type`, `byte_length`, `page_count`, `text_layer` | Measured PDF properties. |
| `locations` | HTTP or HTTPS retrieval URLs, providers, times, and HTTP metadata. |
| `redistribution` | Recorded permission research; it does not distribute the bytes. |
| `document_notes` | Visible defects, scan properties, or source conflicts. |

### Citation

A Citation points from an exact character range in one content block to a
resolved Act or Provision. The cited wording is derived from that text range;
it is not duplicated in the Citation record. Backlinks and Provision citation
lists are generated and are never authored separately.

An Amendment Act needs no special amendment record: its instruction remains
ordinary Provision text, and a Citation can link the named Act or Provision.

| Fields | Meaning |
|---|---|
| `schema_version`, `record_type`, `citation_id` | Contract discriminator and permanent Citation identity. |
| `source_provision_id`, `source_block_id`, `text_range` | Exact origin in the transcribed text. |
| `target` | Resolved Act and optional Provision; `provision_id` is `null` for an Act-level link. |

## 5. Provenance

`source_spans` join a Provision or content block to one or more Sources. A span
has only:

- `source_id`;
- a one-based PDF page number; and
- an optional printed page label.

Source spans are deliberately page-level. Pixel coordinates, OCR details,
extraction tools, and transformation logs are processing artifacts, not
canonical legal data.

`Citation.text_range` addresses the canonical content block's NFC-normalized
`text`. Offsets count Unicode code points; `start` is inclusive and `end` is
exclusive. The frontend derives the linked wording from that slice.

Multiple spans preserve text assembled across pages or checked against more
than one Source. The canonical record does not describe the extraction process.

## 6. Text fidelity and review

Text fidelity answers only how closely a Provision has been checked against its
stated Sources:

- `machine_extracted`;
- `single_reviewed`;
- `double_reviewed`; or
- `source_conflict`.

Act-level coverage is generated from its Provisions rather than authored twice.
Fidelity does not claim that the enacted text is a current consolidation. Git
commits, pull requests, and repository history provide the review trail;
reviewer workflow metadata is not duplicated in canonical records.

## 7. Canonical serialization

Authored data uses UTF-8, Unicode NFC, LF line endings, and a final newline.
Indented JSON uses two spaces. JSONL uses one compact JSON object per line.
Object keys follow schema order. Arrays preserve legal or editorial order where
that order has meaning; otherwise tooling applies a documented sort.

Canonical files contain no comments, HTML presentation, generated hierarchy or
search fields, database IDs, local cache paths, access tokens, reviewer metadata,
or bracketed placeholder values.

The immutable Git tag and exact commit identify a corpus release. The corpus
does not store a second checksum inside an Act or Provision record.

## 8. Validation boundary

The schemas use JSON Schema Draft 2020-12 and reject unknown properties. They
enforce required fields, types, formats, enums, identifier shapes, and local
conditions such as exactly one authoritative text Source per Act.

A corpus validator must additionally enforce facts involving multiple records:

1. Act, Provision, Citation, and Source IDs are globally unique and every
   reference resolves to the correct record type.
2. Each Provision ID belongs to its containing Act. Provision parents belong to
   the same Act, form no cycles, and yield complete deterministic sibling order.
3. Every nested content ID is unique within its Provision. Table cells neither
   overlap nor exceed `column_count`; spans and header or segment references
   resolve to records of the correct role.
4. Every Source reference resolves, and PDF page references are within the
   Source page count.
5. Every Act `source_refs` entry resolves, including the one
   `authoritative_text` required by the Act schema.
6. Every Citation range is in bounds, its source block exists, and its resolved
   target exists.
7. Each non-null Act date claim has Source evidence. Every known Act status has
   Source evidence and a non-null `checked_through_date`.
8. Authored serialization follows the deterministic rules in section 7.

[`tests/test_contract.py`](../tests/test_contract.py) implements the
cross-record checks that can be exercised without source binaries. Source
acquisition separately verifies that cached bytes match `source_id`,
`byte_length`, and measured PDF properties before a Source record is accepted.

Schema-valid means structurally admissible, not a guarantee that transcription
or metadata is correct. Corpus publication still requires provenance and human
review.

## 9. Contract evolution

Every record carries `schema_version`. During alpha, an incompatible change
advances the schema minor version and the corpus release minor version. After
`corpus-v1.0.0`, incompatible schema or stable-identity changes require a corpus
major release. Generated API, database, search, and export models must identify
the canonical schema and corpus release from which they were built.
