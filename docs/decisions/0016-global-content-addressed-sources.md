# 0016 — Global content-addressed Source manifest

**Status:** Accepted
**Date:** 6 August 2026

## Context

One official PDF may contain or evidence several Acts, and an amendment necessarily links an affected Act to a Source published for an affecting Act. Giving a content-hash Source one owning `act_id` would make the same bytes acquire contradictory identities.

## Decision

Sources are corpus-global records in `corpus/sources.jsonl`, deterministically ordered by `source_id`. Each line describes one exact byte sequence and has no owning Act. Acts, date and status claims, Citations, and provenance spans link to Sources by digest.

The alpha Source contract accepts PDF documents only. It records measured PDF properties and one or more HTTP or HTTPS retrieval locations. Supporting another media type requires an explicit schema extension rather than pretending that page-based PDF fields apply to it.

Source binaries remain outside Git in the existing local content-addressed cache. The global manifest changes where metadata is authored, not where the bytes are stored or whether OpenActs redistributes them.

## Why

Content identity is independent of the law that happens to reference it. One global record prevents duplicate manifests, supports multi-Act gazettes and amendment evidence, and preserves exact provenance without introducing a document archive.
