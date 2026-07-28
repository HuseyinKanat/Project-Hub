# TC-01: Handoff/sistem yorumları — marker-parse + rol-renkli kart (CommentCard extract)

> StepMethod test case (UseCaseTemplate-StepMethod uyarlaması). Ticket'a
> `add_attachment(kind="testcase")` ile bağlanır; UI'da Test Plan bölümü altında popup render edilir.
> Bir TC tek davranış kümesini sınar; her Expected Response gözlemlenebilir/ölçülebilir.

## Summary

| Item | Description |
|---|---|
| Test Case ID: | TC-PH-302-01 |
| Test Case Name: | `[HANDOFF]`/`[DEPLOY]` vb. marker'lar tip rozeti + from→to rol chip'li kart olur; marker'sız yorum birebir korunur |
| Description: | CommentCard'ın TicketDetail.tsx'ten `components/CommentCard.tsx`'e davranış-koruyarak çıkarılıp ardından zenginleştirildiğini kanıtlar: `[HANDOFF from→to]` / `[BLOCKED]` / `[RECOVERY]` / `[DEPLOY]` / `[ESCALATION]` marker'ları tip rozeti + from→to rol chip'leriyle kart olur (TEXT badge — renk-only değil), marker gövdeden çıkarılır, marker'sız yorumlar birebir korunur ve collapse çalışır. |
| Related Use Case: | UC-PH-302-01 (Handoff/sistem yorumlarını rol-renkli kartta oku) |
| Related AC: | AC1 (HANDOFF kart + tip rozeti + from→to chip), AC2 (BLOCKED/RECOVERY/DEPLOY/ESCALATION TEXT rozet), AC3 (marker'sız birebir), AC4 (CommentCard extract davranış-korumalı), AC5 (uzun yorum collapse) |
| Type / Priority: | happy_path + edge / P2 (ticket: medium) |
| Actors / Environment: | jarwis-qa (Coordinator browser relay); worktree vite dev :5182 |
| Test Data: | Gerçek yorumlar: PH-297 (8 handoff, qa_failed döngüsü dahil), PH-298 (5 handoff) |
| Pre-Conditions: | CommentCard `components/CommentCard.tsx`'e taşındı; marker regex aktif; canlı ticket yorumları var |
| Post-Conditions: | Marker'lı yorumlar kart; marker'sız yorumlar byte-identik; collapse çalışır |
| References: | PH-302; ticket `test_plan` (4/4 TC); reviewer kod kanıtı + 16/16 unit; `.jarwis/logs/PH-302/` |

## Test Steps

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| 1 | TC-1: PH-297 yorumları render | AC1 — 8 handoff kartı; tip chip + from→to rol chip'leri (pm→architect→frontend→reviewer→qa→frontend qa_failed döngüsü dahil); aria-label'lı |
| 2 | TC-2: DEPLOY marker'lı yorum render | AC2 — deploy chip'i handoff'tan ayırt edilebilir TEXT etiketli |
| 3 | TC-4: PH-298 yorumları render | AC1 — 5 handoff kartı hatasız render |

## Negative / Alternate Scenarios

### E1 – Marker'sız yorum + extract davranış-koruması

| | |
|---|---|
| Branched From: | Test Steps, Step 1 |
| Flow Scenario: | E1 – Marker içermeyen normal yorum (CommentCard extract sonrası) |
| Expected Post-Condition: | Mevcut görünümle birebir (byte-identik); collapse kapalıda marker+özet / açıkta tam gövde |

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| E1-1 | TC-3: marker'sız yorum + collapse | AC3/AC4/AC5 — 7 collapse butonu; marker'sız yol byte-identik (reviewer kod kanıtı + 16/16 unit) |

## Execution Record

| Date | Environment | Result | Evidence | Executed By |
|---|---|---|---|---|
| 2026-07-14 | Coordinator browser relay; worktree vite dev :5182; gerçek yorumlar | PASS 4/4 | TC-1 PH-297'de 8 handoff kartı — tip chip + from→to rol chip'leri (pm→architect→frontend→reviewer→qa→frontend qa_failed döngüsü dahil), aria-label'lı; TC-2 deploy chip'i handoff'tan ayırt edilebilir TEXT etiketli; TC-3 7 collapse butonu + marker'sız yol byte-identik (reviewer kod kanıtı + 16/16 unit — bu ticket'larda insan yorumu yok, negatif görsel değişim gözlenmedi); TC-4 PH-298'de 5 handoff kartı hatasız. | jarwis-qa (Coordinator browser relay) |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (gerçek koşum kayıtlarından) | jarwis-qa |
