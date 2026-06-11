# Using this in Claude Code

This folder **is** a self-contained Agent Skill — `SKILL.md` at the root carries the
required frontmatter (`name: projecthub-design`, `description`, `user-invocable: true`).
Drop it into a Claude Code skills directory and Claude can design on-brand ProjectHub
interfaces and assets on demand.

## Install

**Project-scoped** (available in one repo):
```bash
mkdir -p .claude/skills
cp -R projecthub-design-system .claude/skills/projecthub-design
```

**User-scoped** (available everywhere):
```bash
mkdir -p ~/.claude/skills
cp -R projecthub-design-system ~/.claude/skills/projecthub-design
```

The folder name under `skills/` must match the skill `name` in `SKILL.md`
(`projecthub-design`). Restart Claude Code (or start a new session) so it picks up the skill.

## Use it
- Invoke explicitly: **"use the projecthub-design skill to build a … screen"**, or just ask
  for ProjectHub UI and Claude will discover it from the description.
- Claude reads `README.md` first (product context, content + visual foundations, iconography),
  then pulls tokens from `colors_and_type.css` / `tailwind.config.snippet.js`, fonts from
  `fonts/`, and copies components from `ui_kits/projecthub/`.

## What's inside (manifest)
| Path | Purpose |
|---|---|
| `SKILL.md` | Agent-Skill manifest (read by Claude Code). |
| `README.md` | Full brand brief: product, content rules, visual foundations, iconography, index. |
| `colors_and_type.css` | All design tokens as CSS variables (self-hosts Inter; JetBrains Mono via Google Fonts). |
| `tailwind.config.snippet.js` | Same tokens as a Tailwind `theme.extend`. |
| `fonts/` | Inter TTFs (self-hosted). |
| `assets/` | Wordmark SVG. |
| `preview/` | Specimen cards (colors, type, spacing, components) for visual reference. |
| `ui_kits/projecthub/` | Interactive product recreation — components + assembled screens to copy from. |

## Notes for the implementer
- These are **design references** (HTML/React-in-Babel prototypes), not production code to
  ship verbatim. Recreate them in your codebase's framework using its established patterns,
  pulling exact values from the tokens.
- **Dark-first, cyan-as-accent.** Reference tokens (`var(--accent)`, `var(--bg-surface)`, …)
  — never hard-code hexes. Keep monospace (JetBrains Mono) for keys/SHAs/branches/ids.
- Icons are **Lucide**; no emoji. Motion is 120–200ms ease-out, no bounce.
