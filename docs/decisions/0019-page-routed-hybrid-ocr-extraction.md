# 0019 - Page-routed hybrid OCR extraction

**Status:** Accepted

## Context

An Act can mix useful embedded text, scanned pages, and intentional blank pages.
Rejecting the entire document wastes valid native text, while OCRing every page
is slower and can degrade text that was already extractable.

## Decision

The existing extraction command follows the classifier's page routes. It uses
`pypdf` for `extract` pages, pinned PaddleOCR models for `ocr` pages, and emits
an empty record without processing for `skip` pages. Sparse-text review pages
retain native text; hard review conditions stop the document instead of being
silently treated as OCR.

OCR runs offline in bounded page batches with one loaded model instance. Its
models live in the ignored repository cache, and each completed OCR page has a
reusable checkpoint. Extraction merges all page results in PDF order into the
same single versioned artifact consumed by structuring.

## Consequences

Callers keep one `make extract` workflow and one downstream artifact shape.
OCR setup is an explicit networked action, model and engine versions are
recorded, and skipped pages remain visible for page-number fidelity. The local
OCR environment and models are large but do not enter Git or canonical corpus
records.
