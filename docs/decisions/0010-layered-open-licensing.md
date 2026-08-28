# 0010 - Layered open licensing

**Status:** Accepted
**Date:** 5 August 2026

## Context

OpenActs combines software, project-authored data and documentation, exact statutory wording, and references to third-party source documents. One blanket licence would either claim rights OpenActs does not own or leave reusable project work without clear permission.

## Decision

OpenActs software and schema implementations use the Apache License 2.0. OpenActs-authored corpus metadata, structure, annotations, editorial material, and documentation use Creative Commons Attribution 4.0 International.

Exact statutory wording is identified as official Nigerian legislative text and is outside the OpenActs CC BY licence grant; OpenActs does not claim ownership of or relicense the law itself. Third-party material retains its own status and notices. Source PDFs are not distributed under Decision 0009.

Contributions are submitted under the applicable existing outbound licence without a separate contributor licence agreement initially. Repository licence files, dataset metadata, documentation, and release labels must state the same layer boundaries before the first public dataset release.

## Why

Apache-2.0 permits broad software reuse under a standard code licence. CC BY 4.0 permits commercial sharing and adaptation of OpenActs' original data and documentation while preserving attribution. Separating those grants from statutory wording makes the corpus reusable without pretending that OpenActs owns the law.
