# 01 — Login

**Route:** `/login`
**Screenshot:** `screenshots/01-login.png`

## Amaç (ne işe yarar)

Uygulamanın giriş kapısı. Kullanıcı bir **Bearer token** girerek doğrulanır (admin için `ADMIN_PASSWORD`, agent rolleri için kendi token'ları). Token doğrulanırsa Boards sayfasına yönlendirir. Dev modunda hızlı-giriş rol butonları ve admin token ile otomatik giriş vardır.

## Mevcut UI

- Tam ekran ortalanmış tek kart (`max-w-sm`), `slate-50` / `slate-900` zemin.
- Başlık "ProjectHub" + alt açıklama ("Bearer token ile giriş yap").
- Tek `password` tipi token input (monospace), "Giriş yap" primary buton (token boşken disabled).
- Hata durumunda kırmızı uyarı kutusu.
- Dev-only: alt kısımda "Hızlı giriş (dev)" — admin/pm/architect/backend/frontend/reviewer/qa rol butonları.

## Stitch Prompt

```
Design a minimal, centered login screen for "ProjectHub", a developer tool
(dark theme primary, slate + indigo).

A single centered card (max ~380px wide) on a plain slate background:
- Title "ProjectHub" (semibold), subtitle "Sign in with a bearer token".
- One labeled input "Bearer token", type=password, monospace font, placeholder
  dots. Helper text noting that admins use their ADMIN_PASSWORD.
- Full-width primary "Sign in" button (disabled look when empty).
- An inline error state variant: a red-tinted alert box above the button reading
  "Token rejected — check your credentials".
- Below a thin divider, a small "Quick dev login" row of tiny chip buttons:
  admin, pm, architect, backend, frontend, reviewer, qa (only shown in dev).

Keep it calm and secure-feeling — no marketing imagery, no social logins.
Just the card. Show both light and dark variants.
```

## İyileştirme yönü (öneri)

- Token girişini "API key" gibi sunan, kopyala-yapıştır dostu bir alan + göster/gizle (eye) toggle.
- Rol butonlarını renk-kodlu rozetlerle (her rolün kendi rengi) göstermek.
- Sol tarafta ürünü anlatan ince bir brand paneli (split layout) — opsiyonel, daha "ürün" hissi için.
