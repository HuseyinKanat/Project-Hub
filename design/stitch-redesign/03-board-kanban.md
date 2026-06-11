# 03 — Board Detail · Kanban

**Route:** `/boards/:boardKey` (varsayılan sekme: Kanban)
**Screenshot:** `screenshots/03-board-kanban.png`

## Amaç (ne işe yarar)

Bir board'ın **iş akışı panosu**. Ticket'lar workflow durumlarına (backlog → to_do → in_progress → in_review → in_test → done) göre sütunlara dağılır. Agent'lar çalıştıkça kartlar **canlı (WebSocket)** olarak sütun değiştirir, vurgulanır. Kullanıcı buradan ticket açar, ticket'a tıklayıp detayına gider, board ayarlarına geçer.

## Mevcut UI

- **Header:** geri linki ("← Boards"), board adı + açıklama; sağda **canlı bağlantı rozeti** (Wifi ikonu, "Live"/"..."/"Off" — yeşil/sarı/kırmızı), **"Yeni ticket"** primary buton, **Settings** ghost buton, board **key** rozeti.
- **Sekme şeridi:** `Kanban | Branch Graph` (aktif sekme alt-border indigo).
- **Kanban panel:** yatay kaydırılabilir sütunlar (`auto-cols 14–16rem`). Her sütun: durum adı (UPPERCASE) + adet rozeti; içinde ticket kartları; boş sütunda kesik çizgili "Boş" placeholder.
- **Ticket kartı (`TicketCard`):** key + başlık + tip/öncelik/label rozetleri + assignee; canlı güncellemede kısa highlight animasyonu.
- Sağ üstte geçici **success toast** (yeşil).

## Stitch Prompt

```
Design a Kanban board view for ProjectHub (developer dashboard, dark primary,
slate + indigo). This is a real-time, agent-driven board.

Header bar:
- Left: a back link "← Boards", the board name (h1) with a one-line description.
- Right cluster: a LIVE connection pill (green wifi icon + "Live"; also show
  yellow "Connecting…" and red "Off" variants), a primary "New ticket" button
  with a plus icon, a ghost "Settings" button with a gear icon, and a monospace
  board-key badge (e.g. "PH").

Below the header: a tab strip with two tabs — "Kanban" (active) and
"Branch Graph". Active tab has an indigo underline.

Kanban panel: horizontally scrollable columns (each ~240px wide). Each column:
- A header with the workflow state name in uppercase tracking-wide (e.g.
  "IN PROGRESS") and a small rounded count badge.
- A vertical stack of ticket cards.
- A dashed empty-state placeholder when a column has no tickets.
Use a subtle per-column tint/left-accent keyed to the state (backlog grey,
in-progress blue, in-review purple, in-test amber, done green).

Ticket card: a monospace ticket key (e.g. "PH-167"), a 2-line title, a row of
small chips (type: feature/bug; priority; labels), and an assignee avatar/name.
Show one card in a "just updated" highlighted state (brief indigo/amber ring) to
convey live updates.

Also show a transient success toast (green, top-right): "Ticket created".

Keep it dense and scannable like Linear/Jira but cleaner. Light + dark variants.
```

## İyileştirme yönü (öneri)

- Sütun başlıklarına WIP limiti + limit aşımında kırmızı uyarı.
- Kart üzerinde agent "phase" canlı göstergesi (planning/coding/testing nabzı) ve stale-claim uyarı rozeti.
- Sürükle-bırak için daha belirgin drop-zone; assignee avatar grupları; ticket tipine göre sol renk şeridi.
- Filtre/araç çubuğu: assignee, label, tip; "sadece blocked", "sadece bana atanan".
