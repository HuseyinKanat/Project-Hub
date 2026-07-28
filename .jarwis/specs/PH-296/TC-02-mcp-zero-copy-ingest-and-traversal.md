# TC-02: MCP zero-copy ingest (source=agent) + symlink-escape reject

> StepMethod test case (UseCaseTemplate-StepMethod uyarlaması). Ticket'a
> `add_attachment(kind="testcase")` ile bağlanır; UI'da Test Plan bölümü altında popup render edilir.
> Bir TC tek davranışı sınar; her Expected Response gözlemlenebilir/ölçülebilir.

## Summary

| Item | Description |
|---|---|
| Test Case ID: | TC-02 |
| Test Case Name: | MCP `add_attachment` host source_path'ten zero-copy ingest eder (source=agent, list/get parity); symlink-escape reddedilir |
| Description: | MCP `add_attachment(source_path)` yolunun `/repos` mount altındaki host dosyasını zero-copy ingest edip `source="agent"` olarak persist ettiğini + `list_attachments`/`get_attachment` parity'sini kanıtlar; mount içinde durup dışarıya `resolve()` eden symlink'in `.resolve()` parent-check ile reddedildiğini, disk'e hiçbir şey yazılmadığını doğrular. |
| Related Use Case: | UC-01 (Attach evidence file to a ticket) |
| Related AC: | AC4 (MCP zero-copy ingest — host `source_path` → `source="agent"`; list/get parity), AC8 (`source_path` `/repos` mount'a karşı validate; symlink-escape → `attachment_source_invalid`, persist yok) |
| Type / Priority: | happy_path + negative / P0 |
| Actors / Environment: | jarwis-qa; ephemeral stack — PostgreSQL :5433 + uvicorn :8001 (prod stack dokunulmadı) |
| Test Data: | `/repos` mount altında host PNG (`repos/shot.png`); symlink `repos/evidence.png` → `outside/secret.txt` (hedef mount DIŞINDA) |
| Pre-Conditions: | `PH-1` ticket var; agent aktör `attachment.add` cap'ini taşır; `host_home == repos_root` temp mount'a işaret eder; `attachments_root` boş |
| Post-Conditions: | Geçerli ingest için 1 `source="agent"` attachment; reddedilen symlink için 0 row + 0 blob |
| References: | PH-296; `backend/tests/test_attachments_qa.py::test_ingest_rejects_symlink_escaping_repos_root` + `::test_ingest_accepts_regular_file_under_root`; `backend/app/mcp/server.py` (`add_attachment`); commit `8451037` |

## Test Steps

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| 1 | `add_attachment(id="PH-1", source_path="<repos/shot.png>")` (mount altı gerçek dosya) | Attachment yaratılır; `source == "agent"`; `content_type == "image/png"` (`.png` suffix'ten türetilir); `filename == "shot.png"` (basename); byte'lar host path'ten zero-copy stream edilir |
| 2 | `list_attachments(id="PH-1")` | Dönen liste yeni attachment'ı içerir (`id` + `filename` eşleşir) |
| 3 | `get_attachment(id="<att.id>")` | Metadata döner + `content_url` content route'una işaret eder; list/get parity (aynı `id`, `checksum_sha256`, `size_bytes`) |

## Negative / Alternate Scenarios

### N1 – Symlink-escape source_path reddedilir

| | |
|---|---|
| Branched From: | Test Steps, Step 1 |
| Flow Scenario: | N1 – `source_path`, `/repos` mount İÇİNDE yaşayan ama `resolve()` ile mount DIŞINDAKİ bir dosyaya çıkan bir symlink |
| Expected Post-Condition: | `AttachmentSourceInvalid` (422 `attachment_source_invalid`); `attachments_root` altında blob yok; attachment sayısı == 0. RBAC geçtiği için (aktör `attachment.add` taşır) red, post-`resolve()` parent-check guard'ından gelir — auth denial DEĞİL |

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| N1-1 | `repos/evidence.png` → `outside/secret.txt` symlink'i kur; `add_attachment(id="PH-1", source_path="<repos/evidence.png>")` | `AttachmentSourceInvalid` (422 `attachment_source_invalid`) fırlatılır; `_files_under(attachments_root) == []`; `Attachment` count == 0 (ne blob ne row) |

## Execution Record

| Date | Environment | Result | Evidence | Executed By |
|---|---|---|---|---|
| 2026-07-13 | Ephemeral stack — PG :5433 + uvicorn :8001 (prod dokunulmadı) | PASS | Ticket `test_plan` 6/6 (TC-E2E-MCP + TC-SYM); `test_attachments_qa.py` symlink-escape reject + regular-file control; MCP add/list/get 3/3 runtime; commit `8451037` | jarwis-qa |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-13 | 1.0 | Initial version — koşum sonrası Execution Record dolu | jarwis-qa |
