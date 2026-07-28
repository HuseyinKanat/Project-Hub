# UC-01: Attach evidence to a ticket

> Kaynak format: UseCaseTemplate-StepMethod. Ticket'a `add_attachment(kind="usecase")` ile
> bağlanır ve UI'da popup içinde render edilir. Action = aktörün yaptığı, Reaction = sistemin
> gözlemlenebilir cevabı; Alternate = geçerli varyasyon, Exception = hata yolu.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-01 |
| Use Case Name: | Attach evidence to a ticket |
| Description: | After a device E2E (Appium) run, the QA agent attaches its proof artifacts (screen recording, screenshots, run report) to the ticket via the zero-copy `add_attachment` MCP tool. The server validates and persists each blob and emits an `attachment_added` event so the evidence surfaces on the ticket. |
| Actors: | QA agent (`jarwis-qa`) |
| Triggers: | A device E2E run completes and produces evidence artifacts that must be attached to the ticket as proof of the result. |
| Pre-Conditions: | Ticket exists; `jarwis-qa` is a member of the ticket's board with the `attachment.add` permission; the artifacts exist at absolute host paths under `$HOME` (mounted read-only in-container at `/repos`); the run's `run_id` is known. |
| Post-Conditions: | Main Flow: blob persisted under the UUID shard `{id[:2]}/{id}`, `attachment_added` history event written + published, metadata returned, evidence visible in `list_attachments` · Alternate Flow: oversize recording NOT stored inline — referenced by host path, size-compliant proxy attached instead, no partial blob left · Exception Flow: disallowed content-type rejected with 415 before any disk IO — no blob, no row, no event, ticket unchanged |
| Includes: | None |
| Extension Points: | None |
| References: | PH-296 (Ticket evidence attachments); AC-1, AC-4, AC-9, AC-10 |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | QA agent finishes a device E2E (Appium) run and writes the artifacts — recording `run.mp4`, screenshots `*.png`, report `report.json` — into a directory under the host `$HOME` (visible in-container at `/repos`). | Artifacts exist on disk at absolute host paths; `run_id` is known from the run. |
| 2 | QA agent calls `add_attachment(id=PH-296, source_path=<abs host path>, kind="recording", run_id=<run_id>)` via `mcp__project-hub-qa` — one call per artifact. | Server receives the zero-copy ingest request (MCP dispatch fixes `source="agent"`). |
| 3 | Ingest request received (from Step 2). | Server authorizes the caller for `attachment.add` on the ticket's board FIRST (so existence is not leaked to unauthorized callers), then validates `source_path` — absolute, no `..` traversal, resolves under `/repos`, is a real regular file — and stat-gates the size at ≤ 26,214,400 bytes (25 MiB). |
| 4 | Path + size valid (from Step 3). | Server normalizes the content-type (guessed from filename, parameter-stripped, lower-cased) and requires membership in the allowlist (`image/png, image/jpeg, video/mp4, text/plain, application/json, text/markdown`). |
| 5 | Content-type allowed (from Step 4). | Server streams the bytes into storage `{id[:2]}/{id}` under `attachments_root`, computing sha256 + byte count (cap re-enforced mid-stream), and inserts the `Attachment` row (filename, content_type, size_bytes, checksum_sha256, storage_key, kind, source=`agent`, run_id). |
| 6 | Row committed (from Step 5). | Server writes an `attachment_added` history event, publishes the ticket event to live subscribers, and returns the attachment metadata (no bytes). The evidence now appears in `list_attachments` and the ticket's Evidence section. |

## Alternate Flows

### A1 – Recording exceeds the 25 MiB cap (reference oversize by path)

| | |
|---|---|
| Branched From: | Main Flow, Step 3 |
| Flow Scenario: | A1 – The screen recording is larger than the 25 MiB cap; the evidence is preserved as a host-path reference plus a size-compliant proxy instead of an inline blob. |
| Post-Condition: | Oversize recording NOT stored inline (no partial blob); its absolute host path recorded as a reference on the ticket; a size-compliant proxy attached as inline evidence. |
| Branch To: | End (proxy persisted; oversize referenced by path) |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | `run.mp4` measures > 26,214,400 bytes; QA agent calls `add_attachment(source_path=run.mp4, ...)`. | Server stat-gates the size and raises `payload_too_large` (HTTP 413, `limit=26214400`); no bytes are streamed, no row or event is created. |
| A1-2 | QA agent records `run.mp4`'s absolute host path in a ticket comment (path reference) and prepares a size-compliant proxy — a trimmed `run-clip.mp4` (≤ 25 MiB) or the failing key-frame `frame-fail.png`. | Oversize artifact remains retrievable out-of-band by its path; proxy is ready for ingest. |
| A1-3 | QA agent calls `add_attachment(source_path=<proxy>, kind="recording", run_id=<run_id>)`. | Proxy is under the cap and an allowed type, so the server persists it per Main Flow Steps 4–6; the ticket carries device evidence. |

## Exception Flows

### E1 – Disallowed content-type (415, blob not written)

| | |
|---|---|
| Branched From: | Main Flow, Step 4 |
| Flow Scenario: | E1 – The artifact's normalized content-type is not in the allowlist (e.g. an `application/zip` log bundle). |
| Post-Condition: | Write rejected before any disk IO; no blob written, no `Attachment` row, no `attachment_added` event; the ticket is unchanged. |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | QA agent calls `add_attachment(source_path=logs.zip, ...)`; the normalized content-type resolves to `application/zip`. | Server finds `application/zip` absent from the allowlist and raises `unsupported_media_type` (HTTP 415) — the allowlist check precedes the disk write, so no blob is ever created. |
| E1-2 | QA agent repackages the evidence into an allowed type (extracts `report.json`, or captures a `.png`) and re-calls `add_attachment`. | Content-type is now in the allowlist; Main Flow proceeds from Step 4 and the ticket receives valid evidence. |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Initial Version | jarwis-pm |
