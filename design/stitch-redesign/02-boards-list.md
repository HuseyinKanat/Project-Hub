# 02 — Boards (Liste)

**Route:** `/` (index)
**Screenshot:** `screenshots/02-boards-list.png`

## Amaç (ne işe yarar)

Kullanıcının erişebildiği tüm **board'ların** (proje panolarının) listesi — uygulamanın ana giriş ekranı. Her board bir projeyi temsil eder (ör. "PH" = ProjectHub'ın kendisi). Bir board'a tıklayınca o board'ın Kanban + Branch Graph görünümüne gidilir.

## Mevcut UI

- Başlık "Boards".
- Responsive grid (1 / 2 / 3 sütun) board kartları.
- Her kart: üstte board **key** rozeti (`PH` — koyu/indigo zemin) + sağ ok ikonu; board adı (başlık); açıklama (2 satır clamp); altta meta chip'leri: `project_type` ve `<n> states`.
- Boş durum: "Henüz board yok. Backend bootstrap çalıştırdın mı?" kartı.
- Yükleniyor / hata durumları.

## Stitch Prompt

```
Design a "Boards" index/landing screen for ProjectHub (developer dashboard,
dark theme primary, slate + indigo).

Header: page title "Boards" (left). Optionally a "New board" primary button on
the right.

Body: a responsive grid of board cards (1 col mobile, 2 tablet, 3 desktop).
Each card (rounded-lg, bordered, hover lifts the border to indigo):
- Top row: a small monospace KEY badge (e.g. "PH") on a dark/indigo chip, and a
  right-arrow icon that nudges right on hover.
- Board name (semibold, ~16px).
- A 2-line truncated description.
- Bottom row of small rounded-full meta chips: project type (e.g. "web") and a
  state count (e.g. "6 states"). Optionally add chips for open ticket count and
  a live-activity dot.

Include an empty-state card variant ("No boards yet — run backend bootstrap")
and a subtle loading skeleton variant.

Style: calm, dense, professional. Cards should feel scannable like a Linear/
GitHub project list. Show light and dark variants.
```

## İyileştirme yönü (öneri)

- Karta canlı metrik ekle: açık ticket sayısı, son aktivite zamanı, WIP/blocked rozetleri, küçük durum-dağılımı bar'ı (kaç ticket hangi state'te).
- Arama / filtre çubuğu + "yıldızla/pinle" ile sık kullanılan board'ları yukarı al.
- Board kartında küçük bir branch/commit nabız göstergesi (sparkline).
