# Codewiki Index

> Catalog of all codewiki pages. **Read this first** when looking for "how does X work?".
>
> Maintained by Jarwis sub-agents — updated via the ingest operation (see [SCHEMA.md](SCHEMA.md)).

## Overview

- [[overview]] — Top-level codebase summary

## Components

<!-- module / sub-system pages — describe HOW concrete pieces of the codebase work -->

- [[components/backend]] — FastAPI + SQLAlchemy + Alembic + Postgres/Redis service powering REST + MCP surfaces, ticket state machine, stale-claim cron, Redis event bus
- [[components/frontend]] — React 18 + Vite + Tailwind SPA with TanStack Query cache, Zustand auth store, custom WebSocket hook, and `@xyflow/react`-based workflow editor

## Concepts

<!-- cross-cutting concepts spanning multiple files / components -->

(none yet)

## API reference

<!-- MCP tools, REST endpoints, public function signatures -->

(none yet)

## Decisions

<!-- ADR-style "why this way" — long-lived architectural decisions -->

(none yet)

## Stats

- Pages: 3
- Last lint: never
- Last bootstrap: 2026-05-26
- Last ingest: 2026-05-26 [PH-148]
