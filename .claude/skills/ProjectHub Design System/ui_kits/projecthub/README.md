# ProjectHub — UI Kit

A high-fidelity, interactive recreation of the ProjectHub product in the **Cyan on Black**
design system. Open `index.html` and click through it: it boots on the Boards grid, opens a
board into the **Kanban** and **Branch Graph** tabs, drills into **Ticket detail**, and the
**Board Settings** (Workflow editor · Permissions matrix · Members · Repository).

These are cosmetic recreations driven by mock data — not production code. They exist so you
can assemble on-brand ProjectHub screens fast.

## Run
Open `index.html` directly (no build step). React 18 + Babel-in-browser + Lucide are loaded
from CDN; tokens are self-hosted (`colors_and_type.css` self-hosts Inter from `../../fonts/`,
JetBrains Mono from Google Fonts).

## Interactions to try
- **⌘K / Ctrl-K** — global command menu (search tickets, boards, commits; ↑↓ + Enter).
- **Boards grid** — hover a card for the border-beam; click to open.
- **Kanban ↔ Branch Graph** — animated underline tabs on the board.
- **Branch Graph** — click a branch to filter, click any commit row to mount the diff panel
  (the list auto-compacts to keep messages readable). The top "live" commit glows in.
- **Ticket detail** — the state control opens a "Move to →" menu of allowed transitions.
- **Board Settings → Members** — "Add Member" opens a glass modal; submitting fires a toast.
- **New ticket** — glass modal from the board header.
- Notification bell opens the panel; `2` unread.

## Files
| File | Contents |
|---|---|
| `index.html` | Loads React/Babel/Lucide + every module, mounts `#root`. |
| `data.js` | Mock domain data (boards, tickets, commits, members, transitions, lane palette) on `window.PH`. |
| `kit.css` | All component styles, built on the design-system tokens. |
| `colors_and_type.css` | Copy of the design-system tokens (self-hosted fonts). |
| `primitives.jsx` | `Icon`, `Button`, chips (`KeyChip`/`TypeChip`/`RoleChip`/`LabelChip`), `Priority`, `StatusPill`, `AgentPhase`, `Avatar`, `StatePill`, `Tabs`, `Checkbox`. |
| `shell.jsx` | `TopBar`, `CommandMenu` (⌘K), `NotificationPanel`, `Toasts`. |
| `boards.jsx` | `BoardsPage`, `BoardCard`. |
| `kanban.jsx` | `KanbanBoard`, `KanbanColumn`, `TicketCard`. |
| `branchgraph.jsx` | `BranchGraphPage` — branch sidebar, SVG lane gutter commit list, diff panel. |
| `ticketdetail.jsx` | `TicketDetail` — header + state control, role fields, activity tabs, sidebar. |
| `settings.jsx` | `BoardSettings` — workflow states, visual node editor, permissions matrix, members, repository, add-member modal. |
| `app.jsx` | `App` — view routing, ⌘K wiring, new-ticket modal, toasts. |

> Each `.jsx` runs in its own Babel scope and exports its components to `window` at the
> bottom of the file. Keep that pattern when adding modules. Never name a shared object
> `styles` — give it a component-specific name to avoid global collisions.
