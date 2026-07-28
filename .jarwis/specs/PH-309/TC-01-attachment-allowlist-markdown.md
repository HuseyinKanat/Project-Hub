# TC-01: Attachment allowlist'e text/markdown — .md upload 201 + MCP ingest çözümü

> StepMethod test case (UseCaseTemplate-StepMethod uyarlaması). Ticket'a
> `add_attachment(kind="testcase")` ile bağlanır; UI'da Test Plan bölümü altında popup render edilir.
> Bir TC tek davranış kümesini sınar; her Expected Response gözlemlenebilir/ölçülebilir.

## Summary

| Item | Description |
|---|---|
| Test Case ID: | TC-PH-309-01 |
| Test Case Name: | text/markdown multipart upload 201 + persist; .md MCP ingest text/markdown'a çözülür; svg/html/zip hâlâ 415 |
| Description: | `attachment_allowed_types` default'una `text/markdown` eklenmesinin .md multipart upload'ını 201 + `content_type='text/markdown'` ile kalıcılaştırdığını, MCP ingest yolunun (`ingest_from_source_path`) .md için content-type'ı octet-stream reddine düşmeden text/markdown'a çözdüğünü ve güvenlik gereği svg/html/zip'in hâlâ 415 döndüğünü kanıtlar. |
| Related Use Case: | UC-PH-309-01 (.md belge attachment olarak yükle) |
| Related AC: | AC1 (text/markdown multipart → 201 + persist), AC2 (.md MCP ingest → text/markdown, octet-stream reddine düşmez), AC3 (image/svg+xml veya text/html → hâlâ 415), AC4 (mevcut allowlist tipleri regresyonsuz), AC5 (unit: text/markdown accept + set membership; application/zip 415 negatif), AC6 (doc: .env override'ların text/markdown'ı elle eklemesi gerektiği technical_depth'te not) |
| Type / Priority: | happy_path + negative / P2 (ticket: medium; backend) |
| Actors / Environment: | jarwis-qa; pytest (backend, izole re-run); canlı .md POST smoke merge-sonrası deploy §8.d'de |
| Test Data: | Geçerli .md dosyası; image/svg+xml, text/html, application/zip (negatif) |
| Pre-Conditions: | `config.py` `attachment_allowed_types` default'una text/markdown eklendi; `mimetypes` .md → text/markdown seed (gerekirse `add_type`); mevcut allowlist tipleri korunur |
| Post-Conditions: | .md upload'ları 415 gate'ini geçer + content route nosniff'li (executable-content eskalasyonu yok); disallowed tipler reddedilir |
| References: | PH-309; commit 79a375f; `backend/config.py` attachment_allowed_types, `backend/tests/test_attachments.py`; `.jarwis/logs/PH-309/` |

## Test Steps

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| 1 | TC-1 (pytest): text/markdown multipart upload | AC1/AC5 — 201 + `content_type='text/markdown'` persist; `attachment_allowed_types_set` membership |
| 2 | TC-2 (pytest): .md MCP ingest (source_path) | AC2 — content-type text/markdown'a çözülür (octet-stream reddine düşmez) ve kaydolur |
| 3 | TC-4 (pytest): mevcut allowlist regresyon | AC4 — 14/14 PASS; mevcut tipler regresyonsuz, yeni markdown case'leri yeşil |

## Negative / Alternate Scenarios

### E1 – Güvenlik-dışı tipler hâlâ 415

| | |
|---|---|
| Branched From: | Test Steps, Step 1 |
| Flow Scenario: | E1 – image/svg+xml, text/html veya application/zip upload denenir |
| Expected Post-Condition: | Hâlâ 415 (svg/html güvenlik gereği DIŞARIDA; zip negatif) |

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| E1-1 | TC (pytest): svg/html/zip upload | AC3/AC5 — image/svg+xml + text/html + application/zip → hâlâ 415 |

## Execution Record

| Date | Environment | Result | Evidence | Executed By |
|---|---|---|---|---|
| 2026-07-14 | jarwis-qa; pytest `tests/test_attachments.py` BAĞIMSIZ re-run (canlı .md POST smoke = merge-sonrası deploy §8.d) | PASS | pytest 14/14 PASS: text/markdown multipart → 201 + `content_type='text/markdown'` persist + set membership; .md MCP ingest text/markdown çözümü (octet-stream reddi yok); image/svg+xml + text/html + application/zip → 415 negatif; mevcut allowlist regresyonsuz. Kod-doğruluğu kanıtı tam; canlı .md POST smoke deploy adımında koşulur (§8.d). commit 79a375f. | jarwis-qa (Coordinator relay) |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (gerçek koşum kayıtlarından) | jarwis-qa |
