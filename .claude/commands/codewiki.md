---
description: Codewiki operations — bootstrap (fill from codebase), lint (health check), ingest (retroactive page update)
argument-hint: bootstrap [<src_dir> ...] | lint | ingest <PH-XX>
allowed-tools: Read, Grep, Glob, Edit, Write, Bash, Task
---

You are the **Coordinator**. The user invoked `/codewiki $ARGUMENTS`. Dispatch on the first word of `$ARGUMENTS`. Canonical behavior: `contracts/exit-protocol.md` §11 + `docs/codewiki/SCHEMA.md`.

## `bootstrap [<src_dir> ...]`

Full fill of `docs/codewiki/` from the existing codebase (same behavior the **cold-start** auto-trigger runs — §11.5).

1. If `docs/codewiki/` is missing → tell the user to run `jarwis-init.sh` first (or scaffold from `~/Jarwis/templates/codewiki/`).
2. Determine scope: the `<src_dir>` args if given, else the repo's load-bearing/hot subsystems (skip vendored/build/generated dirs).
3. Invoke the **Architect** (Task, subagent_type=architect) in **bootstrap mode** with the scope. The Architect:
   - reads the source, writes pages under `components/` `concepts/` `api/` `decisions/` (use `docs/codewiki/page-template.md`)
   - fills `overview.md` (replace the placeholder)
   - **appends a `<glob> → <page>` line to `docs/codewiki/.codemap` for every page it creates** (arms the sync gate — §11.7; keep the map SMALL/selective)
   - updates `index.md` catalog + Stats (Pages: N)
   - appends `## [<today>] bootstrap | <summary> | [bootstrap]` to `log.md`
4. Report to the user: pages created, `.codemap` mappings added.

This is a one-time/selective pass — do NOT interleave it into per-ticket flow.

## `lint`

Health check (`§11.4`). Scan `docs/codewiki/` for: orphan pages (no inbound link), stale `[PH-XXXX]` refs (not in project-hub), broken `files:` paths, code-wiki desync (`.codemap` source changed but page didn't), contradicting claims, broken `[[wikilinks]]`. Append `## [<today>] lint | health check\nFindings: ...` to `log.md`. Report findings; offer to open a cleanup ticket. **No automatic cadence — only this command.**

## `ingest <PH-XX>`

Retroactively update a page a done ticket missed (`§11.6`). Map the ticket's touched files via `.codemap` → matching page(s). On a **docs-only branch**, invoke the original implementer role (or Architect) to update the page(s) (minimum: a `Design decisions` bullet `[<KEY>]` + `last_touched_ticket: <KEY>`). Append `## [<today>] ingest | retroactive <KEY> | [<KEY>]` to `log.md`, then merge. If no `.codemap` match → report "PH-XX touched files are unmapped; no ingest needed."

---
If `$ARGUMENTS` is empty or unrecognized, print the usage: `bootstrap [<src_dir> ...] | lint | ingest <PH-XX>`.
