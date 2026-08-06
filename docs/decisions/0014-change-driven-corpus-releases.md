# 0014 — Change-driven corpus releases

**Status:** Accepted
**Date:** 6 August 2026

## Context

Legal Version dates, corpus dataset releases, and application deployments represent different changes. A calendar-only schedule would delay accepted corrections or create empty releases, while using one version number for code and data would make dataset identity depend on unrelated frontend or API changes.

## Decision

Every reviewed corpus change that reaches production creates an immutable Git tag and corpus release. Application-only changes deploy by exact Git commit without changing the corpus version. No release is created merely because a month or quarter ended.

Corpus tags use `corpus-vX.Y.Z`. Patch releases correct transcription or metadata without adding legal coverage. Minor releases add Acts, legal Versions, or reviewed Changes. After `corpus-v1.0.0`, major releases are reserved for incompatible schema or stable-identity changes. Alpha releases use `0.x`; an incompatible alpha change advances the minor version and is documented. The public beta establishes `corpus-v1.0.0`.

Every corpus release identifies its exact Git commit, previous-release diff, schema version, manifest, and artifact checksums. The frontend and API expose the same corpus version. Legal `as_at_date` values remain independent of corpus and application versions.

## Why

Change-driven releases publish corrections promptly and avoid meaningless calendar snapshots. Separate corpus identity lets data consumers reproduce an exact dataset while application code continues to deploy independently.
