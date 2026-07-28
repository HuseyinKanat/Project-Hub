# UC-02: Review evidence on a ticket

> Kaynak format: UseCaseTemplate-StepMethod. Ticket'a `add_attachment(kind="usecase")` ile
> bağlanır ve UI'da popup içinde render edilir. Action = aktörün yaptığı, Reaction = sistemin
> gözlemlenebilir cevabı; Alternate = geçerli varyasyon, Exception = hata yolu.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-02 |
| Use Case Name: | Review evidence on a ticket |
| Description: | A human reviewer/manager inspects the device-run evidence attached to a ticket — listing artifacts grouped by run, playing recordings, viewing images and logs, and downloading — as a read-only activity that never mutates the ticket. |
| Actors: | Manager / Reviewer (human board member) |
| Triggers: | A human wants to verify a ticket's device-run evidence (proof of a pass/fail) before approving or closing the work. |
| Pre-Conditions: | Ticket has at least one attachment; the viewer holds a valid bearer token and is a member of the ticket's board with the `ticket.read` permission. |
| Post-Conditions: | Main Flow: evidence streamed/rendered to the viewer (seekable video, Lightbox images, inline json/log) and downloadable; ticket state unchanged (read-only) · Alternate Flow: None · Exception Flow: a non-member request is denied with HTTP 403 and no metadata or bytes are disclosed (existence not leaked) |
| Includes: | None |
| Extension Points: | None |
| References: | PH-296 (Ticket evidence attachments); `GET /api/tickets/{id}/attachments` (list) + `/{attachment_id}/content` (Range/`nosniff`, `?token=`, `?download=1`) |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Human opens the ticket in the project-hub web UI. | UI fetches ticket detail and issues `GET /api/tickets/PH-296/attachments` with bearer auth. |
| 2 | List response received (from Step 1). | Server enforces `ticket.read` and returns attachment metadata oldest→newest; the UI renders an "Evidence" section, grouping the entries by `run_id`. |
| 3 | Human expands a `run_id` group. | UI shows the per-artifact rows for that run — filename, kind, content_type, and human-readable size. |
| 4 | Human clicks a `video/mp4` recording. | The `<video>` element requests `GET .../attachments/{id}/content?token=<t>` (query-param auth because media tags cannot set headers); server serves with Range support (`206` + `Content-Range`, `Accept-Ranges: bytes`) so the human can seek/scrub the timeline. |
| 5 | Human clicks an `image/png` screenshot. | UI opens a Lightbox; the `<img>` requests `.../content?token=<t>`; server streams the image inline with `X-Content-Type-Options: nosniff`. |
| 6 | Human opens an `application/json` / `text/plain` report or log. | Server serves the bytes inline; the UI renders the report/log for reading. |
| 7 | Human clicks Download on an artifact. | UI requests `.../content?download=1&token=<t>`; server responds with `Content-Disposition: attachment` and the original filename, downloading the file. |

## Alternate Flows

None

## Exception Flows

### E1 – Unauthorized access (403, board non-member)

| | |
|---|---|
| Branched From: | Main Flow, Step 1 (list or any content fetch) |
| Flow Scenario: | E1 – A viewer who is NOT a member of the ticket's board attempts to read the evidence. |
| Post-Condition: | Request denied with HTTP 403 `permission_denied`; no attachment metadata and no bytes are disclosed (resource existence is not leaked). |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | A non-member (valid token, but no membership on the ticket's board) issues `GET /api/tickets/PH-296/attachments` or a `.../content` URL. | Server evaluates `require_permission(ticket.read)`, fails, and raises `permission_denied` (HTTP 403); neither metadata nor bytes are returned. |
| E1-2 | The request arrives at the content route with a missing or invalid token. | The content authenticator (bearer header or `?token=`) resolves no valid actor and raises `permission_denied` (HTTP 403) before any file access. |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Initial Version | jarwis-pm |
