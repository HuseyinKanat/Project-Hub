# ProjectHub — Google Stitch Redesign Kit

Bu klasör, ProjectHub UI'ını **Google Stitch** ([stitch.withgoogle.com](https://stitch.withgoogle.com)) ile yeniden tasarlamak için hazırlanmıştır. Her ekran için:

1. `screenshots/` altında **mevcut halinin ekran görüntüsü** (referans / "before")
2. O ekranın **amacını** (ne işe yarar, kim kullanır) ve
3. Stitch'e yapıştırılabilir **hazır prompt** (İngilizce — Stitch İngilizce promptlarda daha iyi sonuç verir)

içeren bir markdown dosyası var.

## Nasıl kullanılır (Stitch workflow)

1. [stitch.withgoogle.com](https://stitch.withgoogle.com)'a gir, yeni proje aç.
2. Aşağıdaki **Global Design System** bloğunu projenin "design context"ine bir kez yapıştır (her ekranda tutarlılık için).
3. İlgili ekranın `.md` dosyasındaki **"Stitch Prompt"** bölümünü kopyala.
4. Stitch'e prompt + `screenshots/` altındaki referans görseli birlikte ver ("redesign this screen, keep the information architecture, modernize the visuals").
5. Üretilen tasarımı indir, `design/stitch-redesign/output/<ekran>/` altına koy (öneri).

> İpucu: Stitch'e "before" görselini vermek, bilgi mimarisini (hangi panel nerede) korumasını sağlar. Sadece görsel dili modernleştirmesini istersen bunu prompt'ta açıkça belirt.

---

## Ürün bağlamı (Stitch'e context olarak ver)

**ProjectHub**, çok-agentlı (multi-agent) bir yazılım geliştirme iş akışının **kontrol panelidir**. Jira/Linear + SourceTree + bir CI dashboard'unun kesişimi gibi düşün. Yapay zeka agent'ları (PM, Architect, Backend, Frontend, Reviewer, QA) ticket'lar üzerinde çalışır; insan kullanıcı (Coordinator) bu paneli izleme + yönetme için kullanır.

- **Kullanıcı tipi:** teknik (geliştirici / tech lead). Yoğun bilgi yoğunluğuna (information density) toleranslı, hatta bunu ister.
- **Ton:** profesyonel, sakin, "developer tool" estetiği. Oyuncak gibi değil; SourceTree / Linear / GitHub gibi.
- **Gerçek-zamanlı:** WebSocket ile canlı güncellenir (ticket durum değişimi, yeni commit). "Live / Off" bağlantı rozeti her zaman görünür.

---

## Global Design System (her prompt'ın başına ekle)

```
Design system for all screens (ProjectHub — a developer-facing project & git
orchestration dashboard):

- Aesthetic: clean, dense, professional "developer tool" — think Linear +
  SourceTree + GitHub. Calm, not playful. Information-dense but well-spaced.
- Light AND dark theme (dark is primary). 
  - Light: background slate-50 (#f8fafc), surfaces white, text slate-900.
  - Dark: background slate-900 (#0f172a), surfaces slate-800 (#1e293b),
    borders slate-700, text slate-100.
- Accent color: indigo (#6366f1 / indigo-500/600) — used for active tabs,
  primary actions in dark mode, links, selection highlights.
- Neutral palette: slate (Tailwind slate scale) for everything else.
- Typography: system sans-serif (Inter-like) for UI; monospace (ui-monospace)
  for SHAs, branch names, ticket keys, tokens.
- Components:
  - Cards: rounded-lg, 1px border (slate-200 / slate-700), subtle shadow.
  - Primary button: solid (slate-900 light / indigo-500 dark), rounded-md.
  - Ghost button: transparent, hover slate-100 / slate-700.
  - Inputs: rounded-md, 1px border, focus ring (indigo in dark).
  - Badges/chips: rounded-full, small, used for ticket keys, states, labels.
  - Status pills: green = live/success, yellow = pending/connecting, red = error.
- Top chrome: sticky top bar, 56px tall, max content width ~1280px (max-w-7xl),
  brand "ProjectHub" left, nav + notification bell + theme toggle + logout right.
- Icons: lucide-react line icons.
- Spacing: comfortable but compact; this is a power-user tool, not a marketing site.
```

---

## Ekran haritası (route → dosya)

| # | Ekran | Route | Dosya |
|---|---|---|---|
| 00 | Global Chrome (header / nav / notifications / theme / new-ticket dialog) | (tüm sayfalar) | [00-shared-chrome.md](00-shared-chrome.md) |
| 01 | Login | `/login` | [01-login.md](01-login.md) |
| 02 | Boards (liste) | `/` | [02-boards-list.md](02-boards-list.md) |
| 03 | Board Detail — Kanban | `/boards/:key` | [03-board-kanban.md](03-board-kanban.md) |
| 04 | Board Detail — **Branch Graph (SourceTree-style)** | `/boards/:key#graph` | [04-branch-graph.md](04-branch-graph.md) |
| 05 | Ticket Detail | `/boards/:key/tickets/:ticketKey` | [05-ticket-detail.md](05-ticket-detail.md) |
| 06 | Board Settings — General | `/boards/:key/settings` | [06-settings-general.md](06-settings-general.md) |
| 07 | Board Settings — Workflow (states + visual editor + permissions matrix) | `…/settings` (Workflow tab) | [07-settings-workflow.md](07-settings-workflow.md) |
| 08 | Board Settings — Members | `…/settings` (Members tab) | [08-settings-members.md](08-settings-members.md) |
| 09 | Board Settings — Repository | `…/settings` (Repository tab) | [09-settings-repository.md](09-settings-repository.md) |

> **Branch Graph (04)** bu redesign'ın yıldızı: SourceTree / GitKraken benzeri 3-panelli commit geçmişi. Detaylı yönlendirme o dosyada.

## screenshots/

Ekran görüntüleri `screenshots/` altına `NN-<ekran>.png` adıyla eklenir (ör. `04-branch-graph.png`). Token alındıktan sonra canlı uygulamadan yakalanır.
