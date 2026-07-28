# TC-01: SpecDoc deneyimi — DocPopup + AC/Test Plan belge çipleri

> StepMethod test case (UseCaseTemplate-StepMethod uyarlaması). Ticket'a
> `add_attachment(kind="testcase")` ile bağlanır; UI'da Test Plan bölümü altında popup render edilir.
> Bir TC tek davranış kümesini sınar; her Expected Response gözlemlenebilir/ölçülebilir.

## Summary

| Item | Description |
|---|---|
| Test Case ID: | TC-PH-310-01 |
| Test Case Name: | kind=usecase/testcase çipleri alan-altında + DocPopup StepMethod render; spec Kanıtlar'da gizli; text/json/log DocPopup; over-cap indir-fallback |
| Description: | UC/TC StepMethod belgelerinin (kind=usecase|testcase markdown attachment) TicketDetail'de ilgili alan altında (AC altında usecase, Test Plan altında testcase) çip olarak göründüğünü, çipe tıklayınca DocPopup'ta MarkdownRenderer ile TAM render edildiğini (ham metin değil — tablolar tablo), usecase/testcase'in Kanıtlar listesinde gizlendiğini, DocPopup a11y'sinin (Esc/backdrop/focus-return/trap) Lightbox paritesinde olduğunu, PH-300 inline preview'ın DocPopup'a evrildiğini ve over-cap spec belgenin indir-fallback verdiğini kanıtlar. |
| Related Use Case: | UC-PH-310-01 (UC/TC spec belgelerini alan-altı çip + DocPopup'ta oku) |
| Related AC: | AC1 (usecase çip AC altı + DocPopup MarkdownRenderer), AC2 (testcase çip Test Plan altı + DocPopup), AC3 (usecase/testcase Kanıtlar'da GÖRÜNMEZ), AC4 (text/json/log 'Görüntüle' → DocPopup; inline genişletme kalkar), AC5 (Esc/backdrop → kapanır + focus-return + Tab trap), AC6 (mp4/png mevcut davranış — DocPopup'a yönlenmez), AC7 (PH-300 inline → DocPopup, ölü kod yok), AC8 (.md → DocPopup MarkdownRenderer, raw `<pre>` değil), AC9 (a11y Lightbox-paritesi), AC10 (unit isMarkdown) |
| Type / Priority: | happy_path + edge / P1 (ticket: high) |
| Actors / Environment: | jarwis-qa (Coordinator browser relay); worktree vite dev :5187 |
| Test Data: | PH-296 (AC altı 2 UC çipi + Test Plan altı 2 TC çipi); 518KB `big-uc.md` (over-cap); PH-297 media (2 video + 2 img + 7 kanıt) |
| Pre-Conditions: | blocked_by PH-308 (aynı TicketDetail.tsx) + PH-309 (.md allowlist) çözüldü; DocPopup Lightbox a11y pattern'ini paylaşır; kind free-form string (backend değişikliği yok) |
| Post-Conditions: | Spec belgeler alan-altı çip + popup; Kanıtlar'da spec sızıntısı yok; media davranışı değişmez |
| References: | PH-310; commit 30a6a05; TicketDetail attachment yüzeyi + DocPopup; `.jarwis/logs/PH-310/` |

## Test Steps

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| 1 | TC-1: PH-296 ticket açılır | AC1/AC2 — AC altında 2 UC çipi + Test Plan altında 2 TC çipi (BUTTON) |
| 2 | TC-2: UC-01 çipine tıklanır | AC1/AC8 — DocPopup'ta StepMethod TAM render — 7 tablo (Summary/MainFlow/A1/E1 künye+step/Revision); başlık hiyerarşisi şablon düzeninde (ham metin değil) |
| 3 | TC-3: Kanıtlar listesi kontrol | AC3 — spec sızıntısı yok (PH-296 evidence=0; 4 attachment'ın hepsi spec) |
| 4 | TC-4: DocPopup açıkken Esc | AC5/AC9 — Esc kapattı + focus UC-01 çipine döndü (Tab trap / aria-modal Lightbox paritesi) |
| 5 | TC-6: PH-297 media kontrol | AC6/AC7 — 2 video + 2 img + 7 kanıt + 2 'Görüntüle'→popup; mp4/png regresyonsuz (inline genişletme kalktı) |

## Negative / Alternate Scenarios

### E1 – Over-cap spec belge → indir-fallback (popup açılmaz, fetch 0)

| | |
|---|---|
| Branched From: | Test Steps, Step 2 (çip → DocPopup) |
| Flow Scenario: | E1 – 518KB `big-uc.md` (size-cap üstü) spec çipi render edilir |
| Expected Post-Condition: | `<a download=1>` 'çok büyük' çipi; popup açılmaz; content fetch 0 (network kanıtı) |

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| E1-1 | TC-5: 518KB `big-uc.md` çipi | AC — `<a download=1>` 'çok büyük' çipi; popup açılmadı; content fetch 0 |

## Execution Record

| Date | Environment | Result | Evidence | Executed By |
|---|---|---|---|---|
| 2026-07-14 | Coordinator browser relay; worktree vite dev :5187 | PASS 6/6 | TC-1 PH-296'da AC altı 2 UC çipi + Test Plan altı 2 TC çipi (BUTTON); TC-2 UC-01 çipi → DocPopup StepMethod TAM render — 7 tablo (Summary/MainFlow/A1/E1 künye+step/Revision), başlık hiyerarşisi şablon düzeninde; TC-3 Kanıtlar'da spec sızıntısı yok (PH-296 evidence=0, 4 attachment'ın hepsi spec); TC-4 Esc kapattı + focus UC-01 çipine döndü; TC-5 518KB `big-uc.md` → `<a download=1>` 'çok büyük' çipi, popup açılmadı, content fetch 0; TC-6 PH-297 medya regresyonsuz (2 video + 2 img + 7 kanıt + 2 'Görüntüle'→popup). commit 30a6a05. | jarwis-qa (Coordinator browser relay) |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (gerçek koşum kayıtlarından) | jarwis-qa |
