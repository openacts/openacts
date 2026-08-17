# OpenActs pipeline

The pipeline is a local command-line project. It owns its Python dependencies
and writes source binaries only to the gitignored content-addressed cache.

## Setup

From the repository root:

```sh
make setup
```

## Preview a corpus projection

Set the projection database URL in the gitignored `pipeline/.env`:

```text
OPENACTS_PROJECTION_DATABASE_URL=postgresql://<role>:<password>@<host>/<database>
```

After applying `api/sql/001_projection.sql` to that database, preview an exact
tagged corpus release:

```sh
make projection RELEASE=corpus-v0.0.0
```

The command validates the corpus and schemas from the tag's exact commit, builds
the deterministic projection rows, and inspects PostgreSQL inside a read-only
transaction. It reports `import_and_activate`, `activate_existing`, `noop`, or
`blocked`, along with release counts, database pointers, warnings, and blockers.
It does not import records, activate a release, edit the corpus, or persist an
artifact. `corpus-v0.0.0` is a bootstrap release for exercising this tooling;
it is not eligible for production activation.

## Acquire a source

Create a local request such as `source-cache/requests/ndpa.json`:

```json
{
  "url": "https://publisher.example/act.pdf",
  "provider_name": "Document provider",
  "document_title": "Act title",
  "document_publisher": "Document publisher",
  "source_class": "official_agency_copy"
}
```

Optional fields are `language` (default `eng`), `publication`,
`redistribution`, `location_notes`, and `document_notes`.

Acquisition is a dry-run by default and performs no network access or writes:

```sh
make acquire REQUEST=source-cache/requests/ndpa.json
```

The explicit execution target downloads and validates the source:

```sh
make acquire-execute REQUEST=source-cache/requests/ndpa.json
```

A valid PDF is stored at
`source-cache/sha256/<prefix>/<full-digest>.pdf`. Each executed run writes one
JSON receipt under `source-cache/runs/`; the receipt contains a Source candidate
validated against the canonical schema. Acquisition never edits
`corpus/sources.jsonl`.

## Classify document quality

Pass a successful acquisition receipt to the local classifier:

```sh
make classify RECEIPT=source-cache/runs/<run>.json
```

The classifier verifies the cached PDF against the receipt, then measures each
page's text amount, hidden text mode, image coverage, malformed characters, and
content-stream size with `pypdf`. It proposes one page route (`extract`, `ocr`,
`skip`, or `review`) and one document route (`extract`, `ocr`, `hybrid`, or
`manual_review`). The versioned report under `source-cache/classifications/`
includes the thresholds and reason codes behind those proposals.

Classification does not use the network, save extracted text, or edit the
corpus. It is deliberately conservative: the PDF format cannot prove whether
all extractable text was authored digitally or added by OCR, so the report also
selects pages for human review.

## Extract classified pages

Install the pinned PaddleOCR dependencies and models once. The preview is
offline; execution downloads only missing models into the ignored local cache:

```sh
make ocr-setup
make ocr-setup-execute
```

Then pass any successful `extract`, `ocr`, or `hybrid` classification report:

```sh
make extract CLASSIFICATION=source-cache/classifications/<run>.json
```

The extractor rechecks the cached PDF's digest, size, and page count. It uses
`pypdf` for `extract` pages, PaddleOCR for `ocr` pages in bounded batches, and
does no work for `skip` pages. Every PDF page still has one ordered output
record; skipped pages carry empty text. OCR page details are checkpointed under
`source-cache/extraction-work/`, and the complete merged artifact is written
atomically under `source-cache/extractions/`.

Extraction is offline and never downloads models, removes headers, normalizes
text, infers legal structure, or edits the corpus. A `manual_review` document
route still stops explicitly; review is not silently replaced with OCR.

## Structure extracted text

Preview the model requests without loading credentials, using the network, or
writing an artifact:

```sh
make structure EXTRACTION=source-cache/extractions/<run>.json
```

The command validates the extraction and reports the agent strategy. The
planner receives raw `pages[].text` marked with PDF page numbers and identifies
the inclusive legal range plus bounded front-matter, Chapter, top-level Part,
unparted-body, and Schedule units. It does not receive extraction cache
metadata. Draft Provisions use the canonical `node_type` vocabulary and
canonical text, list, and table content-block shapes from `schemas/`.

Schedule structures preserve printed labels and support the full legal nesting
`schedule_paragraph` -> `schedule_subparagraph` -> `paragraph` ->
`subparagraph`, with an optional `schedule_part` above the paragraph.

Add `DEEPSEEK_API_KEY` to the gitignored `pipeline/.env`, then explicitly run:

```sh
make structure-execute EXTRACTION=source-cache/extractions/<run>.json
```

The stage uses PydanticAI agents inside a typed Pydantic Graph workflow and pins
one configured model for the run. `deepseek-v4-pro` is the default through the
direct DeepSeek API. Thinking mode is disabled because DeepSeek does not allow
the required structured-output tool choice while thinking is enabled. Every
planner and worker receives its complete assigned source text directly; model
conversations cannot loop through redundant source-reading tool calls.

After planning, unit workers run concurrently and reconstruct the exact wording
directly owned by every Provision, including structured lists and tables.
Chapters own their Parts when printed; Acts with top-level Parts and unparted
Acts keep their actual source hierarchy. An unnumbered proviso remains an
ordered text block on the provision it qualifies rather than becoming an
anonymous Provision.

A deterministic source-claim ledger then aligns every emitted label, heading,
and content block to normalized alphanumeric characters in the planned
operative source. It separately checks structural markers and accounts
explicitly for printed page numbers, recurring headers, and source-identified
editorial cross-references or alteration notes. Missing source characters,
missing or extra addressable markers, overlapping or duplicate claims,
unsupported wording, invalid hierarchy, and section-order gaps block
completion. The critic either requests bounded JSON-Patch repairs for affected
units or replans the document. Patches apply to copies, pass the ordinary unit
validator, and replace the retained draft only when the complete audit improves
without adding issues elsewhere. Exhausted repairs never produce a
plausible-looking success. Punctuation fidelity remains part of the required
source review before promotion.

Plans, units, rejected drafts, repairs, the current agent state, and audit
reports are checkpointed atomically under `source-cache/structure-work/`.
Running the same source, model, and prompt again revalidates and reuses completed
checkpoints. Repairs use fresh model conversations so a rejected legal tree is
not replayed into the next request context. Every audit also writes the selected
unit drafts and report to `candidate.json`; failed candidates remain inspectable
there but cannot enter `source-cache/structures/`.

During execution the CLI writes one JSON progress event per line to stderr for
planning, unit starts and completions, audits, retries, repairs, and final
materialization. Stdout remains reserved for the final result JSON, so scripts
can capture it independently from live progress.

The model, DeepSeek base URL, request timeout, worker concurrency, repair budget,
and total run token budget can be configured with `OPENACTS_PRIMARY_MODEL`,
`OPENACTS_DEEPSEEK_BASE_URL`, `OPENACTS_STRUCTURE_TIMEOUT_SECONDS`,
`OPENACTS_STRUCTURE_CONCURRENCY`, `OPENACTS_STRUCTURE_MAX_REPAIR_ROUNDS`, and
`OPENACTS_STRUCTURE_MAX_TOTAL_TOKENS`.

The completed draft is written atomically under `source-cache/structures/` and
records the accepted plan, audit evidence and exclusions, each agent pass,
checkpoint status, model usage, and latency. Its `provisions` contain the
complete hierarchy and content blocks. Permanent corpus IDs are deliberately
assigned later; this stage never edits `corpus/`.

## Build and review a corpus candidate

Create one schema-valid Act record containing the human-authored metadata and
the authoritative `source_id`, then materialize the structured draft. Set
`text_kind` to `consolidated` when that Source presents consolidated wording;
the Source relation and editorial notes must state its boundary and must not
imply that OpenActs independently produced or verified the consolidation:

```sh
make candidate \
  STRUCTURE=source-cache/structures/<run>.json \
  ACT=source-cache/requests/<act>.json
```

This local, deterministic step traces the recorded pipeline inputs back to the
acquisition receipt, verifies the cached PDF, assigns proposed permanent
Provision IDs, and writes the exact corpus layout under
`source-cache/corpus-candidates/`. It preserves the structured wording and
starts every Provision at `machine_extracted`; `citations.jsonl` starts empty.
It never edits `corpus/`.

The candidate root also contains `candidate.json`, a deterministic integrity
manifest recording the Act and Source IDs, originating structure artifact,
Provision count, and ordered Provision-ID digest. Review and promotion reject a
candidate if records were added, removed, reordered, or renamed. A structural
correction therefore belongs in the structure artifact followed by candidate
regeneration; ordinary wording and fidelity corrections remain review edits.

Schedules receive order-based permanent IDs (`schedule-1`, `schedule-2`, ...)
regardless of whether the printed label says `SCHEDULE`, `FIRST SCHEDULE`, or an
equivalent ordinal. The exact printed label remains in `display_label`.

Review and correct the candidate against the PDF. After checking every
remaining machine-extracted Provision once, preview the whole-candidate fidelity
change without writing:

```sh
make review \
  CANDIDATE=source-cache/corpus-candidates/<candidate> \
  FIDELITY=single_reviewed
```

The command reports the current and resulting fidelity counts. It records the
reviewer's assertion; it does not perform or infer the review. Apply the change
atomically only after checking that preview:

```sh
make review-execute \
  CANDIDATE=source-cache/corpus-candidates/<candidate> \
  FIDELITY=single_reviewed
```

The execute form changes only `machine_extracted` to `single_reviewed`, leaves
all other record fields and fidelity values untouched, and revalidates candidate
integrity after writing. Per-Provision `double_reviewed` and `source_conflict`
decisions remain explicit review edits. Then validate the candidate without
writing to the corpus:

```sh
make promote CANDIDATE=source-cache/corpus-candidates/<candidate>
```

Promotion is blocked while any Provision remains `machine_extracted`. Once the
candidate is reviewed and the dry run reports `ready`, explicitly write it:

```sh
make promote-execute CANDIDATE=source-cache/corpus-candidates/<candidate>
```

The promotion revalidates every schema and cross-record relationship, rechecks
the cached PDF, merges the immutable Source by ID, and creates a previously
absent Act directory. Updating an existing authored Act remains a separate
reviewed change rather than an overwrite operation.
