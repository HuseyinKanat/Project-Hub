# 05 — Ticket Detail

**Route:** `/boards/:boardKey/tickets/:ticketKey`
**Screenshot:** `screenshots/05-ticket-detail.png`, `screenshots/05-ticket-branch-diff.png`

## Amaç (ne işe yarar)

Tek bir ticket'ın **tüm hikayesi**: tanım, role-özel alanlar (technical_depth, impact_analysis, test_plan vb.), durum geçişleri, yorumlar, git aktivitesi (commit'ler, PR), ve branch diff'i. Hem insanın hem agent'ların okuduğu/yazdığı kayıt. Coordinator buradan durumu doğrular; agent çıktıları (mermaid diyagram, AC, impact analizi) burada görünür.

## Mevcut UI

İki kolon (içerik + sağ meta paneli):

- **Header:** geri linki, ticket **başlığı** (h1), tip/key; durum geçiş kontrolü (state transition; gerekli alan "field gate" doğrulaması ile).
- **Sol/ana kolon:**
  - **Description** (markdown editor).
  - **Tip'e özel alanlar** (`MarkdownFieldEditor`): ör. technical_depth (mermaid render edilir), acceptance_criteria, impact_analysis, test_plan.
  - **Activity** bölümü: filtre sekmeleri **All / Comments / History / Git** (her birinde sayaç). 
    - Comments: yorum listesi + yeni yorum kutusu.
    - History: durum/alan değişim audit log'u (timestamp + actor).
    - Git: ticket'a bağlı commit'ler (`TicketCommits` — SHA, mesaj, zaman) ve PR linkleri.
- **Sağ meta paneli (`card`):** Priority, Reporter, Assignee, Labels, Created; **Branch** satırı (varsa) → tıklayınca **branch diff modal** açılır (default branch'e karşı tüm değişiklik, DiffViewer). Agent phase göstergesi (canlı, nabız). Sil (delete) — sebep soran modal.
- **Canlı bağlantı** rozeti (Wifi).

## Stitch Prompt

```
Design a ticket detail page for ProjectHub (developer dashboard, dark primary,
slate + indigo, monospace for keys/SHAs). A ticket is the single source of truth
that both humans and AI agents read and write.

Two-column layout:

Top header (full width): a back link, the ticket title as an h1, a monospace
ticket key + a type chip (feature/bug), and a STATE control on the right — the
current workflow state as a colored pill plus a "move to →" dropdown of allowed
transitions. Include a small inline note when a transition requires fields
("in_test → done: test_plan ✓"). Also a small live "Live"/"Off" wifi pill.

MAIN column (left, wider):
- "Description" — a markdown-rendered block with an edit affordance.
- A few role-specific markdown fields, each titled, e.g. "Technical depth"
  (which can render a Mermaid diagram), "Acceptance criteria", "Impact analysis",
  "Test plan". Render one of them showing an embedded diagram.
- An "Activity" section with filter tabs: All | Comments | History | Git, each
  with a count badge.
  - Comments: threaded comment list (avatar, author, time, markdown body) and a
    "write a comment" composer at the bottom.
  - History: an audit timeline of state/field changes (actor + timestamp +
    "moved backlog → in_review").
  - Git: a list of commits linked to this ticket (monospace SHA, message,
    relative time) and any linked pull requests with merge status.

RIGHT meta sidebar (card): labeled rows — Priority, Reporter, Assignee (avatar),
Labels (chips), Created date, and a "Branch" row showing a monospace branch name
that opens a diff when clicked. Show a live "agent phase" indicator (a pulsing
"coding…" badge). A subtle "Delete" action at the bottom.

Also design the BRANCH DIFF MODAL: a large centered modal, header with the
monospace branch name vs default branch and a close X, body = a full file-by-file
unified diff (added green / removed red, file path headers, line numbers).

Dense, technical, calm. Light + dark variants.
```

## İyileştirme yönü (öneri)

- Durum geçişini görsel bir **stepper / workflow rail** olarak göster (hangi adımdayız, sırada ne var, hangi alan eksik).
- Activity'de agent handoff'larını ("[HANDOFF architect→backend]") özel kartlarla vurgula; rol renkleri.
- Sağ panelde "linked commits/PR" özet rozetleri + branch ahead/behind.
- Mermaid/diagram alanları için tam-ekran/zoom; field gate eksikliğini inline kırmızı checklist olarak göster.
- Sticky sağ panel + içindekiler (table of contents) uzun ticket'larda.
