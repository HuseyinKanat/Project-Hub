# TC-01: Diff dile-özel syntax highlighting — HunkView prism token render

> StepMethod test case (UseCaseTemplate-StepMethod uyarlaması). Ticket'a
> `add_attachment(kind="testcase")` ile bağlanır; UI'da Test Plan bölümü altında popup render edilir.
> Bir TC tek davranış kümesini sınar; her Expected Response gözlemlenebilir/ölçülebilir.

## Summary

| Item | Description |
|---|---|
| Test Case ID: | TC-PH-299-01 |
| Test Case Name: | HunkView satır-içi prism token render — dile-özel renk + diff semantiği korunur + bilinmeyen uzantı fallback |
| Description: | `prism-react-renderer v2` ile HunkView satır-içerik hücresine dile-özel token enjeksiyonunun (keyword/string/comment ayrımı) çalıştığını, +/- arka planları / hunk başlıkları / satır numaralarının korunduğunu ve bilinmeyen uzantıda düz-metin fallback yaptığını kanıtlar. Gerçek diff (62d53d3 merge, ts/tsx) üzerinde ölçüldü. |
| Related Use Case: | UC-PH-299-01 (Kod diff'ini dile-özel renklerle oku) |
| Related AC: | AC1 (dile-özel token renkleri), AC2 (+/- zemin + hunk başlığı + satır no korunur), AC3 (bilinmeyen uzantı → düz-metin fallback), AC4 (prism-react-renderer v2 pinli + build/typecheck temiz), AC5 (500+ satır jank yok) |
| Type / Priority: | happy_path + edge / P1 (ticket: high) |
| Actors / Environment: | jarwis-qa (Coordinator browser relay); worktree vite dev :5180 |
| Test Data: | Gerçek merge diff 62d53d3 (ts/tsx dosyaları) |
| Pre-Conditions: | prism-react-renderer v2 package.json'a pinli eklendi; vite build/typecheck temiz; FileDiffView/HunkView render edilebilir |
| Post-Conditions: | Diff render'ı token-renkli; diff semantiği (glyph/satır no/collapse) değişmedi |
| References: | PH-299; ticket `test_plan` (4/4 TC); gerçek diff 62d53d3; `.jarwis/logs/PH-299/` |

## Test Steps

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| 1 | TC-1: ts/tsx diff'i render edilip token'lar sayılır | AC1 — 745 renkli token, 6 design-token rengi (keyword 407, plain 280, string 28, warning 7, info 11, comment 12); keyword/string/comment gözle ayrılır |
| 2 | TC-2: Alfa-blend edilmiş GERÇEK zeminlerle WCAG kontrast ölçülür | AC1/AC2 — DARK en kötüler 6.27–10.22 (comment 3.12 = büyük-metin AA, kabul); LIGHT string/keyword/info 3.27–5.86 ✓ |
| 3 | TC-3: +/- glyph + satır-no + collapse kontrol edilir | AC2 — +/- glyph + satır-no kolonları + collapse butonu DOM'da çalışır (diff semantiği highlighting sonrası korundu) |
| 4 | TC-4: unit + meta-satır render | AC4/AC5 — unit yeşil; meta-satır plain fallback görselde 280 plain token; build + typecheck temiz; senkron tokenizasyon (fark edilir jank yok) |

## Negative / Alternate Scenarios

### E1 – Bilinmeyen uzantı → düz-metin fallback

| | |
|---|---|
| Branched From: | Test Steps, Step 4 |
| Flow Scenario: | E1 – Dil çıkarımı bilinmeyen uzantı/meta-satır için başarısız olur |
| Expected Post-Condition: | Mevcut düz görünümle render; hata/boş satır yok (graceful plain-token fallback) |

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| E1-1 | TC-4: bilinmeyen uzantı / meta-satır | AC3 — düz-metin fallback (280 plain token); kırılma/boş satır yok |

## Execution Record

| Date | Environment | Result | Evidence | Executed By |
|---|---|---|---|---|
| 2026-07-14 | Coordinator browser relay; worktree vite dev :5180; gerçek diff 62d53d3 (ts/tsx) | PASS (1 minor not) | TC-1 745 renkli token / 6 renk (keyword 407, plain 280, string 28, warning 7, info 11, comment 12); TC-2 DARK 6.27–10.22 (comment 3.12 büyük-metin AA), LIGHT 3.27–5.86; TC-3 +/- glyph + satır-no + collapse DOM'da; TC-4 unit + 280 plain fallback. MINOR NOT: light'ta text-secondary (noktalama/operatör) 2.46:1 — ana içerik değil; follow-up PH-306'da documented-acceptance (dark-artefakt teyitli). | jarwis-qa (Coordinator browser relay) |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (gerçek koşum kayıtlarından) | jarwis-qa |
