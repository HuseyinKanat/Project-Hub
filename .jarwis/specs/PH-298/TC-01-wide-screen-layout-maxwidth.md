# TC-01: Geniş ekran layout — Layout.tsx max-width kelepçesi kaldırıldı

> StepMethod test case (UseCaseTemplate-StepMethod uyarlaması). Ticket'a
> `add_attachment(kind="testcase")` ile bağlanır; UI'da Test Plan bölümü altında popup render edilir.
> Bir TC tek davranış kümesini sınar; her Expected Response gözlemlenebilir/ölçülebilir.

## Summary

| Item | Description |
|---|---|
| Test Case ID: | TC-PH-298-01 |
| Test Case Name: | 1440p+ ekranda içerik viewport'un ≥%85'ini kullanır; dar ekran (≤1280px) regresyonsuz |
| Description: | `components/Layout.tsx` içindeki 2× `max-w-7xl` kelepçesinin `max-w-screen-2xl` ile değiştirilmesinin geniş ekranda board Kanban + ticket detail içeriğini genişlettiğini, dar ekranda (≤1280px) mevcut görünümü koruduğunu ve ana sayfalarda taşma/kırılma üretmediğini kanıtlar. |
| Related Use Case: | UC-PH-298-01 (Geniş ekranda içerik alanını tam kullan) |
| Related AC: | AC1 (içerik ≥%85 viewport), AC2 (yalnız Layout.tsx diff), AC3 (≤1280px regresyonsuz), AC4 (ana sayfalar taşma/kırılma yok) |
| Type / Priority: | regression + edge / P1 (ticket: high) |
| Actors / Environment: | jarwis-qa (Coordinator browser relay); worktree vite dev :5178 |
| Test Data: | Viewport boyutları 1600px (geniş) ve 900px (dar) |
| Pre-Conditions: | Uygulama vite dev :5178'de çalışır; Boards/Kanban/TicketDetail/Space rota'ları erişilebilir |
| Post-Conditions: | Geniş ekranda container genişler; dar ekranda tek kolon; kalıcı DOM/geometry değişimi yalnız genişlik |
| References: | PH-298; ticket `test_plan` (4/4 TC); `.jarwis/logs/PH-298/` |

## Test Steps

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| 1 | TC-1: 1600px viewport'ta header + main container'larını ölç | AC1/AC2 — container'lar 1536px (%96; eski cap 1280); `max-w-7xl` kalıntısı 0; yatay taşma yok |
| 2 | TC-2: TicketDetail grid'ini ölç | AC1 — `td-grid` "1148px 320px"; sidebar 320px sabit, içerik kolonu +268px genişledi |
| 3 | TC-4: Boards/Kanban + TicketDetail + Space görsel smoke | AC4 — yatay taşma/kırılma yok |

## Negative / Alternate Scenarios

### E1 – Dar viewport (≤1280px) regresyonsuz

| | |
|---|---|
| Branched From: | Test Steps, Step 1 (geniş viewport) |
| Flow Scenario: | E1 – 900px viewport'ta TicketDetail render |
| Expected Post-Condition: | Grid tek kolona iner; daralma/taşma yok; mevcut dar-ekran görünümü korunur |

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| E1-1 | TC-3: 900px viewport'ta TicketDetail | AC3 — `td-grid` tek kolon (852px); yatay taşma yok |

## Execution Record

| Date | Environment | Result | Evidence | Executed By |
|---|---|---|---|---|
| 2026-07-14 | Coordinator browser relay; worktree vite dev :5178 | PASS 4/4 | TC-1 1600px→container 1536px (%96), `max-w-7xl` kalıntı 0, taşma yok; TC-2 `td-grid` "1148px 320px" (+268px içerik); TC-3 900px→tek kolon 852px, taşma yok; TC-4 Kanban/TicketDetail/Space taşma/kırılma yok. DOM/geometry ground-truth. | jarwis-qa (Coordinator browser relay) |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (gerçek koşum kayıtlarından) | jarwis-qa |
