# UC-PH-298-01: Geniş ekranda tam-genişlik yerleşim

> Kaynak format: UseCaseTemplate-StepMethod. Ticket'a `add_attachment(kind="usecase")` ile
> bağlanır ve UI'da popup içinde render edilir. Action = aktörün yaptığı, Reaction = sistemin
> gözlemlenebilir cevabı; Alternate = geçerli varyasyon, Exception = hata yolu.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-298-01 |
| Use Case Name: | Geniş ekranda tam-genişlik yerleşim |
| Description: | Kullanıcı board/ticket sayfalarını geniş ekranda (1440p+) açtığında, `Layout.tsx`'teki 2× `max-w-7xl` kelepçesi `max-w-screen-2xl`'e yükseltildiği için içerik viewport'un ≥%85'ini kullanır (eski ~%50 daralma kalkar); ticket-detail grid (1fr 320px) otomatik genişler, sidebar 320px sabit kalır. |
| Actors: | Kullanıcı (geniş ekranda çalışan), sistem (`components/Layout.tsx` kapsayıcısı) |
| Triggers: | Kullanıcı 1440p+ genişlikte bir ekranda Boards / Kanban / TicketDetail / Space sayfasını açar. |
| Pre-Conditions: | PH-298 merge'lenmiş; değişiklik yalnız `components/Layout.tsx` (`index.css`'e dokunulmadı — P7 ile çakışma bilinçli eritildi); viewport genişliği ≥1440px. |
| Post-Conditions: | Main Flow: içerik viewport ≥%85; TicketDetail içerik kolonu genişler, sidebar 320px sabit · Alternate Flow: ≤1280px ve ≤900px'te mevcut/responsive görünüm regresyonsuz · Exception Flow: kelepçe kalkınca yatay taşma riski — ana sayfaların hiçbirinde taşma yok |
| Includes: | None |
| Extension Points: | None |
| References: | PH-298; AC1–AC4; plan:ph-ui-readability (Dalga A — P2/P3 ile paralel, globs disjoint) |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Kullanıcı geniş monitörde (1600px viewport) board Kanban sayfasını açar. | Header + main container `max-w-screen-2xl` ile 1536px'e (viewport ~%96) genişler; önceki 1280px (`max-w-7xl`) kelepçesi yok (AC1). |
| 2 | Kullanıcı aynı ekranda bir ticket'ın detay sayfasına geçer. | Ticket-detail grid (`1fr 320px`) içerik kolonu +268px genişleyerek 1148px olur; sağ meta sidebar 320px sabit kalır (AC1). |
| 3 | Kullanıcı Boards liste sayfasını açar. | Kart ızgarası genişlemiş kapsayıcıya yayılır; yatay taşma yok (AC4). |
| 4 | Kullanıcı Space sayfasını açar. | İçerik geniş kapsayıcıda render; kırılma/taşma yok (AC4). |
| 5 | Kullanıcı pencereyi 1280px'e daraltır. | Düzen eski davranışına regresyonsuz döner (aşağı-daralma yok; AC3). |
| 6 | Kullanıcı görsel smoke gezinmesini tamamlar (Boards, Kanban, TicketDetail, Space). | Tüm ana sayfalarda içerik viewport'un ≥%85'ini kullanır, hiçbir sayfada kırılma/taşma yok (AC4). |

## Alternate Flows

### A1 – Dar / responsive ekran (≤1280px, ≤900px)

| | |
|---|---|
| Branched From: | Main Flow, Step 5 |
| Flow Scenario: | A1 – Kullanıcı standart/dar ekranda (≤1280px) ya da çok dar ekranda (≤900px) çalışır. |
| Post-Condition: | ≤1280px'te mevcut görünüm birebir; ≤900px'te ticket grid tek kolona düşer — her iki durumda taşma/regresyon yok. |
| Branch To: | End |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Kullanıcı 1280px viewport'ta board/ticket açar. | `max-w-screen-2xl` viewport'tan büyük olduğundan efektif genişlik viewport'a eşit; eski yerleşimle aynı, taşma yok (AC3). |
| A1-2 | Kullanıcı 900px'te TicketDetail açar. | `td-grid` tek kolona (852px) düşer; içerik + sidebar dikey yığılır, yatay taşma yok. |

## Exception Flows

### E1 – Kelepçe kalkınca yatay taşma riski

| | |
|---|---|
| Branched From: | Main Flow, Step 1 (kapsayıcı genişledikten sonra) |
| Flow Scenario: | E1 – `max-width` kelepçesi kalkınca sabit-genişlikli/taşmaya yatkın bir çocuk öğe geniş kapsayıcıda yatay taşma yaratabilir. |
| Post-Condition: | Ana sayfaların hiçbirinde yatay taşma yok; olsaydı regresyon sayılıp düzeltilirdi (guardrail). |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Kullanıcı 1600px'te Kanban / TicketDetail / Space gezip yatay kaydırma arar. | Hiçbir sayfada taşma yok; `max-w-7xl` kalıntısı 0; layout sağlam kalır (AC2, AC4). |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (spec-doc paketi) | jarwis-pm |
