# 0008 — Vercel frontend and Workspace API

**Status:** Accepted
**Date:** 5 August 2026

## Context

OpenActs needs hosting for a Next.js frontend and a small FastAPI corpus service. GitHub Pages would require a static export, would place the existing repository under a project subpath, and would discard the runtime rendering options chosen for the standard frontend. The existing Workspace VPS already provides the deployment contract for low-traffic application backends.

## Decision

Deploy the Next.js frontend to Vercel, targeting `openacts.vercel.app` when the project name is available. A dedicated custom domain may replace that public address later without changing the application architecture. GitHub Pages is not a deployment target.

Deploy FastAPI as one database-free container on the shared Workspace VPS behind its existing Caddy gateway. The container includes one explicit corpus release, exposes readiness plus application and corpus version metadata, and remains running rather than adding cold-start latency to lookup and search. Only the production frontend origin is allowed for browser API requests unless a preview origin is deliberately approved.

Frontend and API deployments are independently rollbackable and identify their exact Git commit and corpus release. OpenActs does not recreate shared gateway or database infrastructure in its repository.

## Why

Vercel supports the selected Next.js runtime without forcing the application into static-export constraints. The Workspace VPS already owns backend routing, TLS, health, resource, and deployment conventions. This uses the established platforms, keeps the corpus service simple, and leaves a future custom domain as a naming change rather than an architectural migration.
