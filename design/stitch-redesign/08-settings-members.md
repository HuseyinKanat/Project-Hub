# 08 — Board Settings · Members

**Route:** `/boards/:boardKey/settings` → **Members** sekmesi
**Screenshot:** `screenshots/08-settings-members.png`, `screenshots/08-add-member-modal.png`

## Amaç (ne işe yarar)

Board'a erişimi olan **aktörlerin** (insan kullanıcılar + `jarwis-*` agent aktörleri) ve **rollerinin** yönetimi. Kim hangi rolde (admin / pm / architect / backend / frontend / reviewer / qa / orchestrator), kim ne yapabilir. Yeni üye ekleme, rol değiştirme, çıkarma.

## Mevcut UI

- "Board Members" başlığı.
- **MembersTab**: üye listesi; her satır (`MembershipRow`): aktör adı (display_name, ör. `jarwis-backend`) + tip (human/agent) + **rol seçici** (dropdown) + kaldır aksiyonu.
- **AddMemberModal**: aktör ara/seç + rol ata + ekle.

## Stitch Prompt

```
Design the "Members" settings panel for ProjectHub (developer dashboard, dark
primary, slate + indigo). Members are humans AND AI agent actors (named like
"jarwis-backend", "jarwis-qa").

Within the Board Settings shell (tabs: General | Workflow | Members[active] |
Repository):

- Section heading "Board Members" with an "Add member" primary button on the
  right.
- A members table/list. Each row:
  - Avatar + display name (monospace for agent actors, e.g. "jarwis-architect"),
    with a small type tag distinguishing "Human" vs "Agent" (agent gets a robot
    icon + indigo tag).
  - A ROLE selector dropdown showing the current role chip (admin, pm, architect,
    backend, frontend, reviewer, qa, orchestrator) — each role color-coded.
  - A secondary email/handle line (muted).
  - A remove (X / trash) action, with a confirm.
- Group or sort so agent actors and humans are visually distinguishable.

Add-member MODAL: search/select an actor (typeahead list with avatars), pick a
role (the same color-coded role chips), and an "Add to board" primary button.

Style: clean roster/admin table like GitHub org members or Linear team settings.
Color-code roles consistently with the rest of the app. Light + dark variants.
```

## İyileştirme yönü (öneri)

- Rolleri tutarlı **renk paleti** ile her yerde (Kanban assignee, ticket meta, branch graph author) eşleştir.
- "Son aktif", "aktif claim sayısı", "bu board'da N ticket" gibi agent telemetri sütunları.
- Toplu rol değişimi, davet linki, rol açıklaması tooltip'i (bu rol neler yapabilir → permissions matrisine link).
