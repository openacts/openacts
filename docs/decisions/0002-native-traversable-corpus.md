# 0002 — Native, readable, traversable corpus

**Status:** Accepted; storage and delivery details superseded by [0006 — Hybrid canonical corpus behind a thin read-only API](0006-hybrid-corpus-thin-api.md)
**Date:** 5 August 2026

## Context

The corpus must be understandable to a person reviewing it in Git, deterministic for software, and traversable across legal hierarchy, history, Citations, amendments, and Source provenance. Designing it around an external legal-data standard would make interoperability assumptions drive the product before an actual consumer requires them.

## Decision

OpenActs owns a small canonical model with six records: **Act**, **Version**, **Provision**, **Source**, **Change**, and **Citation**. Authored corpus files use deterministically ordered, indented JSON. IDs are stable, readable, and project-owned. Explicit fields connect parent and child Provisions, Versions through time, Citations to their targets, Changes to affected and affecting records, and content blocks to Source spans.

JSONL, Parquet, search indexes, backlinks, previous/next navigation, and other optimized views are generated and rebuildable. No external legal-standard schema, vocabulary, identifier system, or export is required. An outside concept may be adopted individually when it solves a demonstrated OpenActs need; an adapter is built only for a real consumer and cannot redefine the canonical model.

## Why

One native structure can serve the reader, editorial review, public corrections, dataset distribution, and any later API without competing sources of truth. This keeps Git review legible, machine validation strict, and traversal direct while avoiding speculative compatibility work.
