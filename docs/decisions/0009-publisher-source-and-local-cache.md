# 0009 — Publisher source and local content-addressed cache

**Status:** Accepted; Source ownership and manifest placement superseded by [0016 — Global content-addressed Source manifest](0016-global-content-addressed-sources.md)
**Date:** 5 August 2026
**Updated:** 6 August 2026

## Context

OpenActs needs the exact source document while extracting and checking legal text, but storing PDFs in Git would bloat review history. Creating a separate public archive before an official source has actually disappeared would add storage, rights, and release machinery without serving the alpha reader.

## Decision

Each Source identifies one exact byte sequence. It stores the document publisher, full SHA-256 digest, media type, byte length, page count, source class, document notes, and one or more retrieval locations. Each location records its URL, provider, and retrieval facts. The digest is the `source_id`.

Different official PDFs of the same Act are different Sources when their bytes differ. Identical bytes obtained from several URLs are one Source with several locations. The Act records the role each Source plays, including which one supplies its authoritative text and which are official mirrors, certified copies, comparison copies, or metadata evidence. The public reader can therefore expose every relevant official location without confusing the document publisher with the host that supplied a copy.

Acquisition stores a processing copy under `source-cache/sha256/<prefix>/<full-digest>.<extension>`. The cache is gitignored and its path is derived from the digest rather than stored in the corpus. A later retrieval is accepted as the same Source only when its digest matches; different bytes require a new Source record and review.

OpenActs does not initially publish source PDFs through Git, Git LFS, GitHub Releases, the API, or an object store. An OpenActs-hosted mirror requires a later decision triggered by observed link rot or a demonstrated reproducibility need, and only after redistribution is permitted.

## Why

The manifest and digest make source identity exact, while explicit locations preserve provenance across several genuine official copies. The local cache supports extraction without polluting the repository or product deployment. Deferring a hosted mirror keeps the first system smaller and leaves the storage decision tied to an actual preservation problem.
