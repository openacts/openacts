# 0018 — Direct Act and Provision corpus

**Status:** Accepted
**Date:** 6 August 2026

## Context

Once OpenActs limited the alpha to instruments as enacted, the Version record no longer represented a legal state. Its date duplicated Act dates, its root list and fidelity were derivable from Provisions, its checksum belonged to the corpus release, and its remaining Source relationships did not justify a separate canonical layer.

## Decision

The canonical corpus has four records: Act, Provision, Source, and Citation. Each Act directly owns one as-enacted Provision tree and its Source relationships. The Constitution uses the same Act record without a subtype. `provisions.jsonl` and `citations.jsonl` live directly beside `act.json`; there is no `version.json` or `versions/` directory.

Provision contains only stable identity, structural type, visible label and heading, parent and order, content blocks, Source spans, and text fidelity. Paths, normalized labels, roots, children, navigation, and aggregate fidelity are generated. Citations identify their source Provision and block directly; Citation sequences are assigned within the source Act. Corpus releases, not legal Version records, identify corrections.

A separate Edition record may be designed later if OpenActs publishes multiple language editions of the same instrument. It is not reserved in the alpha schema.

## Why

The direct model contains every edge needed to read, traverse, verify, search, and link enacted law without an empty abstraction. It also leaves multilingual editions as a concrete later requirement rather than keeping amendment-era machinery under a new name.
