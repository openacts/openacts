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
