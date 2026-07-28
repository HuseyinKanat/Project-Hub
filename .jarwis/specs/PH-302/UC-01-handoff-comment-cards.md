# UC-PH-302-01: Handoff/sistem yorumları — marker-parse + rol kart

> Kaynak format: UseCaseTemplate-StepMethod. Ticket'a `add_attachment(kind="usecase")` ile
> bağlanır ve UI'da popup içinde render edilir. Action = aktörün yaptığı, Reaction = sistemin
> gözlemlenebilir cevabı; Alternate = geçerli varyasyon, Exception = hata yolu.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-302-01 |
| Use Case Name: | Handoff/sistem yorumları — marker-parse + rol-renkli kart |
| Description: | Kullanıcı ticket yorumlarını görüntülediğinde, [HANDOFF x→y] / [BLOCKED] / [RECOVERY] / [DEPLOY] / [ESCALATION] marker'lı sistem yorumları tip rozeti + from→to rol chip'leri (TEXT badge, renk-only değil) + gövde ile kart olarak render edilir; marker gövde metninden çıkarılır, marker'sız normal yorumlar birebir korunur. |
| Actors: | Kullanıcı (yorumları okuyan), Jarwis agent'ları/sistem (marker'lı handoff yorumları üreten — pm→architect→...→qa döngüsü), sistem (`components/CommentCard.tsx`) |
| Triggers: | TicketDetail yorum listesini render eder. |
| Pre-Conditions: | CommentCard, TicketDetail.tsx'ten `components/CommentCard.tsx`'e extract edildi (davranış-korumalı, ayrı commit); marker regex tanımlı. |
| Post-Conditions: | Main Flow: [HANDOFF from→to] → tip rozeti + from→to chip'leri + gövde, marker gövdeden çıkarılır · Alternate Flow: [BLOCKED]/[RECOVERY]/[DEPLOY]/[ESCALATION] → ayırt edilebilir TEXT-etiketli rozet · Exception Flow: marker'sız yorum → birebir (byte-identik) eski render |
| Includes: | None |
| Extension Points: | Uzun yorum collapse — kapalıda marker+özet, açıkta tam gövde (Main Flow Step 4) |
| References: | PH-302; AC1–AC5; plan:ph-ui-readability (Dalga B — P4 ile paralel; P7'nin blocked_by kaynaklarından) |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Kullanıcı bir ticket'ın yorumlarını açar. | TicketDetail her yorumu extract edilmiş `CommentCard` bileşenine verir; kullanım davranış-korumalı çalışır (AC4). |
| 2 | CommentCard yorum gövdesinde marker regex çalıştırır. | [HANDOFF <from>→<to>] marker'ını tespit eder. |
| 3 | Marker eşleşir. | Sistem tip rozeti + from→to rol chip'leri (TEXT badge, renk-only değil) render eder ve marker'ı gövde metninden çıkarır (AC1; PH-297'de 8 handoff kartı — qa_failed döngüsü dahil). |
| 4 | Kullanıcı uzun bir yorumu görüntüler. | Collapse: kapalı halde marker + ilk N satır özet, aç/kapa ile tam gövde (AC5). |
| 5 | Kullanıcı chip'lere klavye ile odaklanır. | Chip'ler `aria-label`'lı ve klavye ile erişilebilir. |

## Alternate Flows

### A1 – Diğer marker tipleri ([BLOCKED]/[RECOVERY]/[DEPLOY]/[ESCALATION])

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | A1 – Yorum [BLOCKED] / [RECOVERY] / [DEPLOY] / [ESCALATION] marker'ı taşır. |
| Post-Condition: | Ayırt edilebilir TEXT-etiketli rozetle render (renk-only değil); handoff'tan görsel olarak ayrılır. |
| Branch To: | Main Flow Step 4 (collapse davranışı) |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Kullanıcı [DEPLOY] marker'lı yorumu görüntüler. | Sistem deploy rozetini handoff'tan ayırt edilebilir TEXT etiketiyle gösterir (AC2). |

## Exception Flows

### E1 – Marker'sız normal yorum

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | E1 – Yorumda tanınan marker yok (insan yorumu / serbest metin). |
| Post-Condition: | Mevcut görünümle birebir (byte-identik) render; hiçbir rozet/chip eklenmez, kırılma yok. |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Kullanıcı marker'sız bir yorum görüntüler. | CommentCard onu eski markdown yoluyla render eder; rozet/chip yok, davranış korunur (AC3). |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (spec-doc paketi) | jarwis-pm |
