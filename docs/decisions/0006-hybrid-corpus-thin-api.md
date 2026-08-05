# 0006 — Hybrid canonical corpus behind a thin read-only API

**Status:** Accepted
**Date:** 5 August 2026

## Context

The corpus must remain directly readable and reviewable in Git, while its largest collections must also support record-level diffs, streaming, and deterministic processing. The public frontend should be a conventional application with an HTTP data boundary, not a reader coupled to the repository layout or a static-site generator. A database is unnecessary while the corpus is small enough to validate and index at process startup.

## Decision

The six-record OpenActs model remains canonical. Small metadata documents use indented JSON: `act.json`, `sources.json`, and each `version.json`. Growing record collections use canonical JSON Lines: `provisions.jsonl`, `citations.jsonl`, and `changes.jsonl`. Every JSONL line is one complete schema-validated record; file ordering and serialization are deterministic. Parquet and other consumer formats are generated projections, never independently edited sources of truth.

A thin, read-only FastAPI service is the frontend's corpus boundary. It validates and loads a tagged corpus release at startup, builds disposable in-memory traversal and lexical-search indexes, and exposes a versioned REST API with corpus-version metadata and cache validators. It does not edit the corpus, run a CMS, own a background queue, or require a persistent database.

The frontend is a standard web application that knows the HTTP contract, not corpus file paths. It may server-render or prerender public Act and Provision routes for accessibility, link previews, and search indexing, while client navigation and search use the same API. Framework and hosting choices require a separate implementation decision.

## Why

This keeps one portable source of truth while making large legal collections easy to diff and process. The API gives every frontend a stable, conventional boundary without prematurely introducing a database or search service. Any later database remains a replaceable projection of a corpus release rather than a competing authority.
