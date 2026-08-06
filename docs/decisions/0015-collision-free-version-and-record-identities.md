# 0015 — Collision-free Version and Citation identities

**Status:** Superseded by [0018 — Direct Act and Provision corpus](0018-direct-act-provision-corpus.md)
**Date:** 6 August 2026

## Context

A Version ID containing only an Act ID and date collides when editions exist in more than one language. Citation IDs also need assignment rules that remain stable when text moves during correction.

## Decision

A Version ID is `<act_id>@<as_at_date>:<language>`, for example `ng-federal-act-2023-37@2023-06-12:eng`. Its directory is `versions/<as_at_date>/<language>/`. A corpus correction retains the same Version ID; a different language or legal date is a different Version.

A Citation ID is `citation:<source_version_id>:<six-digit-sequence>`. Sequences are assigned once within their source Version and never recomputed. Provision IDs remain stable across corpus corrections; a stored `~2`, `~3`, or later suffix resolves a collision between distinct historical identities and never changes thereafter.

## Why

The IDs contain only legal identity facts needed to prevent collisions. They remain readable without deriving identity from mutable titles, current paths, or database keys.
