# UC-PH-309-01: Attachment allowlist'e text/markdown ekle (.md upload 201)

> Kaynak format: UseCaseTemplate-StepMethod. Ticket'a `add_attachment(kind="usecase")` ile
> bağlanır ve UI'da popup içinde render edilir. Action = aktörün yaptığı, Reaction = sistemin
> gözlemlenebilir cevabı; Alternate = geçerli varyasyon, Exception = hata yolu.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-309-01 |
| Use Case Name: | Attachment allowlist'e text/markdown ekle (.md upload 201) |
| Description: | SpecDoc (.md) belgelerinin attachment olarak yüklenebilmesi için `config.py` `attachment_allowed_types` default'una `text/markdown` eklenir; multipart upload 201 döner ve content_type 'text/markdown' persist edilir. MCP ingest yolu (`ingest_from_source_path`) .md içeriğini octet-stream reddine düşmeden text/markdown'a çözer. |
| Actors: | Yükleyen kullanıcı/agent (multipart .md), QA/PM agent (MCP `ingest_from_source_path` ile .md ekleyen), sistem (config allowlist + ingest yolu) |
| Triggers: | Bir .md (SpecDoc) dosyası multipart upload ya da MCP ingest yoluyla ticket'a eklenir. |
| Pre-Conditions: | `config.py` `attachment_allowed_types` default'una `text/markdown` eklendi; mimetypes .md → text/markdown çözebiliyor (gerekirse `mimetypes.add_type` seed ya da ingest'te explicit content_type). `kind` alanı free-form string — usecase/testcase için enum/backend değişikliği gerekmez. |
| Post-Conditions: | Main Flow: text/markdown multipart → 201 + content_type persist · Alternate Flow: MCP ingest .md → text/markdown'a çözülür ve kaydolur · Exception Flow: image/svg+xml / text/html / application/zip → hâlâ 415 (güvenlik) |
| Includes: | None |
| Extension Points: | None |
| References: | PH-309; AC1–AC6; plan:ph-ui-readability (T3 — .md allowlist, PH-310 e2e bağımlılığı) |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Kullanıcı/agent bir .md dosyasını multipart olarak ticket'a yükler. | Backend content-type'ı `text/markdown` olarak alır. |
| 2 | Backend allowlist kontrolü yapar. | `text/markdown` artık `attachment_allowed_types_set` üyesi — membership geçer (AC5). |
| 3 | Membership geçer. | Backend blob'u persist eder ve 201 döner; content_type 'text/markdown' olarak kaydedilir (AC1). |
| 4 | Mevcut allowlist tipleri (png/jpeg/mp4/plain/json) de kullanılır. | Regresyon yok; eski upload'lar yeşil kalır, yeni markdown case'leri yeşil (AC4). |
| 5 | Ops/DOC gereksinimi karşılanır. | technical_depth'te `.env` `ATTACHMENT_ALLOWED_TYPES` override'ının full-replace olduğu ve text/markdown'ın elle eklenmesi gerektiği not edilir (AC6). |

## Alternate Flows

### A1 – MCP ingest (source_path ile .md)

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | A1 – QA/PM agent .md'yi MCP `ingest_from_source_path` ile ekler; `mimetypes.guess_type('.md')` bazı ortamlarda None → octet-stream riski. |
| Post-Condition: | content-type text/markdown'a çözülür ve kaydolur (octet-stream reddine düşmez). |
| Branch To: | Main Flow Step 3 (persist + 201) |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Agent `add_attachment(source_path=UC-01-....md, kind="usecase")` çağırır. | Ingest yolu .md için content-type'ı text/markdown'a çözer (`mimetypes.add_type` seed ya da explicit content_type) → persist (AC2). |

## Exception Flows

### E1 – Güvenlik-dışı tipler hâlâ 415

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | E1 – image/svg+xml, text/html ya da application/zip upload. |
| Post-Condition: | 415 reddi; bu tipler güvenlik gereği allowlist'e eklenmez (executable-content eskalasyonu yok). |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Kullanıcı image/svg+xml veya text/html yükler. | Membership kontrolü başarısız → 415 (AC3). |
| E1-2 | Kullanıcı application/zip yükler. | Hâlâ 415 (negatif test; AC5). |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (spec-doc paketi) | jarwis-pm |
