# 0012 — Exact and lexical search through beta

**Status:** Accepted
**Date:** 5 August 2026

## Context

OpenActs' first job is to take a known or approximately known Act, citation, title, or wording to the exact legal Provision. Embeddings and query models would add serving, versioning, ranking, cost, and privacy obligations before the ten-Act reader has established that deterministic and lexical retrieval are inadequate.

## Decision

Alpha and beta search use deterministic citation resolution, title and alias matching, and lexical Provision search. Exact citations bypass fuzzy ranking. OpenActs does not add embeddings, a vector database, a query model, or LLM-generated answers during those releases.

Semantic legal discovery remains a later product track. Before it is implemented, OpenActs builds a held-out Nigerian legal query set, establishes the exact and lexical baseline, and defines acceptance criteria. Semantic retrieval ships only when it materially improves conceptual queries without reducing exact-reference accuracy, violating query privacy, or exceeding an accepted latency and operating-cost budget.

If semantic retrieval is later added, it returns ranked Acts and Provisions with their exact text and Sources. It does not replace the corpus with generated explanations or answers.

## Why

Exact and lexical retrieval directly prove the initial reader job with deterministic, inspectable behavior. Evaluation keeps the broader discovery vision alive while preventing speculative model infrastructure from becoming part of the corpus or beta critical path.
