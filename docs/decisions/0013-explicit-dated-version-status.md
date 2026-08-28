# 0013 - Explicit dated Version status

**Status:** Superseded by [0017 - As-enacted instruments without an amendment engine](0017-as-enacted-instruments-no-amendment-engine.md)
**Date:** 5 August 2026

## Context

The exact wording of an Act can refer to its enacted text or to a later text produced by applying amendments. Calling whichever text OpenActs happens to display “current” would hide that distinction and turn an unresolved research state into a product claim.

## Decision

Every Version has an `as_at_date`, language, and kind: `as_enacted` or `consolidated`. Those three facts form its collision-free identity as defined by decision 0015. The frontend and API show the explicit state and date. They do not use a generic “current” Version label.

An `as_enacted` Version reproduces the enacted wording. A `consolidated` Version is published only when every known in-scope Change effective on or before its date has been verified and applied and no such Change remains unresolved. The Version records its `checked_through_date`, applied Changes, and unresolved Change IDs; counts are generated rather than authored.

The undated Act and Provision URLs resolve to the latest Version published by OpenActs but make no claim that it is legally current. Dated URLs identify exact point-in-time text. A transcription correction to the same legal text creates a new corpus release, not a fabricated legal Version date.

## Why

Explicit Version kinds and dates tell the reader exactly which wording is displayed without turning status into disclaimer copy. They also give the API, URLs, amendment pipeline, and datasets one deterministic rule for distinguishing corrections from legal change.
