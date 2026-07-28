# TC-01: Structured AC/TC render — deterministik GWT/TC şeması (variant-scoped)

> StepMethod test case (UseCaseTemplate-StepMethod uyarlaması). Ticket'a
> `add_attachment(kind="testcase")` ile bağlanır; UI'da Test Plan bölümü altında popup render edilir.
> Bir TC tek davranış kümesini sınar; her Expected Response gözlemlenebilir/ölçülebilir.

## Summary

| Item | Description |
|---|---|
| Test Case ID: | TC-PH-301-01 |
| Test Case Name: | AC/test_plan read-view'ında GWT/TC kart render; şema-dışı içerik markdown fallback; variant-scope izolasyonu |
| Description: | `### AC<N>` + GIVEN/WHEN/THEN ve `### TC<N>` + Önkoşul/Adımlar/Beklenen şemalı içeriğin kart/tablo olarak render edildiğini, şemaya uymayan içeriğin birebir markdown fallback'e düştüğünü ve structured render'ın YALNIZ acceptance_criteria + test_plan read-view'ında (description/diğer alanlar hariç) etkin olduğunu kanıtlar. |
| Related Use Case: | UC-PH-301-01 (AC/test_plan'ı yapılandırılmış GWT/TC kartlarında oku) |
| Related AC: | AC1 (AC GWT kart), AC2 (TC kart), AC3 (şema-dışı → markdown fallback), AC4 (parseCriteria saf + birim testli), AC5 (yalnız AC+test_plan read-view), AC6 (TicketDetail.tsx diff'te yer almaz) |
| Type / Priority: | happy_path + edge / P1 (ticket: high) |
| Actors / Environment: | jarwis-qa (Coordinator browser relay); worktree vite dev :5181 |
| Test Data: | Canlı ticket'lar: PH-296 (21 AC), PH-286 (23.7K, eski şemasız) |
| Pre-Conditions: | MarkdownFieldEditor `variant="criteria"` acceptance_criteria + test_plan read-view'ında akar; parseCriteria saf parser mevcut |
| Post-Conditions: | Şemalı içerik kart; şema-dışı içerik markdown; description/diğer alanlar etkilenmez |
| References: | PH-301; ticket `test_plan` (4/4 TC); canlı PH-296 / PH-286; `.jarwis/logs/PH-301/` |

## Test Steps

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| 1 | TC-1: PH-296 acceptance_criteria render | AC1 — 21 AC kartı (`<article>`); AC1..AC21 rozetleri + Given/When/Then TEXT etiketleri + 21 checkbox `role=img` |
| 2 | TC-3: Impact Analysis / Technical Depth alanları render | AC5/AC6 — DÜZ markdown (0 kart); variant-scope tutuyor (structured render yalnız AC + test_plan'da) |

## Negative / Alternate Scenarios

### E1 – Şema-dışı içerik → markdown fallback

| | |
|---|---|
| Branched From: | Test Steps, Step 1 |
| Flow Scenario: | E1 – Deterministik şemaya uymayan serbest-format içerik render edilir |
| Expected Post-Condition: | Temiz markdown fallback (kart yok, kırılma/boş alan yok); progressive enhancement fallback-güvenli |

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| E1-1 | TC-2: PH-296 test_plan serbest-format | AC3 — temiz markdown fallback (kart yok, kırılma yok) |
| E1-2 | TC-4: PH-286 eski şemasız 23.7K içerik | AC3 — hatasız render + 0 kart fallback |

## Execution Record

| Date | Environment | Result | Evidence | Executed By |
|---|---|---|---|---|
| 2026-07-14 | Coordinator browser relay; worktree vite dev :5181; canlı ticket'lar | PASS 4/4 | TC-1 PH-296'da 21 AC kartı (`<article>`) — AC1..AC21 rozet + Given/When/Then TEXT etiket + 21 checkbox `role=img`; TC-2 PH-296 test_plan serbest-format → temiz markdown fallback (kart yok); TC-3 Impact Analysis/Technical Depth DÜZ markdown (0 kart) — variant-scope tuttu; TC-4 PH-286 (eski şemasız) 23.7K içerik hatasız + 0 kart fallback. | jarwis-qa (Coordinator browser relay) |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (gerçek koşum kayıtlarından) | jarwis-qa |
