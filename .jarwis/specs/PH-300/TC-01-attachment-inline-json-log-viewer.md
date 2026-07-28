# TC-01: Attachment inline viewer — JSON pretty-print + log/text önizleme

> StepMethod test case (UseCaseTemplate-StepMethod uyarlaması). Ticket'a
> `add_attachment(kind="testcase")` ile bağlanır; UI'da Test Plan bölümü altında popup render edilir.
> Bir TC tek davranış kümesini sınar; her Expected Response gözlemlenebilir/ölçülebilir.

## Summary

| Item | Description |
|---|---|
| Test Case ID: | TC-PH-300-01 |
| Test Case Name: | JSON pretty-print + top-level fold ve log/text monospace önizleme + wrap toggle; size-cap üstünde indir-fallback |
| Description: | Kanıtlar UI'ındaki 'diğer' attachment dalına metin-önizleme davranışını kanıtlar: JSON için `JSON.parse` + 2-space pretty + top-level fold, log/text için monospace scrollable pane + wrap toggle, size-cap aşımında 'ilk N KB + tamamını indir' fallback ve mp4/png regresyonsuzluğu. |
| Related Use Case: | UC-PH-300-01 (JSON/log attachment içeriğini inline oku) |
| Related AC: | AC1 (JSON pretty + top-level fold, ≤ size-cap tam içerik), AC2 (.log/.txt monospace + wrap toggle), AC3 (size-cap → ilk N KB + 'tamamını indir'), AC4 (`?token=` content fetch, 403 inline), AC5 (mp4/png regresyonsuz) |
| Type / Priority: | happy_path + edge / P1 (ticket: high) |
| Actors / Environment: | jarwis-qa (Coordinator browser relay); worktree vite dev :5179 |
| Test Data: | Gerçek PH-297 fixture'ları: `test_results.json`, `seed.log.txt`, 600KB `big-qa.log`; mevcut mp4/png |
| Pre-Conditions: | PH-297 Kanıtlar UI'ı mount; PH-296 content endpoint (`?token=`) erişilebilir; fixture'lar ticket'ta seed |
| Post-Conditions: | Önizleme açılıp kapanır; büyük dosyada fetch atılmaz; media davranışı değişmez |
| References: | PH-300; ticket `test_plan` (4/4 TC); PH-297 fixture'ları; `.jarwis/logs/PH-300/` |

## Test Steps

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| 1 | TC-1: `test_results.json` satırında 'Görüntüle' | AC1 — pretty (2-space) + top-level fold; suite/passed/failed inline; ▸cases "[ 4 öğe ]" → tıklayınca array içeriği açıldı; 'Tümünü aç/kapat' mevcut |
| 2 | TC-2: `seed.log.txt` 'Görüntüle' + wrap toggle | AC2 — monospace pane; 'Satır kaydırma' toggle `aria-pressed` true→false + `whiteSpace` pre-wrap→pre |
| 3 | TC-4: media regresyon kontrolü | AC5 — 2 video + 2 img render (mp4/png regresyonsuz) |

## Negative / Alternate Scenarios

### E1 – Size-cap aşımı → indir-fallback (fetch atılmaz)

| | |
|---|---|
| Branched From: | Test Steps, Step 1 (önizleme) |
| Flow Scenario: | E1 – 600KB `big-qa.log` (size-cap üstü) satırı render edilir |
| Expected Post-Condition: | 'çok büyük, indir' fallback; 'Görüntüle' butonu YOK; content fetch atılmaz; UI kilitlenmez |

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| E1-1 | TC-3: 600KB `big-qa.log` satırı | AC3 — 'çok büyük, indir' fallback; 'Görüntüle' butonu YOK; content fetch atılmadı |

## Execution Record

| Date | Environment | Result | Evidence | Executed By |
|---|---|---|---|---|
| 2026-07-14 | Coordinator browser relay; worktree vite dev :5179; gerçek PH-297 fixture'ları | PASS 4/4 | TC-1 `test_results.json` pretty + top-level fold (suite/passed/failed inline + ▸cases "[ 4 öğe ]" → array açıldı, 'Tümünü aç/kapat' mevcut); TC-2 `seed.log.txt` monospace + 'Satır kaydırma' `aria-pressed` true→false, `whiteSpace` pre-wrap→pre; TC-3 600KB `big-qa.log` → 'çok büyük, indir' fallback, 'Görüntüle' YOK, fetch atılmadı; TC-4 2 video + 2 img regresyonsuz. | jarwis-qa (Coordinator browser relay) |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (gerçek koşum kayıtlarından) | jarwis-qa |
