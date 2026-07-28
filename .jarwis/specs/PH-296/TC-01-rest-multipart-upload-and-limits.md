# TC-01: REST multipart evidence upload — 201 + checksum, size/type limits

> StepMethod test case (UseCaseTemplate-StepMethod uyarlaması). Ticket'a
> `add_attachment(kind="testcase")` ile bağlanır; UI'da Test Plan bölümü altında popup render edilir.
> Bir TC tek davranışı sınar; her Expected Response gözlemlenebilir/ölçülebilir.

## Summary

| Item | Description |
|---|---|
| Test Case ID: | TC-01 |
| Test Case Name: | REST multipart upload persists blob + checksum; oversize→413, unsupported type→415 |
| Description: | `POST /api/tickets/{id}/attachments` multipart yolunun mutlu senaryosu (201 + sha256 checksum + UUID-shard blob) ile iki sınır reddini (25 MiB cap→413 partial-cleanup, allowlist-dışı content-type→415) kanıtlar. |
| Related Use Case: | UC-01 (Attach evidence file to a ticket) |
| Related AC: | AC1 (multipart upload kabul → 201 + `checksum_sha256` kaydı), AC9 (per-file byte cap → 413 `payload_too_large` + partial temizliği), AC10 (content-type allowlist → 415 `unsupported_media_type`) |
| Type / Priority: | happy_path + edge/negative / P0 |
| Actors / Environment: | jarwis-qa; ephemeral stack — PostgreSQL :5433 + uvicorn :8001 (prod stack dokunulmadı) |
| Test Data: | Geçerli PNG (8-byte PNG magic + payload); 26 MiB text blob (25 MiB / 26.214.400-byte cap'i aşar); SVG payload (`image/svg+xml`, allowlist dışı) |
| Pre-Conditions: | `PH-1` ticket seed board'da var; aktör `attachment.add` cap'ini taşır (backend/qa rolü); `attachments_root` boş |
| Post-Conditions: | Geçerli upload için tam 1 attachment row + `<id[:2]>/<id>` shard altında 1 blob; reddedilen upload'larda ne blob ne row (partial cleanup) |
| References: | PH-296; `backend/tests/test_attachments.py`, `backend/tests/test_attachments_qa.py`; commit `8451037`; `.jarwis/logs/PH-296/qa.md` |

## Test Steps

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| 1 | `POST /api/tickets/PH-1/attachments` — multipart `file=shot.png` (`image/png`, geçerli byte'lar) | HTTP 201; response gövdesi `id` + `size_bytes == yüklenen uzunluk` + `checksum_sha256 == sha256(bytes)`; blob `storage_key == "<id[:2]>/<id>"` altında; client filename `storage_key`'de YOK |
| 2 | `GET .../attachments/{id}/content` geçerli `?token=` ile | HTTP 200; gövde byte'ları upload ile birebir aynı; `sha256(body) == checksum_sha256`; header `x-content-type-options: nosniff` + `accept-ranges: bytes` |
| 3 | `POST .../attachments` — 26 MiB text dosya (25 MiB cap'i aşar) | HTTP 413 `payload_too_large`; `attachments_root` altında hiç blob yok; attachment sayısı değişmedi (partial cleanup) |
| 4 | `POST .../attachments` — `x.svg` (`image/svg+xml`, allowlist dışı) | HTTP 415 `unsupported_media_type`; disk'e hiç blob yazılmadı |

## Negative / Alternate Scenarios

### N1 – Token'sız content erişimi reddedilir

| | |
|---|---|
| Branched From: | Test Steps, Step 2 |
| Flow Scenario: | N1 – Content byte-serving route'una ne `?token=` ne `Authorization: Bearer` header'ı ile erişim |
| Expected Post-Condition: | 403 inline hata; hiç byte servis edilmez; blob değişmez |

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| N1-1 | `GET .../attachments/{id}/content` — token param yok, Authorization header yok | HTTP 403; JSON `required == "authenticated_actor"` |
| N1-2 | `GET .../attachments/{id}/content?token=not-a-real-token` (geçersiz token) | HTTP 403; JSON `required == "valid_bearer_token"` |

## Execution Record

| Date | Environment | Result | Evidence | Executed By |
|---|---|---|---|---|
| 2026-07-13 | Ephemeral stack — PG :5433 + uvicorn :8001 (prod dokunulmadı) | PASS | Ticket `test_plan` 6/6 (TC-AUTH/E2E-REST/E2E-MCP/MIG/SYM); `backend/tests/test_attachments.py` (201+checksum, 413 partial-cleanup, 415), `test_attachments_qa.py` (real-token 403 paths); commit `8451037` | jarwis-qa |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-13 | 1.0 | Initial version — koşum sonrası Execution Record dolu | jarwis-qa |
