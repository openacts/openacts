# 0011 — No behavioural analytics

**Status:** Accepted
**Date:** 5 August 2026

## Context

Searches for legislation can reveal sensitive legal, employment, commercial, or personal interests. The alpha can evaluate lookup quality through moderated user testing and a versioned golden-query set without identifying visitors or retaining what they search for.

## Decision

OpenActs adds no behavioural analytics service, analytics SDK, session replay, advertising tracker, tracking cookie, user identifier, or interest profile. This includes optional analytics products offered by hosting providers.

Search and citation-resolution input is sent in bounded `POST` bodies and is excluded from application, proxy, analytics, and error logs. Operational records contain only the route template, status, latency, request ID, application version, and corpus release. Build, editorial, readiness, resource, and aggregate error metrics remain allowed because they measure the system rather than a person's behaviour.

Product learning uses moderated tests and the offline golden-query set. Adding analytics later requires a new accepted decision naming the specific product question, collected fields, processor, retention period, access boundary, and deletion or opt-out behavior.

## Why

The first product needs evidence that exact lookup works, not a visitor-tracking system. Avoiding behavioural collection protects sensitive searches, removes consent and data-retention machinery, and still leaves enough operational evidence to run the service reliably.
