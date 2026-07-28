# UC-PH-312-01: View the evidence fix-story (phase-grouped)

> Kaynak format: UseCaseTemplate-StepMethod. Ticket'a `add_attachment(kind="usecase")` ile
> bağlanır ve UI'da popup içinde render edilir. Action = aktörün yaptığı, Reaction = sistemin
> gözlemlenebilir cevabı; Alternate = geçerli varyasyon, Exception = hata yolu.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-312-01 |
| Use Case Name: | Kanıt düzeltme-hikâyesini görüntüle (phase-gruplu) |
| Description: | Kanıtları phase taşıyan bir ticket'ı açan yönetici/kullanıcı, Kanıtlar kartının düzeltmeyi kronolojik bir hikâye olarak render ettiğini görür — Reproduce → iterasyon denemeleri (tutmadı/çözüldü) → nihai fix — iterasyon-sayısı özeti ve öncesi/sonrası karşılaştırmasıyla. Phase'siz kanıt PH-297 `run_id` görünümüne değişmeden fallback eder. |
| Actors: | Yönetici/kullanıcı (ticket inceleyen); sistem (frontend Kanıtlar bölümü + PH-296 list endpoint) |
| Triggers: | Kullanıcı bir ticket detay sayfasını (`/boards/:board/tickets/:key`) açar. |
| Pre-Conditions: | BE `phase` `AttachmentResponse`'ta canlı (BE ticket done); FE tipi `phase` ile genişletilmiş; PH-297 Kanıtlar kartı + viewer'lar (lightbox/`<video>`) mevcut. |
| Post-Conditions: | Main Flow: phase taşıyan kanıtlar kronolojik hikâye gruplarında + iterasyon özeti + öncesi/sonrası; viewer'lar çalışır · Alternate Flow: phase'siz → PH-297 `run_id` görünümü (regresyonsuz); karışık → phased hikâye + phase'siz fallback grubu · Exception Flow: geçerli-bilinmeyen slug → kendi sonda grubu, crash yok |
| Includes: | None |
| Extension Points: | Yeni phased attachment MCP ile eklendiğinde `attachment_added` WS event'iyle canlı hikâye yenilenmesi (PH-297'den reuse) |
| References: | DRAFT-fe (bu ticket); blocked_by DRAFT-be (phase backend); PH-297 (evidence UI); AC1–AC7 |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Kullanıcı ticket detay sayfasını açar. | Sistem PH-296 list endpoint'ini çağırır; her attachment `phase` alanıyla döner (veri gelene kadar loading göstergesi). |
| 2 | Liste döner ve ≥1 attachment phase taşır. | Kanıtlar kartı phase-hikâye görünümüne geçer (herhangi bir phase varlığını algılar). |
| 3 | Hikâye render edilir. | Gruplar kronolojik sırada: `Reproduce` → `İterasyon N — tutmadı` → `İterasyon N — çözüldü` → `Sonrası`; her biri insan-okunur başlıkla. |
| 4 | Kart iterasyon sayısını hesaplar. | `-pass`'a ulaşan en yüksek N'den özet gösterilir: `N iterasyonda çözüldü` (pass yoksa `N iterasyon — henüz çözülmedi`). |
| 5 | Öncesi/sonrası çapaları mevcut. | Öncesi (repro/before) ↔ sonrası (nihai pass/after) yan yana karşılaştırma sunulur. |
| 6 | Kullanıcı bir kanıtı önizler/oynatır/indirir. | PH-297 lightbox / `<video>` seek / indirme davranışı değişmeden çalışır. |

## Alternate Flows

### A1 – Phase'siz kanıt (regresyonsuz fallback)

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | A1 – Ticket'ın hiçbir attachment'ı phase taşımaz (hepsi null). |
| Post-Condition: | Kart tam PH-297 `run_id` gruplu görünümüne fallback eder; sıfır görsel/davranışsal regresyon. |
| Branch To: | Main Flow Step 6 (viewer'lar) |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Liste tüm attachment'ları `phase=null` ile döner. | Kart phase yokluğunu algılar ve mevcut `run_id` gruplu `<details>` görünümünü render eder (PH-297 aynen). |

### A2 – Karışık phased + phase'siz

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | A2 – Bazı attachment'lar phase taşır, bazıları null. |
| Post-Condition: | Phased öğeler hikâye görünümünde; phase'siz öğeler fallback grubunda erişilebilir; hiçbiri düşmez. |
| Branch To: | Main Flow Step 3 |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A2-1 | Liste karışık döner. | Kart phased öğeleri hikâye gruplarına, phase'siz öğeleri `Diğer`/`run_id` fallback grubuna yerleştirir (hepsi görünür). |

## Exception Flows

### E1 – Bilinmeyen (ama geçerli) phase slug

| | |
|---|---|
| Branched From: | Main Flow, Step 3 |
| Flow Scenario: | E1 – Bir attachment konvansiyon-dışı geçerli bir slug taşır (ör. `smoke-check`). |
| Post-Condition: | Slug kendi sonda grubunda ham etiketiyle render edilir; UI kilitlenmez; deterministik sıra korunur. |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Kart `smoke-check` phase'li öğeyi işler. | Bilinen başlık eşleşmesi yok → ham slug'ı başlık yapıp bilinen grupların SONUNA deterministik ekler (crash yok, öğe düşmez). |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-16 | 1.0 | Initial Version | jarwis-pm |
