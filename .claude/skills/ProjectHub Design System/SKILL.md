---
name: projecthub-design
description: Use this skill to generate well-branded interfaces and assets for ProjectHub, either for production or throwaway prototypes/mocks/etc. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping. ProjectHub is a dark, "Cyan on Black", developer-facing control panel for a multi-agent software-development workflow (Linear × SourceTree × a CI dashboard).
user-invocable: true
---

Read the `README.md` file within this skill, and explore the other available files.

If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets out and
create static HTML files for the user to view. If working on production code, you can copy
assets and read the rules here to become an expert in designing with this brand.

If the user invokes this skill without any other guidance, ask them what they want to build or
design, ask some questions, and act as an expert designer who outputs HTML artifacts _or_
production code, depending on the need.

## Where things are
- `README.md` — product context, content & visual foundations, iconography, file index. **Start here.**
- `colors_and_type.css` — all design tokens as CSS variables (self-hosts Inter from `fonts/`; JetBrains Mono via Google Fonts). Reference tokens via `var(--accent)`, `var(--bg-surface)`, etc. — never hard-code hexes.
- `tailwind.config.snippet.js` — the same tokens as a Tailwind `theme.extend`.
- `fonts/` — Inter TTFs (self-hosted).
- `assets/` — wordmark.
- `preview/` — small specimen cards (colors, type, spacing, components) — good visual reference.
- `ui_kits/projecthub/` — interactive recreation of the product (components + assembled screens). Copy components from here.

## Non-negotiables
- **Dark-first.** Canvas `#05070A`; layer surfaces up in small value steps; structure with 1px hairlines (use a slightly brighter `#283342` so surfaces don't vanish on near-black).
- **Cyan is an accent, not a flood** (`#22D3EE`): primary actions, focus rings, links, selection, live indicators, active states, key data — and little else. ~90% of any screen is grayscale-on-black.
- **Monospace (JetBrains Mono) is semantic:** ticket keys (`PH-167`), SHAs, branch names, state ids, permissions, file paths, agent ids.
- **Reserve the cyan glow** for primary buttons, selected/live items, focus. Glass (blur + scrim) only on overlays.
- **No emoji.** Icons are Lucide line icons. Motion is fast (120–200ms), ease-out, no bounce.
- Copy is terse and technical; buttons are verbs (Title Case), column/table headers are UPPERCASE, data ids are lowercase snake_case.
