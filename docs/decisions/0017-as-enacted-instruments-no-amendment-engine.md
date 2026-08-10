# 0017 — As-enacted instruments without an amendment engine

**Status:** Accepted; the consolidated-text prohibition is superseded by [0020 — Source-declared consolidated Act text](0020-source-declared-consolidated-act-text.md)
**Date:** 6 August 2026

## Context

An amending Act is itself a separately enacted legal instrument. The OpenActs alpha exists to make exact enacted instruments readable, searchable, traversable, and shareable; it does not promise a continuously consolidated statement of current law.

## Decision

OpenActs publishes each Act or Constitution as enacted. An Amendment Act is stored as another Act, and Citations may connect its provisions to the instrument or Provision it names. The Act record may carry an evidenced, date-bounded status such as `in_force` or `repealed`. That status does not turn the displayed enacted text into consolidated current wording. The alpha does not author Change records, maintain `changes.jsonl`, apply amendment instructions, or publish consolidated text.

The enacted text is stored directly as the Act's Provisions. Corpus corrections retain Act and Provision identities and appear in a new corpus release.

The reader links to a prefilled GitHub issue form for transcription errors, broken Sources, and missing Acts or relationships. GitHub stores the report; no bot or Action writes workflow metadata into canonical records.

## Why

This keeps the canonical corpus about durable legal instruments rather than an unproven amendment engine. Consolidation can be designed later from real Nigerian amendment cases if readers demonstrate that need.
