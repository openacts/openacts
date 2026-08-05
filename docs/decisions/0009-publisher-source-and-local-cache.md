# 0009 — Publisher source and local content-addressed cache

**Status:** Accepted
**Date:** 5 August 2026

## Context

OpenActs needs the exact source document while extracting and checking legal text, but storing PDFs in Git would bloat review history. Creating a separate public archive before an official source has actually disappeared would add storage, rights, and release machinery without serving the alpha reader.

## Decision

Each Source record stores the publisher URL, publisher, retrieval time, full SHA-256 digest, media type, byte length, page count, source class, and relevant document notes. The digest is the `source_id`. The public reader links to the publisher's document.

Acquisition stores a processing copy under `source-cache/sha256/<prefix>/<full-digest>.<extension>`. The cache is gitignored and its path is derived from the digest rather than stored in the corpus. A later download from the same URL is accepted as the same Source only when its digest matches; different bytes require a new Source record and review.

OpenActs does not initially publish source PDFs through Git, Git LFS, GitHub Releases, the API, or an object store. An OpenActs-hosted mirror requires a later decision triggered by observed link rot or a demonstrated reproducibility need, and only after redistribution is permitted.

## Why

The manifest and digest make source identity exact, while the local cache supports extraction without polluting the repository or product deployment. Deferring a hosted mirror keeps the first system smaller and leaves the storage decision tied to an actual preservation problem.
