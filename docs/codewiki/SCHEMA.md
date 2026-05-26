# Codewiki Schema

> **Schema document** for `docs/codewiki/` — a living, LLM-maintained knowledge base about THIS PROJECT's codebase. Pattern adapted from the **LLM Wiki idea** (Vannevar Bush's 1945 Memex realized with LLMs handling the cross-referencing maintenance burden).
>
> **Who reads this**: every Jarwis sub-agent (PM, Architect, Implementer, Reviewer, QA) before touching the codewiki. Coordinator reads it during lint passes.
>
> **Who writes the codewiki**: sub-agents (Implementer / Reviewer / Architect) via their normal flow. The developer reads — does not edit pages by hand (except trivial typo fixes).

## The three layers

| Layer | What | Owner | Mutability |
|---|---|---|---|
| **Raw sources** | The codebase itself (`src/`, `backend/`, `frontend/`, `Assets/Scripts/`, ...) | Implementers (via tickets) | Mutable through normal flow |
| **The wiki** | `docs/codewiki/*.md` — synthesized pages | LLM (sub-agents) | LLM-owned, ticket-driven |
| **The schema** | This file + `CLAUDE.md` codewiki section | Co-evolved with project | Updated when conventions change |

The wiki is **synthesis over raw sources**, not a copy. It describes the codebase's current behavior, design decisions, and gotchas — with `[PH-XX]` ticket refs back to **intent** (project-hub) and `src/` file refs forward to **truth** (the code).

The three-way triangle:

```
   ticket history (WHY)
        |  intent, decision, scope
        ↓
codebase wiki  ←→  git
   (WHAT)          (WHEN/HOW)
```

## Directory layout

```
docs/codewiki/
├── SCHEMA.md            # this file
├── index.md             # catalog of all pages (read FIRST on query)
├── log.md               # append-only chronology
├── .codemap             # file glob → page mapping (for sync lint)
├── overview.md          # codebase 1-page summary
├── components/          # module / sub-system pages
├── concepts/            # cross-cutting concepts
├── api/                 # MCP tools, REST endpoints reference
└── decisions/           # ADR-style "why this way" pages
```

Subdirectories are **categories**, not strict containers. A page that's both a component and a concept lives in `components/` (more concrete).

## Page format

Every page MUST have YAML frontmatter + standard sections:

```markdown
---
type: component | concept | api | decision
files: [src/path/to/file1.py, src/path/to/file2.py]
last_touched_ticket: PH-42
related: [[concepts/claim-system]], [[api/mcp-tools]]
status: active
---

# <Page Title — same as filename basename, human-readable>

> 1-line description (optional).

## Current behavior
<!-- 1-3 paragraphs describing what this thing does NOW.
     The canonical "how it works" — kept current, no historical narrative. -->

## Design decisions (recent)
- <decision> [PH-42] — <one-line rationale>
- <decision> [PH-15] — <one-line rationale>

## Known gotchas
- <gotcha> [PH-23]

## Related
- [[components/state-machine]]
- [[api/mcp-tools#transition_state]]
```

**Required sections**: `## Current behavior`, `## Design decisions (recent)`, `## Related`.
**Optional sections**: `## Known gotchas`, `## Migrations`, `## Performance notes`, `## Open questions`.

### Frontmatter fields

| Field | Required | Meaning |
|---|---|---|
| `type` | yes | One of: `component`, `concept`, `api`, `decision`, `overview` |
| `files` | yes (except `concept`, `decision`) | Source file paths this page describes |
| `last_touched_ticket` | yes | Last ticket that updated this page (`PH-XX` or `bootstrap`) |
| `related` | recommended | Wikilinks to related pages — feeds Obsidian graph |
| `status` | optional | `active` (default), `deprecated`, `archived` |
| `owner` | optional | If a specific developer owns this domain |

## Cross-reference conventions

Three reference types — each meaningful, each required in its place:

1. **`[[wikilinks]]`** between pages → Obsidian graph edges.
   - Form: `[[components/state-machine]]` or `[[components/state-machine|state machine]]` (with display text)
   - Section anchor: `[[api/mcp-tools#transition_state]]`
   - Use in `related:` frontmatter AND inline in body where natural.

2. **`[PH-XX]`** to project-hub tickets → intent source.
   - Form: `[PH-42]` (plain ticket key in brackets, no special syntax)
   - **MANDATORY** on every design decision, gotcha, breaking change
   - Use `[bootstrap]` for initial seed content (placeholder until a real ticket exists)

3. **File paths** to source → truth.
   - Form: `src/jarwis/state.py:42` (with line) or `src/jarwis/state.py` (general)
   - Always declared in `files:` frontmatter; optionally inline when pointing at a specific symbol

## Operations

### Ingest (per ticket done)

Triggered by Implementer/Reviewer/Architect at end of work. Steps:

1. Read `.codemap` — do my touched files map to wiki pages?
2. For each matched page:
   - Add a bullet under "Design decisions (recent)" with `[PH-XX]` ref + one-line rationale
   - Update "Current behavior" if the behavior changed (rewrite, don't append)
   - Add to "Known gotchas" if you discovered one
   - Update `last_touched_ticket: PH-XX` in frontmatter
3. Append to `log.md`:
   ```
   ## [YYYY-MM-DD] ingest | <one-line summary> | [PH-XX]
   Touched: components/state-machine.md, concepts/claim-system.md
   Summary: in_test gate added before done; QA skip now blocked.
   ```
4. Refresh `index.md` if a page was created/renamed/recategorized

**If touched files don't map to ANY page**:
- Significant change → create a new page + add to `.codemap` + add to `index.md`
- Trivial (typo, comment fix, formatting) → skip wiki update
- Unsure → leave a ticket comment asking Architect for next planning round

### Query (per planning / question)

When asked "how does X work?":

1. Read `index.md` first — find pages tagged with X or in the X area
2. Read the most relevant 1-3 pages — synthesize from "Current behavior" + "Design decisions"
3. Drill into `[PH-XX]` ticket refs if you need WHY a decision was made
4. Drill into `src/...` file refs if you need ground truth (page may be stale)
5. If you find the wiki is wrong → file a fix ticket OR fix inline if trivial

### Lint (periodic — Coordinator runs)

Weekly OR every N done tickets. Check:

- **Orphan pages**: page exists but no other page links to it (`related:` or inline `[[...]]`) — add inbound links OR delete
- **Stale ticket refs**: `[PH-XXXX]` doesn't exist in project-hub (typo OR ticket deleted)
- **Broken file refs**: `files:` lists paths that no longer exist
- **Code-wiki desync**: file in `.codemap` was touched in last N commits but its target page was NOT (pre-commit hook catches at PR-time; lint catches what slipped through)
- **Contradicting claims**: two pages make opposing statements about the same thing
- **Missing pages**: a `[[wikilink]]` points at a page that doesn't exist
- **Stagnant pages**: page with 5+ decisions but empty `## Known gotchas` — gotchas accumulate, probably exist

Append result to `log.md`:
```
## [YYYY-MM-DD] lint | health check
Findings: 2 orphans, 1 broken ref, 0 contradictions
Action: opened cleanup ticket [PH-50] OR resolved inline
```

## The .codemap file

File→page mapping for code-wiki sync lint. Format:

```
# src glob → wiki page (relative to docs/codewiki/)
src/jarwis/state.py            → components/state-machine.md
src/jarwis/transitions.py      → components/state-machine.md
backend/app/api/mcp/*.py       → api/mcp-tools.md
frontend/src/components/Ticket*.tsx → components/ticket-ui.md
```

Rules:
- Multiple sources → one page (many-to-one) is fine
- One source → multiple pages is **not allowed** (pick the most specific)
- Lines starting with `#` are comments; blank lines ignored
- Empty `.codemap` = sync lint disabled (default during bootstrap)

## The index.md file

Content-oriented catalog. Architect/PM reads this FIRST on query.

Format:

```markdown
# Codewiki Index

## Components
- [[components/state-machine]] — Ticket state machine + transitions
- [[components/auth]] — Authentication / authorization

## Concepts
- [[concepts/claim-system]] — How agents claim and release tickets

## API reference
- [[api/mcp-tools]] — Project-hub MCP tools

## Decisions
- [[decisions/0001-state-machine]] — Why Coordinator owns transitions

## Stats
- Pages: 12  |  Last lint: 2026-05-26  |  Last ingest: [PH-42]
```

Update on every ingest that creates/renames a page or shifts category.

## The log.md file

Append-only chronology. Format: `## [YYYY-MM-DD] <op> | <title> | [TICKET-KEY]`.

Op types:
- `bootstrap` — initial page generation during jarwis-init
- `ingest` — wiki update triggered by ticket done
- `lint` — periodic health check
- `query` — significant query that produced a saved page (rare)

Parseable via shell: `grep "^## \[" log.md | tail -10` → recent activity.

## Anti-patterns (don't do)

- ❌ Page that just lists files with no narrative — must describe behavior, not act as a TOC
- ❌ Copy-pasting code into pages — link with `src/path:line` instead
- ❌ Page-per-file 1:1 — pages are about concepts/components, can span multiple files
- ❌ Writing the wiki to "look complete" — write it to **answer questions you actually have**
- ❌ Treating ticket refs as decoration — every design decision MUST have `[PH-XX]` (or `[bootstrap]`)
- ❌ Updating wiki AFTER ticket is done and merged — wiki update belongs in the same commit (else sync lint fails)
- ❌ Long historical narratives in "Current behavior" — that section describes NOW; history lives in "Design decisions" + git log

## Genesis

This codewiki was bootstrapped by `jarwis-init.sh` at project setup. Initial pages were generated by Architect agents reading the existing codebase; subsequent updates come from the normal Jarwis ticket flow (ingest operation).

Pattern source: the **LLM Wiki idea** — a personal knowledge base where the LLM handles the cross-referencing maintenance burden that humans abandon. Bush's 1945 Memex finally realized.
