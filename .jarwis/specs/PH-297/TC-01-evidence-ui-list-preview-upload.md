# TC-01: Kanıtlar bölümü — liste/gruplama, png Lightbox, mp4 seek, form upload, WS canlı yenileme, rol-bazlı upload

> StepMethod test case (UseCaseTemplate-StepMethod uyarlaması). Ticket'a
> `add_attachment(kind="testcase")` ile bağlanır; UI'da Test Plan bölümü altında popup render edilir.
> Bir TC tek davranış kümesini sınar; her Expected Response gözlemlenebilir/ölçülebilir.

## Summary

| Item | Description |
|---|---|
| Test Case ID: | TC-PH-297-01 |
| Test Case Name: | Kanıtlar bölümü uçtan uca — run_id gruplu liste, png Lightbox, inline mp4 seek, form upload (progress→201→refetch), WS canlı yenileme, orijinal-ad indirme, write/read-only rol görünürlüğü |
| Description: | Ticket detay sayfasındaki "Kanıtlar / Attachments" bölümünün davranışını kanıtlar: run_id ile gruplu attachment listesi, png Lightbox (focus-trap), `?token=` src'li inline mp4 oynatma + arbitrary seek, form upload (progressbar→POST 201→otomatik refetch), 415 inline reddi, `attachment_added` WS canlı yenileme, orijinal-ad indirme ve write vs read-only rol upload görünürlüğü. iter-1'deki `canUpload` (nested `board.roles.roles`) bug'ı fix sonrası iki rolle doğrulandı. |
| Related Use Case: | UC-PH-297-01 (Ticket kanıt dosyalarını görüntüle/yükle) |
| Related AC: | AC1/AC11 (liste + run_id grup + kind/source TEXT badge), AC2/AC12 (png focus-trapped Lightbox + focus-return), AC3/AC13 (inline mp4 + Range seek), AC4 (orijinal-ad indirme), AC5/AC15/AC17 (upload progress + 201 + refetch + FormData boundary), AC6 (reject inline hata), AC8/AC14 (write/read-only rol upload), AC10/AC16 (WS `attachment_added` canlı yenileme), AC7/AC9 (loading/empty/error + a11y) |
| Type / Priority: | happy_path + negative / P1 (ticket: medium) |
| Actors / Environment: | jarwis-qa (Coordinator browser relay); worktree vite dev — iter-2 (fix sonrası) |
| Test Data: | PH-296 seed attachment'ları; 52.7s mp4; `qa-iter2-form-upload.png`; pdf (415 tetikleyici); jarwis-qa (write) + jarwis-reviewer (read-only) rolleri |
| Pre-Conditions: | PH-296 backend attachment/evidence API'leri deployed; ticket detay sayfası açık; PH-296 seed attachment'ları listelenebilir |
| Post-Conditions: | Upload sonrası liste kalıcı +1 (5→6); mevcut yol regresyonsuz (yalnız additive frontend); backend/şema/migration değişmedi |
| References: | PH-297; ticket `test_plan` (iter-2, 8/8 TC); reviewer bağımsız gate tsc 0 / eslint 0 / node:test 13/13; `.jarwis/logs/PH-297/` |

## Test Steps

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| 1 | TC-B1: Ticket açılınca Kanıtlar kartı render olur | AC1/AC11 — attachment satırları filename + kind/source TEXT badge + size + uploader + date; run_id ile gruplu + grup sayaçları; loading/empty/error state'leri ayrı |
| 2 | TC-B2: png thumbnail aktive edilir (click/keyboard) | AC2/AC12 — focus-trapped Lightbox açılır; Esc kapatır; focus tetikleyici thumbnail'e geri döner |
| 3 | TC-B3: 52.7s mp4'te 8s konumuna seek | AC3/AC13 — inline `<video>` `?token=` src'den oynar; 8s → 9.3s'e ilerledi (backend Range/206 seek çalışır); full-page navigasyon yok |
| 4 | TC-B4: Form upload — dosya seç (DataTransfer inject) + submit | AC5/AC15/AC17 — progressbar `aria-valuenow=100`; POST 201; otomatik refetch; DOM listesi 5→6; `qa-iter2-form-upload.png` listede; multipart FormData boundary korunur (JSON Content-Type gönderilmez) |
| 5 | TC-B6: Başka client MCP ile attachment ekler | AC10/AC16 — Kanıtlar listesi `attachment_added` WS event'iyle manuel reload olmadan canlı yenilenir (4→5) |
| 6 | TC-B7: Attachment download kontrolü aktive edilir | AC4 — `<a>` `download` attr = orijinal ad + download=1; tarayıcı dosyayı orijinal adıyla indirir |
| 7 | TC-B8: Bölüm jarwis-qa (write) vs jarwis-reviewer (read-only) ile render | AC8/AC14 — jarwis-qa'da upload formu GÖRÜNÜR (fix sonrası); jarwis-reviewer'da liste/video okunur ama form YOK |
| 8 | TC-U: Reviewer bağımsız statik/gate re-run | AC7/AC9 gate — tsc 0 / eslint 0 / node:test 13/13 |

## Negative / Alternate Scenarios

### E1 – Allowlist-dışı dosya reddi (415)

| | |
|---|---|
| Branched From: | Test Steps, Step 4 (form upload) |
| Flow Scenario: | E1 – Desteklenmeyen tür (pdf) inject edilip yüklenir |
| Expected Post-Condition: | Inline aria-live "Desteklenmeyen tür" (415 mapping); mevcut liste değişmez; generic toast değil |

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| E1-1 | TC-B5: pdf inject → submit | AC6/AC15 — inline aria-live "Desteklenmeyen tür" (415); mevcut liste değişmedi |

## Execution Record

| Date | Environment | Result | Evidence | Executed By |
|---|---|---|---|---|
| 2026-07-14 (iter-2) | Coordinator browser relay; worktree vite dev (fix sonrası) | PASS | 8/8 TC (B1–B8) + TC-U: mp4 8s→9.3s seek (`?token=` src); form upload progressbar `aria-valuenow=100`→POST 201→DOM 5→6 (`qa-iter2-form-upload.png`); WS `attachment_added` 4→5; pdf→415 inline "Desteklenmeyen tür"; download orijinal-ad + download=1; jarwis-qa form görünür / jarwis-reviewer form yok; reviewer tsc 0 / eslint 0 / node:test 13/13. iter-1 `canUpload` (nested `board.roles.roles`) bug'ı fix'lendi ve iki rolle runtime doğrulandı. Kanıtlar DOM/geometry/network ground-truth (derin-scroll screenshot capture bozuk). | jarwis-qa (Coordinator browser relay) |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (gerçek koşum kayıtlarından) | jarwis-qa |
