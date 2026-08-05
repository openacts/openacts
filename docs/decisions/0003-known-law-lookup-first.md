# 0003 — Known-law lookup first

**Status:** Accepted
**Date:** 5 August 2026

## Context

The wider OpenActs vision serves people locating known law, people discovering which law is relevant, and developers or researchers consuming the corpus. Trying to optimize the alpha equally for all three would blur its primary job and pull conceptual search, explanation, and data-platform work into the first release.

## Decision

The alpha is primarily for a person who already knows, or approximately knows, the Nigerian Act, Provision, citation, title, or wording they need and wants to locate, verify, and share the exact text. This is a behavioral definition rather than a profession: the user may be a lawyer, student, journalist, compliance professional, developer, or member of the public.

Legal discovery and dataset consumption remain part of the product vision, but they follow the known-law reader. The alpha therefore prioritizes exact citation and title lookup, precise keyword search, permanent Provision URLs, legal hierarchy, Source verification, and copyable Citations. It does not need legal Q&A, generated explanations, conceptual search, or an API to complete its primary job.

## Why

Known-law lookup is the smallest end-to-end test of the core corpus and Bible Gateway reader. It proves whether OpenActs can make statutory text directly reachable and trustworthy before later product tracks add broader discovery and machine-consumption capabilities.

