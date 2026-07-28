# TC-01: Shared Modal primitive — Lightbox + DocPopup adoption (a11y + onClose stability)

> StepMethod test case (UseCaseTemplate-StepMethod uyarlaması). Ticket'a
> `add_attachment(kind="testcase")` ile bağlanır; UI'da Test Plan bölümü altında popup render edilir.
> Bir TC tek davranış kümesini sınar; her Expected Response gözlemlenebilir/ölçülebilir.

## Summary

| Item | Description |
|---|---|
| Test Case ID: | TC-PH-305-01 |
| Test Case Name: | `ui/Modal` primitive a11y kontratı + Lightbox/DocPopup adoption + primitive-seviyesinde onClose latest-ref stabilitesi |
| Description: | Davranış-koruyan refactor'ün runtime doğrulaması: Lightbox ve DocPopup'ın bespoke overlay+focus-trap `useEffect`'lerini silip `ui/Modal`'ı tükettiğini, `role=dialog`/`aria-modal`/focus-trap/Esc+backdrop/focus-return kontratının çalıştığını ve onClose'un primitive'de latest-ref (`onCloseRef` + empty-deps mount-once effect) ile stabilize edildiğini (parent re-render'da listener re-bind / focus steal yok) kanıtlar. |
| Related Use Case: | —: refactor ticket, UC zorunlu değil |
| Related AC: | AC1 (Modal a11y kontratı + focusTrap 8 case: empty/single/middle/wrap-fwd/wrap-bwd/active-not-in-list PASS), AC2 (Lightbox+DocPopup consume + onClose latest-ref stability), AC2-contract (prop'lar UNCHANGED, no caller edits), AC3 (adoption ledger — 11 kalan call site), AC4 (tsc/eslint + manual keyboard smoke) |
| Type / Priority: | regression + edge / P2 (ticket: low) |
| Actors / Environment: | jarwis-qa (Coordinator browser relay); worktree vite dev :5188 |
| Test Data: | PH-297 img (Lightbox trigger); PH-296 UC-01 (DocPopup trigger) |
| Pre-Conditions: | `ui/Modal.tsx` + `ui/focusTrap.ts` (+ test) mevcut; Lightbox/DocPopup adopte; worktree node_modules symlink'li (tsc/eslint/node:test için) |
| Post-Conditions: | Lightbox/DocPopup external prop'ları byte-identik; davranış korunur; kod-yolu tekilleşir |
| References: | PH-305; commit ddf746c, ee6aab4; `frontend/src/components/ui/{Modal.tsx,focusTrap.ts,focusTrap.test.ts}`, `Lightbox.tsx`, `DocPopup.tsx`; `.jarwis/logs/PH-305/` |

## Test Steps

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| 1 | TC-1: PH-297 img → Lightbox açılır | AC1/AC2 — Modal'da açıldı (`aria-modal`, focus içeride); Esc kapattı; focus trigger'a DÖNDÜ; dismiss-surface ('Kapat') tıklaması kapattı |
| 2 | TC-2: PH-296 UC-01 → DocPopup açılır | AC1/AC2 — 7 StepMethod tablosu; `aria-labelledby`; Esc + focus-return |
| 3 | TC-3: focus-trap runtime | AC1 — Tab/Shift+Tab iki yönde dialog içinde wrap |
| 4 | TC-4: Reviewer bağımsız gate re-run | AC4 — node:test 116/116 (8 focusTrap yeni + 108 mevcut) + tsc clean + eslint clean |

## Negative / Alternate Scenarios

### E1 – Parent re-render (kimlik-değişen onClose) → listener re-bind / focus steal yok

| | |
|---|---|
| Branched From: | Test Steps, Step 1 |
| Flow Scenario: | E1 – Modal açıkken parent, her render'da yeni identity'li `onClose={() => setOpen(false)}` ile re-render eder |
| Expected Post-Condition: | keydown listener RE-BIND olmaz; focus ÇALINMAZ; kapanışta focus tam tetikleyici elemana (thumbnail button / chip) döner (primitive latest-ref) |

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| E1-1 | Modal açıkken parent re-render (inline onClose) | AC2 — listener re-bind yok, focus steal yok; kapanışta focus trigger'a döner |

## Execution Record

| Date | Environment | Result | Evidence | Executed By |
|---|---|---|---|---|
| 2026-07-14 | Coordinator browser relay; worktree vite dev :5188 | PASS 4/4 | TC-1 Lightbox: PH-297 img → Modal (aria-modal, focus içeride), Esc kapattı, focus trigger'a DÖNDÜ, dismiss-surface ('Kapat') kapattı; TC-2 DocPopup: PH-296 UC-01 → 7 StepMethod tablosu, `aria-labelledby`, Esc+focus-return; TC-3 Tab/Shift+Tab iki yönde dialog içinde wrap; TC-4 reviewer re-run node:test 116/116 + tsc clean + eslint clean. commit ddf746c, ee6aab4. | jarwis-qa (Coordinator browser relay) |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (gerçek koşum kayıtlarından) | jarwis-qa |
