# UC-PH-310-01: SpecDoc deneyimi — DocPopup + AC/Test Plan belge çipleri

> Kaynak format: UseCaseTemplate-StepMethod. Ticket'a `add_attachment(kind="usecase")` ile
> bağlanır ve UI'da popup içinde render edilir. Action = aktörün yaptığı, Reaction = sistemin
> gözlemlenebilir cevabı; Alternate = geçerli varyasyon, Exception = hata yolu.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-310-01 |
| Use Case Name: | SpecDoc deneyimi — DocPopup + AC/Test Plan belge çipleri |
| Description: | UC/TC StepMethod markdown alt-belgeleri (kind=usecase\|testcase) ticket'ın ilgili alanında (AC altında usecase, Test Plan altında testcase) dosya çipi olarak görünür; tıklayınca DocPopup'ta MarkdownRenderer ile (tablolar tablo olarak) render edilir. Kanıt text/json/log dosyaları da inline yerine DocPopup'ta açılır; mp4/png mevcut davranışını korur (video inline, png Lightbox). |
| Actors: | Kullanıcı (ticket okuyan), QA/PM agent'ları (usecase/testcase belgelerini MCP ile ekleyen), sistem (DocPopup + TicketDetail attachment yüzeyi) |
| Triggers: | Kullanıcı usecase/testcase attachment'ı olan bir ticket'ın detay sayfasını açar; ya da bir belge çipine / 'Görüntüle'ye basar. |
| Pre-Conditions: | blocked_by PH-308 (aynı TicketDetail.tsx) + PH-309 (.md allowlist — e2e için) merge; `kind` free-form string (backend değişikliği yok); belge İÇERİĞİNİ agent'lar üretti (paket dışı). |
| Post-Conditions: | Main Flow: kind=usecase çipi → DocPopup StepMethod render (tablolar) + a11y focus-return · Alternate Flow: testcase çipi + text/json/log 'Görüntüle' → DocPopup; usecase/testcase Kanıtlar'da gizli; mp4/png mevcut davranış · Exception Flow: oversize .md → download çipi, popup açılmaz, content fetch atılmaz |
| Includes: | None |
| Extension Points: | None |
| References: | PH-310; AC1–AC10; blocked_by PH-308 + PH-309; plan:ph-ui-readability |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Kullanıcı usecase/testcase attachment'ı olan bir ticket'ı açar. | TicketDetail AC bölümü altında kind=usecase çip(ler)ini (dosya adı, BUTTON) render eder (AC1; PH-296'da AC altı 2 UC çipi). |
| 2 | Kullanıcı bir usecase çipine (UC-01) tıklar. | DocPopup açılır (role=dialog / aria-modal, focus-trap; AC9). |
| 3 | DocPopup içeriği yükler. | Markdown, MarkdownRenderer ile render edilir → StepMethod TAM render: Summary/MainFlow/A1/E1 künye+step/Revision tabloları TABLO olarak (ham metin değil; AC1, AC8). |
| 4 | Kullanıcı Esc'e basar veya backdrop'a tıklar. | DocPopup kapanır, focus tetikleyici çipe döner; Tab trap içeride (AC5, AC9). |
| 5 | Kullanıcı Test Plan bölümüne bakar. | kind=testcase çip(ler)i (BUTTON) görünür; tıklayınca DocPopup render (AC2). |

## Alternate Flows

### A1 – Kanıt text/json/log DocPopup + spec gizleme

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | A1 – Ticket'ta text/json/log kanıt dosyaları var; usecase/testcase spec dosyaları Kanıtlar listesinde gösterilmemeli. |
| Post-Condition: | usecase/testcase Kanıtlar'da GÖRÜNMEZ (yalnız alan-altı çip); text/json/log 'Görüntüle' DocPopup açar (JSON fold / mono log); eski inline genişletme kalkmıştır. |
| Branch To: | End |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Kullanıcı Kanıtlar listesine bakar. | usecase/testcase dosyaları listede yok — spec sızıntısı yok (AC3). |
| A1-2 | Kullanıcı bir json/log kanıtında 'Görüntüle'ye basar. | DocPopup açılır (JSON fold / mono log); PH-300 inline genişletmesi DocPopup'a taşınmış, ölü kod yok (AC4, AC7). |

### A2 – mp4/png mevcut davranış korunur

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | A2 – Ticket'ta mp4/png kanıtları var. |
| Post-Condition: | Video inline oynar, png Lightbox açar; DocPopup'a yönlenmez. |
| Branch To: | End |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A2-1 | Kullanıcı mp4/png kanıtına bakar. | Video inline oynar, png Lightbox açar — mevcut davranış regresyonsuz (AC6). |

## Exception Flows

### E1 – Oversize .md belgesi

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | E1 – usecase/testcase belgesi size-cap üstünde (ör. 518KB big-uc.md). |
| Post-Condition: | `<a download>` 'çok büyük' çipi sunulur; DocPopup açılmaz; content fetch atılmaz. |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Kullanıcı 518KB big-uc.md çipine tıklar. | 'Çok büyük' download çipi sunulur; popup açılmaz, content fetch 0 (network kanıtı). |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (spec-doc paketi) | jarwis-pm |
