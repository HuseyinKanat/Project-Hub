# UC-PH-301-01: Yapılandırılmış AC/TC render (GWT/TC şeması)

> Kaynak format: UseCaseTemplate-StepMethod. Ticket'a `add_attachment(kind="usecase")` ile
> bağlanır ve UI'da popup içinde render edilir. Action = aktörün yaptığı, Reaction = sistemin
> gözlemlenebilir cevabı; Alternate = geçerli varyasyon, Exception = hata yolu.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-301-01 |
| Use Case Name: | Yapılandırılmış AC/TC (GWT/TC) render (variant-scoped) |
| Description: | Kullanıcı bir ticket'ın acceptance_criteria / test_plan read-view'ını açtığında, deterministik şemaya ('### AC<N>:' + GIVEN/WHEN/THEN, '### TC<N>:' + Önkoşul/Adımlar/Beklenen) uyan içerik kart/tablo olarak render edilir; şemaya uymayan içerik aynen mevcut markdown render'ına düşer (progressive enhancement, fallback-güvenli). |
| Actors: | Kullanıcı (ticket okuyan), Jarwis agent'ları (AC/TC'yi deterministik parse eden — şema agent-parse-edilebilirlik amaçlı), sistem (`parseCriteria` + `StructuredCriteria` + `MarkdownFieldEditor` variant="criteria") |
| Triggers: | Kullanıcı bir ticket detay sayfasında acceptance_criteria veya test_plan alanının read-view'ını görüntüler. |
| Pre-Conditions: | `parseCriteria` saf fonksiyon + birim testli (şemalı/şemasız/karışık/boş girdiler; AC4); `MarkdownFieldEditor` variant="criteria" YALNIZ acceptance_criteria + test_plan alanlarında etkin; editör dokunulmaz. |
| Post-Conditions: | Main Flow: şemalı AC kart (rozet + etiketli G/W/T) · Alternate Flow: şemalı TC kart (Önkoşul/Adımlar/Beklenen) · Exception Flow: şemasız içerik → birebir eski markdown fallback, kırılma/boş alan yok |
| Includes: | None |
| Extension Points: | None |
| References: | PH-301; AC1–AC6; plan:ph-ui-readability (Dalga B — P6 ile paralel; P5'in blocked_by kaynağı) |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Kullanıcı '### AC1: ...' + GIVEN/WHEN/THEN şemalı acceptance_criteria taşıyan bir ticket'ı açar. | `MarkdownFieldEditor` variant="criteria" read-view'ı `parseCriteria`'ya verir. |
| 2 | `parseCriteria` içeriği ayrıştırır. | Her AC için başlık + G/W/T satırları çıkarılır (saf fonksiyon; AC4). |
| 3 | `StructuredCriteria` render eder. | Her AC kart (`<article>`) olarak render edilir: AC id rozeti + etiketli Given/When/Then satırları + checkbox (AC1; PH-296'da 21 AC kartı). |
| 4 | Kullanıcı description / Impact Analysis / Technical Depth alanlarına bakar. | Structured render YALNIZ acceptance_criteria + test_plan read-view'ında etkin; diğer alanlar düz markdown (0 kart; AC5). |
| 5 | Kullanıcı test_plan alanına geçer. | '### TC1:' şemalı ise test-case kartları (bkz. A1), değilse markdown fallback (bkz. E1) görünür. |

## Alternate Flows

### A1 – Şemalı TC render (test-case kartları)

| | |
|---|---|
| Branched From: | Main Flow, Step 5 |
| Flow Scenario: | A1 – test_plan '### TC<N>:' + Önkoşul/Adımlar/Beklenen şemasına uyar. |
| Post-Condition: | Test-case kartlarıyla render (TC id + adım listesi); read-view yapılandırılmış görünür. |
| Branch To: | End |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Kullanıcı '### TC1:' şemalı test_plan taşıyan ticket açar. | `parseCriteria` TC bloklarını ayrıştırır (Önkoşul/Adımlar/Beklenen). |
| A1-2 | `StructuredCriteria` render eder. | Her TC kart olarak render edilir: TC id + Önkoşul + Adımlar (liste) + Beklenen blokları (AC2). |

## Exception Flows

### E1 – Şemasız içerik → markdown fallback

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | E1 – Mevcut/eski içerik deterministik şemaya uymuyor (serbest format). |
| Post-Condition: | Birebir eski markdown görünümü; kart yok, kırılma/boş alan yok. |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Kullanıcı eski/şemasız (ör. PH-286, 23.7K) içerik açar. | `parseCriteria` şema bulamaz → içerik düz markdown olarak render edilir (0 kart, hatasız; AC3). |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (spec-doc paketi) | jarwis-pm |
