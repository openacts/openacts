# 0001 — Reader and contribution model

**Status:** Accepted
**Date:** 5 August 2026
**Updated:** 6 August 2026

## Context

OpenActs needs both a direct way to read Nigerian legislation and a trustworthy way for a community to improve the corpus. A single analogy does not adequately describe both responsibilities.

## Decision

The public reader follows the Bible Gateway model: exact-reference lookup, book-like hierarchy, uncluttered Provision pages, and permanent links. Corpus maintenance follows the useful part of the Wikipedia model: public history, evidence-backed corrections, visible uncertainty, and human review before publication. The enacted or officially published Source controls the legal text; community consensus cannot change it.

Git commits, pull requests, and repository review history carry contribution and review evidence. Canonical corpus records do not embed reviewer identities, approval outcomes, timestamps, or review links. Automation validates records and builds releases; it does not write approval metadata back into the corpus. A portable attestation model requires a later explicit decision if a real consumer needs one.

The initial contribution interface is GitHub issues and small pull requests, not an unrestricted browser wiki. OpenActs is a legal reader and corpus, not a chatbot or legal-advice product.

## Why

The reader model serves the core job of reaching and verifying an exact Provision quickly. The contribution model allows open improvement without treating statutory wording as editable opinion. A free-form wiki or generated-answer interface would weaken the source-verification boundary before there is evidence that either is needed.
