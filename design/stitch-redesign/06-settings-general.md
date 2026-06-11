# 06 — Board Settings · General

**Route:** `/boards/:boardKey/settings` (General sekmesi — varsayılan)
**Screenshot:** `screenshots/06-settings-general.png`

## Amaç (ne işe yarar)

Board'ın temel kimlik ayarları. Şu an sade: board **adı** (ve ileride açıklama/anahtar/proje tipi gibi temel alanlar) düzenlenir. Settings sayfasının üst seviye sekme yapısının ilk sekmesi: **General | Workflow | Members | Repository**.

## Mevcut UI

- Başlık "Board Settings" + geri linki.
- Sekme şeridi (ikon + etiket): General / Workflow / Members / Repository (aktif sekme vurgulu).
- **General panel:** "General Settings" başlığı; board **Name** input (blur'da kaydeder). Minimal.

## Stitch Prompt

```
Design the "General" settings panel for a board in ProjectHub (developer
dashboard, dark primary, slate + indigo).

Page shell:
- Title "Board Settings" with a back link to the board.
- A horizontal tab strip with icon+label tabs: General (active), Workflow,
  Members, Repository. Active tab underlined indigo.

General panel content (a settings card / form):
- Section heading "General Settings".
- A labeled "Name" text input (auto-saves on blur — show a subtle "saved" check).
- Add sensible additional read-friendly fields for a fuller design: board Key
  (monospace, read-only chip), Description (textarea), Project type (select),
  and a "Default branch" hint. Group them in a clean two-column settings form
  with helper text under each field.
- A "Danger zone" card at the bottom (subtle red border): "Archive board" and
  "Delete board" with confirm affordances.

Style: classic settings form — clear labels, helper text, generous spacing,
save-on-blur. Light + dark variants.
```

## İyileştirme yönü (öneri)

- Sol dikey settings navigasyonu (tab yerine) — daha fazla bölüm eklenince ölçeklenir.
- Her ayar satırına "saved ✓ / saving…" mikro durum; değişiklik geçmişi linki.
- Board avatarı/rengi seçimi (board key rozeti rengini buradan yönet).
