# 0021 — PostgreSQL read projection for the corpus API

**Status:** Accepted
**Date:** 17 August 2026

## Context

OpenActs now has two canonical Acts and expects the corpus to keep growing. The
startup-loaded in-memory indexes selected while the corpus was smaller would be
rebuilt in every API process, couple startup time and memory use to corpus size,
and keep application deployment tied to corpus distribution. The Workspace VPS
already operates a shared PostgreSQL service for low-traffic applications.

## Decision

The authored Act, Provision, Source, and Citation JSON and JSONL records remain
the canonical corpus. An immutable `corpus-vX.Y.Z` Git tag and its exact commit
identify each published corpus release.

The existing shared PostgreSQL service stores a release-scoped read projection
generated from one validated corpus release. Each projected release records its
tag, commit, canonical schema versions, and import state. The projection is
non-canonical: it is never independently edited, can be deleted and rebuilt
from its tag, and does not make a database backup a legal-data authority.

Projection is an explicit release operation, not an API startup side effect.
Its non-writing preview resolves and validates the exact tag before an explicit
execute operation imports and activates the release atomically. Activation
uses one active-release pointer, retains the previous release for rollback, and
cannot expose a partial import. Application deployment neither imports nor
activates a corpus release, so application and corpus releases remain
independently rollbackable.

FastAPI remains a thin, read-only corpus boundary. It uses a SELECT-only role,
resolves the active release once per request, and constrains every subsequent
query to that release. Only the explicit projection operation uses a
write-capable owner role. PostgreSQL stores the generated hierarchy, navigation,
exact-lookup, and lexical-search read models and supports them with B-tree and
full-text indexes. The canonical record remains preserved in the projection for
fidelity.

This supersedes only the startup in-memory projection and no-persistent-database
parts of decision 0006, and the database-free and container-bundled-corpus parts
of decision 0008. Their thin API, versioned REST, cache, hosting, readiness,
CORS, and independent deployment choices remain accepted. OpenActs continues
to consume shared gateway, network, and PostgreSQL infrastructure rather than
recreating it in this repository.

## Why

The shared PostgreSQL service provides one atomic release view for every API
process and one inspectable implementation for traversal and lexical search
without introducing another production system. In-memory indexing would repeat
work and grow startup cost per process. A file-backed or in-memory SQLite
database would still require per-instance artifact distribution or rebuilding
and coordinated release switches. A new managed database, Redis cache, or
dedicated search service would add cost and operations before the reader needs
them. Making PostgreSQL canonical was rejected because Git review, provenance,
deterministic serialization, and immutable corpus tags remain the stronger
editorial and release boundary.
