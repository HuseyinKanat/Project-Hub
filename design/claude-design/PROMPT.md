# ProjectHub — Claude Design Prompt Kit (Cyan / Black, 21st.dev-style)

Bu dosya, **Claude design**'a (claude.ai → Artifacts / design) yapıştırıp ProjectHub için
**cyan-on-black**, modern, 21st.dev component diline uygun bir **design system** üretmen için
hazırlanmış promptları içerir.

## Nasıl kullanılır
1. `claude.ai`'de yeni sohbet → aşağıdaki **MASTER PROMPT**'u yapıştır → çalıştır.
   Çıktı: tek sayfalık, interaktif bir **Design System showcase** artifact'ı (tokens + component
   galerisi + 2 assembled ekran), koyu/cyan.
2. Sistem oturduktan sonra **FOLLOW-UP PROMPT**'ları sırayla çalıştır (her biri bir ekranı/modülü
   sisteme sadık kalarak üretir). Tek artifact her şeyi taşıyamaz; modül modül büyüt.
3. Yön vermek istersen **TOKEN CHEAT-SHEET** ve **21st.dev SHOPPING LIST** bölümlerinden
   değer/komponent adı kopyalayıp prompt'a ekle.

> İpucu: Her prompt'ın sonuna "keep it dark by default, cyan accent used with restraint,
> WCAG AA contrast, monospace for SHAs/keys" gibi kısıtları sabit tut.

---

## ⭐ MASTER PROMPT (kopyala-yapıştır)

```
You are a senior product designer + design engineer. Build a complete, cohesive,
production-grade DESIGN SYSTEM for a product called "ProjectHub", delivered as a
single interactive React + Tailwind artifact (a living style guide). Dark by default.

PRODUCT CONTEXT
ProjectHub is a power-user, developer-facing control panel for a MULTI-AGENT software
development workflow. Think "Linear × SourceTree × a CI dashboard". AI agents (PM,
Architect, Backend, Frontend, Reviewer, QA) work on tickets through a state machine;
a human Coordinator monitors and drives everything in real time (WebSocket-live).
It contains: a Boards list, a Kanban board, a SourceTree-style git Branch Graph,
Ticket detail pages, and Board Settings (workflow editor, permissions matrix, members,
repository). The audience is technical; high information density is welcome.

ART DIRECTION — "Cyan on Black", modern, premium, calm-but-electric
- Mood: deep near-black canvas, layered dark surfaces, a single ELECTRIC CYAN accent
  with restrained neon glow. Glassmorphism on overlays. Fine 1px hairline borders with
  faint cyan tint. Subtle gradient/spotlight/glow on interactive surfaces. Feels like
  Linear + Vercel + Raycast + Resend, with the animated, gradient-bordered, glowing
  component language of 21st.dev / Magic UI.
- Cyan is an ACCENT, not a flood: primary actions, active states, focus rings, links,
  selection, live indicators, key data highlights. Most of the UI is grayscale-on-black.
- Use SVG/CSS/gradients for all assets (no external images). Monospace for SHAs,
  branch names, ticket keys, tokens. Smooth, fast micro-interactions (120–200ms).

DELIVERABLE — one scrollable "ProjectHub Design System" page with these sections:
1) FOUNDATIONS
   - Color system: define exact tokens as CSS variables AND a Tailwind theme extension.
     Layered backgrounds (bg-base #05070A → surfaces), border/hairline, text
     primary/secondary/muted, ELECTRIC CYAN accent (+ hover/active/subtle/glow),
     and semantic success/warning/danger/info tuned for dark. Show swatches with hex.
   - Also define a 6-hue "git lane palette" (cyan, teal, sky, violet, amber, rose,
     emerald…) used by the branch graph — show the swatches.
   - Typography scale (UI sans like Inter/Geist; mono like JetBrains/Geist Mono),
     spacing scale, radii, elevation/shadow + cyan glow tokens, motion tokens.
2) CORE COMPONENTS (cyan-black, with states: default/hover/active/focus/disabled)
   Buttons (primary glowing/shimmer, secondary, ghost, destructive, icon), inputs +
   selects + textarea, checkbox/switch/radio, badges & chips (incl. monospace key chip
   and color-coded ROLE chips: admin/pm/architect/backend/frontend/reviewer/qa), tabs
   (animated underline), tooltip, dropdown menu, dialog/modal (glass), toast/sonner,
   avatar, table (sticky header), skeleton/shimmer loaders, status pills
   (green "Live" / amber "Connecting" / red "Off"), empty states, a ⌘K command menu.
3) PRODUCT MODULES (assembled from the components above)
   - App shell: sticky top bar (wordmark, nav, ⌘K, notification bell w/ count, theme
     toggle, avatar) over the dark canvas.
   - Board card (for the Boards grid) with a subtle glow/border-beam hover.
   - Kanban column + Ticket card (monospace key, type/priority/label chips, assignee
     avatar, and a small live "agent phase" pulsing indicator).
   - SourceTree-style Branch Graph ROW: a colored lane gutter (vertical lines + commit
     dot, merge curves, faint pass-through lanes from the lane palette; new commits get
     a cyan glow), short SHA, message, ref/branch chips, clickable ticket-key chips,
     author, relative time. Plus the branch sidebar item and the commit diff panel header.
   - Workflow state-machine node + edge (for a visual editor), and one permissions-matrix
     row (role × action checkboxes).
4) TWO ASSEMBLED SCREENS to prove the system end-to-end, rendered in the new theme:
   (a) the Kanban board, and (b) the SourceTree-style Branch Graph (3 panes:
   branch sidebar | commit list with lane gutter | commit diff panel).

CONSTRAINTS
- Dark-first; ensure AA contrast on near-black. Cyan accent with restraint.
- Output the design tokens as copy-pasteable CSS variables + a tailwind.config snippet.
- Keep everything in ONE self-contained artifact (inline styles/Tailwind, lucide icons,
  no external assets). Make it interactive (hovers, the ⌘K menu, tab switching).
Start by proposing the token values, then render the full page.
```

---

## FOLLOW-UP PROMPTS (sistem oturduktan sonra, sırayla)

> Her birinin başına şu satırı ekle: *"Using the exact design tokens and components from
> the ProjectHub design system we just built (cyan-on-black), produce a new artifact for:"*

**A — App shell + Boards**
```
…the Boards landing page: the sticky top bar (wordmark "ProjectHub", nav, ⌘K, bell,
theme, avatar) and a responsive bento-style grid of Board cards. Each card: monospace
KEY badge, board name, 2-line description, meta chips (project type, "N states", open
ticket count), a live-activity dot, and a border-beam/glow on hover. Include empty +
loading-skeleton variants. Dark, cyan accent, AA contrast.
```

**B — Branch Graph (hero, SourceTree-style)**
```
…the Branch Graph view, 3-pane, full height: (1) branch sidebar — "All" + branch rows
with lane-color dots, default branch gets a "HEAD" tag, selected row cyan-tinted;
(2) commit list — dense ~36px rows, each with an SVG LANE GUTTER (colored vertical
lines + commit dot, merge curves, faint pass-through lanes; a "just arrived" row glows
cyan), then short SHA (mono), message (truncate), branch/HEAD ref chips, clickable
ticket-key chips (e.g. PH-167), author, relative time; selected row cyan background;
(3) a commit diff panel (mono SHA + summary + close, then a file-by-file unified diff,
green added / red removed). Add a top toolbar: branch filter, "ticketed commits only",
author filter, refresh. Make it read like a premium desktop git client. Dark + cyan.
```

**C — Ticket detail**
```
…a ticket detail page: header with title, mono key, type chip, and a STATE control
showing the current workflow state as a pill plus a "move to →" menu of allowed
transitions with required-field hints. Main column: markdown Description, role fields
(Technical depth w/ a rendered diagram, Acceptance criteria, Impact analysis, Test plan),
and an Activity section with animated tabs All|Comments|History|Git (count badges) — a
comment composer, an audit timeline, and linked commits/PRs. Right sidebar card:
Priority, Reporter, Assignee (avatar), Labels (chips), Created, a mono Branch row that
opens a diff, and a pulsing live "agent phase" badge. Also design the branch-diff modal
(glass). Dark, cyan accent.
```

**D — Board Settings (workflow + permissions + members + repository)**
```
…the Board Settings screens with a tab strip General|Workflow|Members|Repository:
(1) Workflow — editable state list with color swatches, a node-graph workflow editor
(states as glowing nodes, transitions as directed edges with role + field-gate labels),
and a permissions matrix table (roles × actions, cyan check toggles, sticky header/first
column); (2) Members — a roster table of human + AI agent actors (mono names like
"jarwis-architect", color-coded role chips, type tag Human/Agent) + an "Add member"
modal; (3) Repository — connection status pill, config form (remote URL, default branch,
masked secret), operations (fetch/sync), and rotate-secret + detach confirm modals.
Dark, cyan accent, dense but legible.
```

---

## TOKEN CHEAT-SHEET (öneri başlangıç değerleri — prompt'a ekleyerek yön ver)

Claude'a kendi token'larını ürettir ama istersen bu paleti dayat:

```
/* Backgrounds (near-black, layered) */
--bg-base:    #05070A;   /* page canvas */
--bg-surface: #0A0E14;   /* cards / panels */
--bg-raised:  #111722;   /* popovers / hovered rows */
--bg-inset:   #070B11;   /* wells / code areas */
--hairline:   #1B2430;   /* 1px borders */
--hairline-cyan: rgba(34,211,238,0.18); /* cyan-tinted divider */

/* Text */
--text-primary:   #E6EDF3;
--text-secondary: #9FB0C0;
--text-muted:     #5C6B7A;

/* Accent — ELECTRIC CYAN */
--accent:        #22D3EE;  /* cyan-400 */
--accent-strong: #06B6D4;  /* cyan-500/600 for solid fills */
--accent-soft:   rgba(34,211,238,0.12);  /* tinted bg */
--accent-glow:   0 0 0 1px rgba(34,211,238,.35), 0 0 22px -6px rgba(34,211,238,.55);

/* Semantic (tuned for dark) */
--success:#34D399; --warning:#FBBF24; --danger:#F87171; --info:#38BDF8;

/* Git lane palette (branch graph) */
cyan #22D3EE, teal #2DD4BF, sky #38BDF8, violet #A78BFA, amber #FBBF24,
rose #FB7185, emerald #34D399, indigo #818CF8

/* Type */ UI: Inter / Geist Sans;  Mono: JetBrains Mono / Geist Mono
/* Radius */ sm 6px · md 10px · lg 14px · xl 20px · pill 9999px
/* Motion */ 120–200ms, ease-out; glow/scale on hover, no bounce
```

Rol renkleri (her yerde tutarlı kullan): admin=slate, pm=sky, architect=violet,
backend=emerald, frontend=cyan, reviewer=amber, qa=rose, orchestrator=indigo.

---

## 21st.dev SHOPPING LIST (component dili → ProjectHub kullanımı)

Claude'a "use these 21st.dev / Magic UI style components" diye ver; nereye oturacağını da söyle:

| 21st.dev / Magic UI pattern | ProjectHub'da nerede |
|---|---|
| **Animated Beam** | Branch graph lane'leri / merge eğrileri (cyan-teal akış) |
| **Border Beam / Shine Border** | Seçili/aktif kart, "Live" board kartı hover |
| **Shimmer / Gradient / Glow Button** | Primary CTA ("New ticket", "Connect repo") |
| **Spotlight / Magic Card** | Board kartları, settings panelleri |
| **Bento Grid** | Boards liste düzeni |
| **Animated Tabs (underline)** | Kanban/Branch Graph sekmeleri, Activity filtreleri |
| **Command Menu (⌘K)** | Global ticket/board/commit arama |
| **Dock / Floating bar** | (Opsiyonel) hızlı aksiyon bar |
| **Number Ticker** | Kanban sütun sayaçları, unread badge, commit ahead/behind |
| **Marquee** | (Opsiyonel) canlı aktivite akışı şeridi |
| **Sonner Toast** | İşlem bildirimleri ("Ticket created") |
| **Animated Tooltip / Avatar group** | Assignee/member avatarları |
| **Shimmer Skeleton** | Yükleniyor durumları |
| **Glass Dialog** | Yeni-ticket, branch-diff, add-member modalları |
| **Status Pill / Pulsing Dot** | Live/Connecting/Off, agent "phase" nabzı |

---

## Notlar
- Mevcut ekranların referans görselleri `design/stitch-redesign/screenshots/` altında (bilgi
  mimarisi aynı kalsın istersen Claude'a "before" olarak verebilirsin).
- Gerçek ürün detayları: state'ler `backlog → to_do → in_progress → in_review → in_test → done`;
  ticket key formatı `PH-167`; agent aktörleri `jarwis-pm … jarwis-qa`; canlı WebSocket güncellemeleri.
```
