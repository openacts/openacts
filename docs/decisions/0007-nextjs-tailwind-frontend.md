# 0007 — Next.js and Tailwind frontend

**Status:** Accepted
**Date:** 5 August 2026

## Context

OpenActs needs a conventional frontend that can publish crawlable, accessible legal pages while still supporting fast client navigation and search. The frontend must remain a consumer of the corpus API rather than becoming a second backend or reading repository files directly.

## Decision

The frontend uses the Next.js App Router with strict TypeScript and Tailwind CSS. Public Act and Provision routes are server-rendered or prerendered from the corpus-versioned FastAPI contract. Client Components are limited to interactions that require browser state, including search and in-page navigation.

FastAPI remains the only corpus backend. Next.js Route Handlers and Server Actions do not duplicate, proxy, or mutate corpus operations. Tailwind is the styling system, with a small project theme and semantic accessible HTML underneath; no component library or second CSS architecture is adopted by default.

Frontend hosting remains a separate decision. The application must remain deployable without coupling its data model or release process to one hosting provider.

## Why

This is a standard React stack with built-in routing, rendering, metadata, loading, and error conventions. It makes legal pages usable before client JavaScript runs while preserving a full application experience. Tailwind provides a direct styling vocabulary without introducing a separate runtime or generic component system.
