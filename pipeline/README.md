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

## Extract native text

Pass a successful classification report whose document route is `extract`:

```sh
make extract CLASSIFICATION=source-cache/classifications/<run>.json
```

The extractor rechecks the cached PDF's digest, size, and page count, then uses
`pypdf` to preserve one raw text record for every PDF page. The complete,
versioned artifact is written atomically under `source-cache/extractions/`; the
command prints only its path and summary rather than the extracted document.

Extraction does not remove headers, normalize text, infer legal structure, run
OCR, or edit the corpus. A non-native classification route fails explicitly.

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
