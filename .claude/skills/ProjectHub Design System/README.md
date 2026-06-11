# ProjectHub — Design System

> **"Cyan on Black."** A premium, calm-but-electric dark theme for a power-user,
> developer-facing control panel. Deep near-black canvas, layered dark surfaces, a
> single electric-cyan accent used with restraint. Linear × Vercel × Raycast × Resend,
> with the glowing, gradient-bordered component language of 21st.dev / Magic UI.

This folder is a **living design system**: foundations (color, type, spacing, motion),
a component library, and a high-fidelity UI-kit recreation of the product — all in the
new cyan-on-black art direction. Use it to build on-brand ProjectHub interfaces, mocks,
and production code.

---

## What is ProjectHub?

ProjectHub is a **local-first, MCP-first, Jira-like ticket/project management system built
for a multi-agent software-development workflow** — "Linear × SourceTree × a CI dashboard."

A human **Coordinator** (admin) monitors and drives everything in real time over a live
WebSocket connection, while a fleet of **AI agents** (PM, Architect, Backend, Frontend,
Reviewer, QA, Orchestrator — named `jarwis-pm`, `jarwis-architect`, …) work tickets
through a **workflow state machine**. The audience is technical; high information density
is welcome and expected.

### Surfaces (the product, screen by screen)
- **Login** — bearer-token auth, single centered glass card.
- **Boards list** — a grid of board cards (monospace KEY badge, name, description, meta chips: project type, "N states").
- **Kanban board** — columns per workflow state (`backlog → to_do → in_progress → blocked → in_review → in_test → done`), ticket cards with monospace key, type/priority/label chips, assignee, and a live "agent phase" pulsing indicator.
- **Branch Graph** — a SourceTree-style 3-pane git view: branch sidebar │ commit list with an SVG lane gutter │ commit diff panel. New commits arrive live at the top with a cyan glow.
- **Ticket detail** — title + mono key + type chip + state control with allowed transitions; markdown Description and role fields (Technical Depth, Acceptance Criteria, Impact Analysis, Test Plan); an Activity feed with tabs (All │ Comments │ History │ Git); right sidebar with priority/reporter/assignee/labels/branch.
- **Board Settings** — tabbed: General │ Workflow (state list + visual state-machine editor + permissions matrix) │ Members (human + agent roster with role chips) │ Repository (connection status, config, secret rotation).

### Domain facts baked into the system
- **Workflow states:** `backlog → to_do → in_progress → blocked → in_review → in_test → done`
- **Ticket key format:** `PH-167` (monospace, always)
- **Ticket types:** feature, bug, task, epic
- **Priorities:** low, medium, high, urgent (colored dot)
- **Roles:** admin, pm, architect, backend_dev, frontend_dev, reviewer, qa, orchestrator
- **Agent actors:** `jarwis-pilot`, `jarwis-pm`, `jarwis-architect`, `jarwis-backend`, `jarwis-frontend`, `jarwis-reviewer`, `jarwis-qa`
- **Live status:** Live (green) / Connecting (amber) / Off (red)
- **Permissions grammar:** `<resource>.<action>[:<scope>]`, e.g. `state.transition:to_in_review`

---

## Sources

These are the materials this system was built from. The reader is **not** assumed to have
access — paths are recorded in case they do.

| Source | Path / location | Notes |
|---|---|---|
| Product codebase | `project-hub/` (local mount) | FastAPI + React 18 + Vite + Tailwind + shadcn/ui. Frontend at `project-hub/frontend/src`. |
| Original tailwind theme | `project-hub/frontend/tailwind.config.js` | State color tokens. |
| State / badge tokens | `project-hub/frontend/src/lib/utils.ts`, `lib/stateColor.ts` | `STATE_CATEGORIES`, `PRIORITY_DOT`, `TYPE_BADGE`. |
| Git lane algorithm | `project-hub/frontend/src/components/git/branchGraphLayout.ts` | `LANE_COLORS`, `LANE_W=16`, `ROW_H=36`, two-pass lane assignment. |
| Permissions matrix | `project-hub/docs/permissions.md` | Role × action grammar + default matrix. |
| Art-direction brief | `claude-design/PROMPT.md` | The "Cyan on Black" master prompt + token cheat-sheet. |
| Reference screenshots | `uploads/00-…` → `09-…` (light + dark) | Existing product UI (pre-redesign). |

> **Design note:** the *original* product is a blue-slate shadcn theme with an indigo/purple
> accent (see screenshots). This design system is the commissioned **redesign** into
> "Cyan on Black." Information architecture is preserved; the visual language is new.

---

## CONTENT FUNDAMENTALS

How ProjectHub writes. The product audience is engineers and AI agents, so copy is
**terse, technical, and literal** — labels over sentences.

- **Voice:** neutral, system-like, precise. No marketing tone, no exclamation. The UI
  states facts ("17 files changed", "12 members", "No tickets") rather than addressing
  the user.
- **Person:** mostly **impersonal/imperative**. Buttons are verbs ("Create", "Add Member",
  "Activate", "Edit"). Section labels are nouns ("Board Members", "Acceptance Criteria").
  Rarely "you" ("Drag to reorder states. States define the columns on your kanban board.").
- **Casing:**
  - Buttons & nav: **Title Case** or single Capitalized word — "New ticket", "Add Member", "Back to Board", "Boards", "Logout".
  - Column / table headers & eyebrows: **UPPERCASE**, letter-spaced — "BACKLOG", "TRANSITION", "ACTOR", "ROLE", "SHA", "MESSAGE", "AUTHOR".
  - State & role names in data: **lowercase snake_case**, monospace — `in_progress`, `backend_dev`, `to_do`.
- **Monospace is semantic:** anything machine-derived is mono — ticket keys (`PH-167`),
  short SHAs (`6927b8de`), branch names (`ph-167-branch-graph-ux-rework`), state ids,
  permissions, tokens, file paths, diff bodies, agent ids (`jarwis-pm`).
- **Numbers:** bare counts in chips and badges — column counts ("5"), unread bell ("2"),
  "N states", "12 members", "8 perms", "+1020 −306".
- **Relative time, abbreviated:** "now", "6m", "6h", "13d", "45s ago", "11m ago".
- **Empty states are one calm word/short phrase:** "No tickets", "Empty", "—". Never cute.
- **No emoji.** Iconography is line-icons only (see ICONOGRAPHY). Status is conveyed by
  colored dots/pills, not emoji.
- **The original product ships Turkish microcopy in places** ("Yeni ticket", "Giriş yap",
  "Vazgeç", "Oluştur", "Düzenle", "Boş"). For this English-language design system the
  recreations use the English equivalents (New ticket, Log in, Cancel, Create, Edit,
  Empty) — keep that mapping if localizing.

**Examples (verbatim register):**
- Title: `Branch graph UX rework — SourceTree-style list + commit→diff + remove demo link`
- Meta line: `high · 6m · jarwis-pm`
- Diff header: `6927b8de2773 · 17 files changed · +1020 −306`
- Helper: `Each row is a workflow transition; each column is a board role. Check a cell to allow that role to perform the transition.`

---

## VISUAL FOUNDATIONS

The motifs that make a screen read as ProjectHub.

### Palette & vibe
- **Near-black, layered.** Canvas is `#05070A`; surfaces step up in tiny increments
  (`#0A0E14` cards → `#111722` raised/hover → `#070B11` inset wells). The depth comes from
  **value steps + hairlines**, not big shadows.
- **Cyan is an accent, never a flood.** `#22D3EE` appears only on: primary actions, focus
  rings, links, selection, active tab underline, live "new commit" glow, key data
  highlights, and the active/selected row tint. Easily 90 %+ of any screen is grayscale-on-black.
- **Cool, electric, calm.** No warm neutrals. Semantic hues (success/warning/danger/info)
  are desaturated for dark and used sparingly as dots, pills, and chip tints.

### Type
- **Inter** for all UI text; **JetBrains Mono** for machine data. Dense scale — UI body is
  13–14px, headers 18–28px, page titles 28px. Uppercase eyebrows are 12px / 600 / +0.04em.

### Borders, cards & elevation
- **1px hairlines do the structural work.** Default `#1B2430`; on interactive surfaces a
  faint cyan-tinted hairline `rgba(34,211,238,.18)`. Cards are `bg-surface` + 1px hairline +
  `radius-lg (14px)` with only a whisper of shadow (`shadow-sm/md`). Corners: controls 6–10px,
  cards 14px, modals 20px, pills/chips fully rounded.
- **Glow is reserved.** The cyan glow token (`0 0 0 1px rgba(34,211,238,.35), 0 0 22px -6px rgba(34,211,238,.55)`)
  appears on primary buttons, the selected branch-graph commit, hovered board cards
  (border-beam), and live indicators — not on ordinary cards.
- **Glassmorphism on overlays only.** Modals/popovers sit on a `rgba(5,7,10,.72)` scrim with
  `backdrop-filter: blur(12px)` and a cyan-tinted hairline; never on inline content.

### Backgrounds & texture
- Flat near-black; **no photography, no illustration**. Optional faint radial spotlight /
  cyan glow behind hero areas and the ⌘K menu. A subtle dotted grid backs the workflow
  editor canvas. No gradients as fills (avoid the bluish-purple-gradient trope) — gradients
  appear only as thin animated **border beams** and lane strokes.

### Motion
- **Fast, calm, no bounce.** 120–200ms, `ease-out`. Hover = subtle lift + glow/brighten;
  press = brief darken (no large scale). Live data: a soft **pulse** on agent-phase
  indicators and a one-shot cyan **glow-in** when a new commit arrives at the top of the list.
  Tabs use an animated sliding underline. Respect `prefers-reduced-motion`.

### States (interactive)
- **Hover:** brighten surface one step (`bg-raised`) and/or cyan-tinted hairline; links →
  `--accent-hover`.
- **Active/selected:** `--accent-soft` background tint + cyan hairline + cyan text/icon.
- **Focus:** 2px cyan focus ring offset from the canvas (`0 0 0 2px bg, 0 0 0 4px ring`).
- **Pressed:** darken solid fills to `--accent-active`.
- **Disabled:** 50 % opacity, no pointer events.

### Layout
- Sticky top bar (56–64px) over the canvas; content max-width ~1400px, generous gutters.
- Dense data views (Kanban, Branch Graph, tables, matrices) go edge-to-edge with sticky
  headers/first-columns. Right-hand detail sidebars are ~320px cards.

---

## ICONOGRAPHY

- **System:** [**Lucide**](https://lucide.dev) line icons — the original product imports
  `lucide-react` (e.g. `Activity`, `Wifi`, `Bell`, `Settings`, `GitBranch`, `Users`,
  `Plus`, `X`, `ArrowLeft`, `ArrowRight`, `Check`, `ChevronDown`, `Trash2`, `UserPlus`,
  `Sun`/`Moon`). Keep this set.
- **Style:** 1.5–2px stroke, no fill, rounded joins, currentColor. Icons inherit text color
  and sit at 14–18px inline; cyan only when the element is active/primary.
- **Delivery:** linked from CDN — `https://unpkg.com/lucide@latest` (or `lucide-react` in
  React). No icon font, no sprite sheet in the original. **This is the original product's own
  set, not a substitution.**
- **Emoji:** **never** used. Status and category are shown with colored dots, pills, and
  chips. Unicode arrows (`→`) appear inside text for transitions (`backlog → to_do`) and
  the "move to →" control, rendered in the UI font.
- **The wordmark** "ProjectHub" is **set type, not a logomark** — Inter 700, white, with the
  "Hub" able to take a cyan tint. See `assets/`.

---

## Index — what's in this folder

| File / folder | What it is |
|---|---|
| `README.md` | This file — product context, content & visual foundations, iconography, index. |
| `colors_and_type.css` | All design tokens as CSS variables (color, type, spacing, radii, shadow/glow, motion) + semantic element defaults. Self-hosts **Inter** from `fonts/`; loads JetBrains Mono from Google Fonts. Includes a **light variant** (`html.light` → "Cyan on White"). **Copy-pasteable.** |
| `tailwind.config.snippet.js` | Tailwind `theme.extend` mirror of the tokens. |
| `SKILL.md` | Agent-Skill manifest so this system works as a Claude Code skill. |
| `fonts/` | Self-hosted **Inter** TTFs (UI sans). |
| `assets/` | Wordmark SVG + role/state swatch references. |
| `preview/` | Small HTML specimen cards that populate the Design System tab. |
| `ui_kits/projecthub/` | High-fidelity, interactive recreation of the product in the new theme — components + assembled screens. Start at `ui_kits/projecthub/index.html`. |

### Quick start
1. Link the fonts + tokens: `colors_and_type.css` (it self-hosts Inter from `fonts/` and loads JetBrains Mono from Google Fonts).
2. Reach for tokens via `var(--accent)`, `var(--bg-surface)`, etc. — never hard-code hexes.
3. For components and full screens, copy patterns from `ui_kits/projecthub/`.
