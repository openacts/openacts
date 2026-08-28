# 0014 - Change-driven corpus releases

**Status:** Accepted
**Date:** 6 August 2026

## Context

Legal instrument dates, corpus dataset releases, and application deployments represent different changes. A calendar-only schedule would delay accepted corrections or create empty releases, while using one version number for code and data would make dataset identity depend on unrelated frontend or API changes.

## Decision

Every reviewed corpus change that reaches production creates an immutable Git tag and corpus release. Application-only changes deploy by exact Git commit without changing the corpus version. No release is created merely because a month or quarter ended.

Corpus tags use `corpus-vX.Y.Z`. Patch releases correct existing transcription, Source evidence, or metadata without adding legal-text coverage. Minor releases add Acts or expand Provision or Citation coverage. After `corpus-v1.0.0`, major releases are reserved for incompatible schema or stable-identity changes. Alpha releases use `0.x`; an incompatible alpha change advances the minor version and is documented. The public beta establishes `corpus-v1.0.0`.

`corpus-v0.0.0` is reserved for the immutable, non-production bootstrap snapshot used to build and exercise corpus release tooling. It is not evidence that the corpus has passed production review and must not be activated on the production reader. The tag is never moved: bootstrap corrections advance the patch version, and the first reviewed alpha release is `corpus-v0.1.0`.

The immutable tag resolves to the exact Git commit; Git provides the previous-release diff, and the tagged records identify their schema version. The frontend and API expose the same corpus version. Act dates remain independent of corpus and application versions. Separate release manifests and artifact checksums are added only when OpenActs actually distributes generated corpus artifacts.

## Why

Change-driven releases publish corrections promptly and avoid meaningless calendar snapshots. Separate corpus identity lets data consumers reproduce an exact dataset while application code continues to deploy independently.
