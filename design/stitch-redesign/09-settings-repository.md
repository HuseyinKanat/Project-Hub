# 09 — Board Settings · Repository

**Route:** `/boards/:boardKey/settings` → **Repository** sekmesi
**Screenshot:** `screenshots/09-settings-repository.png`

## Amaç (ne işe yarar)

Board'ı bir **git deposuna bağlayan** ayarlar. Repo bağlandığında Branch Graph (04) ve ticket'lardaki commit/diff özellikleri çalışır. Bağlantı durumu, kimlik bilgisi (secret/token) yönetimi, senkron/fetch operasyonları ve bağlantıyı kaldırma burada.

## Mevcut UI (repository bileşenleri)

- **RepositoryStatusPanel:** bağlı mı? (connected/disconnected), default branch, son senkron zamanı, remote URL, sağlık göstergesi.
- **RepositoryConfigForm:** remote URL + default branch + kimlik bilgisi (token/secret) girişi; kaydet.
- **RepositoryOperationsPanel:** manuel "fetch / sync now", yeniden tara gibi aksiyonlar.
- **RotateSecretModal:** depo erişim secret'ını döndür (rotate).
- **DetachConfirmModal:** depoyu board'dan ayır (onay isteyen modal).

## Stitch Prompt

```
Design the "Repository" settings panel for ProjectHub (developer dashboard, dark
primary, slate + indigo, monospace for URLs/branches/SHAs). Connecting a git
repo here powers the board's Branch Graph and per-ticket commit/diff features.

Within the Board Settings shell (tabs: General | Workflow | Members |
Repository[active]):

1) Repository STATUS card: a prominent connection state (green "Connected" /
   grey "Not connected" pill), the remote URL (monospace, truncated, copyable),
   default branch chip, last-sync relative time, and a small health/heartbeat
   indicator. When connected, show counts (branches, commits indexed).

2) Repository CONFIG form: labeled inputs for Remote URL, Default branch, and an
   auth credential field (token/secret, masked, with show/hide). Helper text on
   each. A primary "Save / Connect" button.

3) Repository OPERATIONS card: action buttons — "Fetch now / Sync", "Re-scan
   history", each with a last-run timestamp and a spinner/disabled busy state.

Also design two modals:
- "Rotate secret" modal: explains rotation, shows a masked old secret, a
  generate/paste new secret field, and confirm.
- "Detach repository" CONFIRM modal (subtle red): warns that branch graph and
  diffs will stop working, requires confirm.

Style: like a CI/integration settings page (GitHub repo settings / Vercel git
integration). Secure-feeling, clear status. Light + dark variants.
```

## İyileştirme yönü (öneri)

- Bağlantı durumunu en üstte tek "health banner" olarak (yeşil/kırmızı) + son hata mesajı.
- Webhook/otomatik sync vs manuel fetch ayrımını netleştir; sync log/aktivite akışı.
- Secret yönetimini "API keys" tarzı (oluşturulma tarihi, son kullanım, scope) tabloya çevir.
- "Bağlı değil" durumunda büyük, yönlendirici boş-durum + adım adım bağlama sihirbazı.
