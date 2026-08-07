# OpenActs pipeline

The pipeline is a local command-line project. It owns its Python dependencies
and writes source binaries only to the gitignored content-addressed cache.

## Setup

From the repository root:

```sh
make setup
```

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

The command validates the extraction and reports the request strategy. Every
model pass receives all raw `pages[].text` in one string, marked with PDF page
numbers; it does not receive the extraction JSON or local cache metadata. Draft
Provisions use the canonical `node_type` vocabulary and canonical text, list,
and table content-block shapes from `schemas/`.

Schedule passes preserve printed labels and support the full legal nesting
`schedule_paragraph` -> `schedule_subparagraph` -> `paragraph` ->
`subparagraph`, with an optional `schedule_part` above the paragraph.

Add `DEEPSEEK_API_KEY` to the gitignored `pipeline/.env`, then explicitly run:

```sh
make structure-execute EXTRACTION=source-cache/extractions/<run>.json
```

The stage uses `deepseek-v4-pro` through the direct DeepSeek API with typed tool
output, the model's 384,000-token maximum output budget, and up to two bounded
output-correction retries. Thinking mode is disabled because DeepSeek does not
allow the required structured-output tool choice while thinking is enabled.
It discovers the document units, then reconstructs complete front matter, each
operative Part or unparted body, and each Schedule. Authentication sheets,
assent signatures, certification tables, and explanatory back matter are outside
the corpus and are not sent through a separate structuring pass.
Focused passes return the exact wording directly owned by every Provision,
including structured lists and tables. Nested children preserve repeated legal
labels under their exact parents; the pipeline then generates draft IDs, parent
IDs, sibling order, block IDs, Source spans, and
`machine_extracted` fidelity. The model, DeepSeek base URL, and request timeout
can be overridden with `OPENACTS_PRIMARY_MODEL`,
`OPENACTS_DEEPSEEK_BASE_URL`, and `OPENACTS_STRUCTURE_TIMEOUT_SECONDS`.

Every valid pass is checkpointed atomically under `source-cache/structure-work/`.
Running the same source, model, and prompt again reuses those checkpoints and
continues from the first unfinished pass. Invalid or source-unrecoverable model
output fails the run instead of becoming a warning-filled success artifact.

The completed draft is written atomically under `source-cache/structures/` and
records each pass, target, checkpoint status, model, usage, and latency. Its
`provisions` contain the complete hierarchy and content blocks. Permanent
corpus IDs are deliberately assigned later; this stage never edits `corpus/`.

## Build and review a corpus candidate

Create one schema-valid Act record containing the human-authored metadata and
the authoritative `source_id`, then materialize the structured draft:

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

Schedules receive order-based permanent IDs (`schedule-1`, `schedule-2`, ...)
regardless of whether the printed label says `SCHEDULE`, `FIRST SCHEDULE`, or an
equivalent ordinal. The exact printed label remains in `display_label`.

Review and correct those candidate files against the PDF. Change each checked
Provision's `text_fidelity` to `single_reviewed`, `double_reviewed`, or
`source_conflict`. Then validate the candidate without writing:

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
