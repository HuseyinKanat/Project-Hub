# 07 — Board Settings · Workflow (States + Visual Editor + Permissions Matrix)

**Route:** `/boards/:boardKey/settings` → **Workflow** sekmesi
**Screenshot:** `screenshots/07-settings-workflow.png`, `screenshots/07-permissions-matrix.png`

## Amaç (ne işe yarar)

Board'ın **iş akışını (state machine)** tanımlayan en yoğun ayar ekranı. Hangi durumlar var (backlog, to_do, …, done), hangi geçişlere izin var, her geçişi hangi rol yapabilir ve hangi alanlar zorunlu ("field gates"). Ayrıca durumları görsel bir graph editöründe düzenleme ve **rol × aksiyon permission matrisi**.

## Mevcut UI (tek panelde birkaç bölüm)

1. **Workflows** (`WorkflowList`): board'ın workflow tanımları listesi/seçimi.
2. **Workflow States** (`WorkflowStateList`): durum listesi; yeni durum ekle (isim + renk seçici); renk/isim düzenle, sırala, sil.
3. **Visual Workflow Editor** (`WorkflowEditor`, `@xyflow/react`): durumlar düğüm (node), geçişler kenar (edge) olarak sürükle-bırak graph. Node/edge seçilince sağda **NodePropertyPanel / EdgePropertyPanel** (renk, geçiş kuralları, izinli roller, field gates).
4. **Permissions Matrix** (`PermissionMatrix`): rol × izin (transition/field/comment vb.) tablosu; hücrelerde toggle.

## Stitch Prompt

```
Design the "Workflow" settings panel for ProjectHub (developer dashboard, dark
primary, slate + indigo). This is the densest config screen — it defines a
ticket state machine, transitions, role permissions and required-field gates.

Within the Board Settings shell (tabs: General | Workflow[active] | Members |
Repository), stack these sections:

1) "Workflows" — a compact list/selector of workflow definitions for this board
   (name, active toggle, default badge).

2) "Workflow States" — an editable list of states. Each row: a drag handle, a
   color swatch, the state name (e.g. "in_review"), and edit/delete actions.
   An "Add state" inline form: name input + a color picker swatch row + add
   button.

3) "Visual Workflow Editor" — a node-graph canvas (like a flow editor): each
   state is a rounded node colored by its swatch; transitions are directed edges
   with arrowheads and labels. A node/edge is selected, opening a right-side
   PROPERTY PANEL showing: for a transition — "from → to", allowed roles
   (multi-select chips: pm, architect, backend, frontend, reviewer, qa),
   and required fields / field-gates (chips like "test_plan", "technical_depth").
   Include zoom/fit controls and a minimap.

4) "Permissions Matrix" — a table: rows = roles (admin, pm, architect, backend,
   frontend, reviewer, qa), columns = actions/permissions (e.g. create, comment,
   transition:*, field:edit, claim, delete). Cells are checkbox/toggle states
   (granted = indigo check, denied = empty, wildcard = filled). Sticky header
   row and sticky first column for scanning a large grid.

Make it feel like a powerful but legible admin/config surface (think GitHub
branch-protection + a flow editor). Light + dark variants.
```

## İyileştirme yönü (öneri)

- 4 bölümü tek upuzun panel yerine **alt-sekmeler** (States · Editor · Permissions) ya da accordion'a böl — şu an çok uzun.
- Visual editor ile state list'i **çift-yönlü senkron** ve "geçersiz state machine" (ör. done'a ulaşılamıyor) uyarıları.
- Permissions matrisinde rol grupları, "kopyala from role", diff vurgusu (default'tan sapma), arama/filtre.
- Field-gate'leri geçiş kenarının üzerinde küçük kilit ikonuyla göster.
