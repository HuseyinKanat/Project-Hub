# 00 — Global Chrome (Header / Nav / Notifications / Theme / New-Ticket Dialog)

**Route:** tüm authenticated sayfalarda ortak (`Layout.tsx`)
**Screenshot:** `screenshots/00-shared-chrome.png`, `screenshots/00-new-ticket-dialog.png`, `screenshots/00-notifications.png`

## Amaç (ne işe yarar)

Tüm uygulamayı saran kalıcı kabuk. Üstte sticky bir bar; her sayfada görünür. İçinde:
- **Marka** ("ProjectHub") → tıklayınca Boards'a döner.
- **Boards** nav linki.
- **Notification Bell** — okunmamış bildirim sayacı + dropdown (ticket atandı, durum değişti, commit geldi vb.).
- **Theme Toggle** — açık/koyu tema (güneş/ay ikonu).
- **Logout** — token'ı temizler, login'e döner.

Ayrıca her board'da **"Yeni ticket"** açan bir dialog (modal) var: başlık + tip (feature/bug/...) + açıklama ile yeni iş kaydı oluşturur.

## Mevcut UI

- Header: `h-14` (56px), beyaz / `slate-800` zemin, alt border. İçerik `max-w-7xl` ortalı.
- Sağ blok: ghost butonlar, lucide ikonlar. Aktif nav linki `slate-100`/`slate-700` arka planlı.
- Notification dropdown: liste, okunmamışlar vurgulu, "tümünü okundu işaretle".
- New Ticket Dialog: ortalanmış modal, başlık input, tip seçici, markdown açıklama, "Oluştur" primary buton.

## Stitch Prompt

```
Design the persistent top navigation chrome for "ProjectHub", a developer-facing
project + git orchestration dashboard (dark theme primary).

Top bar (sticky, 56px tall, full width with content centered at ~1280px max):
- Left: wordmark "ProjectHub" (semibold).
- Right cluster (icon + label, ghost-button style): 
  1) "Boards" nav link (shows an active/filled state when selected),
  2) a notification bell icon with a small unread-count badge,
  3) a theme toggle (sun/moon),
  4) a "Logout" button with a logout icon.
- 1px bottom border, subtle. Dark surface slate-800 on slate-900 page.

Also design two overlays that live on top of this chrome:

A) Notification dropdown (opens under the bell): a compact card list of
   notifications. Each row: an icon by type (ticket assigned, state changed,
   new commit, comment), a one-line title, a relative timestamp ("3m ago").
   Unread rows have an indigo dot / tinted background. Header has "Notifications"
   and a "Mark all read" text action. Empty state: "You're all caught up".

B) "New Ticket" modal dialog (centered, dimmed backdrop): title input, a
   ticket-type selector (segmented: Feature / Bug / Chore / Refactor), a
   markdown-capable description textarea, and footer with a ghost "Cancel" and a
   solid primary "Create ticket" button. Monospace hint showing the auto board
   key prefix (e.g. "PH-…").

Style: clean developer-tool aesthetic, slate + indigo accent, lucide line icons,
monospace for keys/SHAs. Show both light and dark variants of the top bar.
```

## İyileştirme yönü (öneri)

- Board seçiliyken header'a board key + hızlı board switcher (combobox) eklenebilir.
- Notification bell → grup başlıkları (Today / Earlier) ve filtre (sadece bana atananlar).
- Global komut paleti (⌘K) — ticket/board/commit hızlı arama. Stitch'ten bir ⌘K overlay konsepti de isteyebilirsin.
