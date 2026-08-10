# 0020 — Source-declared consolidated Act text

**Status:** Accepted
**Date:** 10 August 2026

## Context

The selected PLAC Constitution source presents one consolidated text through
the Fourth Alteration. Treating that wording as the Constitution as enacted in
1999 would be false, while restoring a Version and amendment-operation model
for one directly published text would add machinery the alpha does not need.

## Decision

An Act may directly own either `as_enacted` or `consolidated` text, recorded by
its optional `text_kind`. Omission retains the existing `as_enacted` meaning
for records authored before this field was introduced.

A consolidated Act is accepted only when its authoritative Source itself
presents consolidated wording. The Source relation and editorial notes state
the consolidation boundary and whether OpenActs independently verified it.
OpenActs does not generate the consolidation, apply amendment operations, or
call it current without separate evidence.

Each Act still owns one Provision tree. Publishing another legal state of the
same Act would require a later explicit model decision; it is not represented
as a corpus correction.

## Why

This truthfully publishes the acquired PLAC edition with one discriminating
field and existing provenance fields. It avoids both mislabelling amended text
as enacted wording and rebuilding the superseded Version system.
