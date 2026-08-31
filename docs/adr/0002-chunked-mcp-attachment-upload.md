# ADR-0002: Chunked MCP attachment upload for remote agents

## Status

Accepted (PH-341).

## Context

MCP-only remote agents (e.g. `jarwis-*@emrehan`, running OFF the hub host) must
attach large evidence — device `.mp4` recordings, logs, step PNGs — up to the
25 MiB `attachment_max_bytes` cap. Before this change there were exactly three
ingest paths and **none covered `remote + > 8 MiB`**:

| Path | Limit | Remote agent? |
|---|---|---|
| `add_attachment(source_path=…)` | file must be under the hub host's read-only `/repos` mount | ❌ a remote file is not there |
| `add_attachment_content(content_b64=…)` | `attachment_mcp_max_bytes` = 8 MiB (the whole JSON-RPC body is buffered in RAM before dispatch) | ✅ but only ≤ 8 MiB |
| `POST /api/tickets/{id}/attachments` (multipart, 25 MiB) | — | ⚠️ raw HTTP — forbidden for agents by `contracts/mcp-discipline.md` §2.9 (breaks MCP isolation + audit trail) |

So remote + over-8-MiB evidence had no MCP-discipline-compliant path; the
`add_attachment_content` description even conceded large files "MUST use REST
multipart upload instead." Storage backend, verified in code, is **local disk**
(`attachments_root=/data/attachments`, server-generated UUID shard `{id[:2]}/{id}`)
— there is no S3/MinIO/object store. Live data: 1900 attachments, 23 over 8 MiB,
largest 23 MB — a real but small tail that MCP-only agents currently cannot produce.

## Decision

Add a chunked MCP upload protocol with three new tools:

- `add_attachment_begin` opens an authorized upload session (row in a new
  `attachment_upload_sessions` table) and returns `{ upload_id, next_seq,
  chunk_max_bytes, max_total_bytes, expires_at }`.
- `add_attachment_chunk` streams the bytes as a sequence of ≤ 8 MiB UNWRAPPED-base64
  chunks, appended to a staging blob under `attachments_root/.uploads/{id[:2]}/{id}`.
- `add_attachment_commit` finalizes by re-streaming the staged file through the
  existing `_persist_attachment` core, then drops the session row + staging blob.

Per-chunk decoded size is capped at the existing `attachment_mcp_max_bytes` (8 MiB,
no peak-RAM regression); cumulative size at `attachment_max_bytes` (25 MiB). Session
ownership (`author_id`), ordering (`next_seq`), and a TTL (`attachment_upload_ttl_seconds`,
default 3600 s) live on the session row; a GC cron (`upload_session_gc_cron`) reaps
abandoned sessions. No config cap is raised; no docker-compose/infra change (staging
shares the existing attachments volume).

## Rationale

1. **Every byte stays inside an authenticated MCP tool call** → the audit trail and
   per-call `attachment.add` RBAC are preserved and there is no raw HTTP (satisfies
   the ticket's "ham REST'e düşmeden" and mcp-discipline §2.9).
2. **Per-message peak RAM is identical to today's 8 MiB inline path** — chunking removes
   the single-oversized-message hazard that `attachment_mcp_max_bytes` exists to bound.
3. **`_persist_attachment` is reused verbatim** for the final size/type/checksum/row/history/event,
   so the new path adds **zero** divergent write logic and inherits every existing gate.
4. On local-disk storage, server-side chunk assembly is the natural fit; a presigned
   URL buys nothing here.

## Rejected Alternatives

**MCP-issued presigned upload URL.** Rejected for three independent reasons, any one
decisive:

1. **Violates the ticket's own premise.** The agent would `PUT` raw bytes to a URL =
   raw HTTP — precisely what mcp-discipline §2.9 forbids and what AC-1 says to avoid.
   A signed URL is a bearer that, if logged/intercepted, is replayable within its TTL —
   a new **non-MCP authorization surface**, violating AC-2 ("MCP dışı bir yetki yüzeyi
   açmaz").
2. **Zero benefit on this storage backend.** Presigned URLs pay off only against an
   object store that ingests the PUT directly (S3/GCS/MinIO). Storage here is a local
   named volume, so we would STILL have to write our own authenticated upload endpoint +
   streaming persist — i.e. all the backend work of the chunked path PLUS an
   HMAC sign/verify/nonce/replay layer.
3. **One-time-use still needs server state.** Guaranteeing single-use requires
   server-side nonce tracking (DB/Redis) — the same state cost as chunk sessions, with
   more moving parts.

Net: strictly more code, more attack surface, less MCP-native. Reconsider only if/when
attachments migrate to a real object store.

## Consequences

- (+) Remote agents can upload to the full 25 MiB cap without leaving MCP.
- (+) `add_attachment_content` (≤ 8 MiB, single call) stays the simple path; chunked is
  for the larger tail (measured: 23 / 1900 attachments > 8 MiB, max 23 MB).
- (−, disclosed) Each chunk's base64 still passes through the model transcript
  (N tool round-trips of ≤ ~11 MiB base64 each). Chunking removes the peak-RAM /
  single-oversized-message problem, NOT the fact that bytes traverse the transcript.
  This is the accepted cost of staying MCP-native (the raw-HTTP escape is forbidden);
  it is bounded per chunk and the agent emits chunks programmatically (write-only — it
  never reasons over the base64).
- (−) One new table (`attachment_upload_sessions`) + one background GC cron to own
  (abandoned-session cleanup).

### Implementation note — sha256 verification order (refinement of the design)

The design sketched "persist, then compare `attachment.checksum_sha256`, mismatch →
409". Implemented instead as **pre-persist verification**: when the caller supplies
`sha256`, `commit_upload_session` hashes the staging file and compares BEFORE calling
`_persist_attachment`. A mismatch therefore raises 409 with the session (and staging
bytes) preserved for retry and creates **no** attachment row — strictly better than the
literal order, which would commit a bogus attachment on every mismatch and duplicate it
on retry. The stored checksum still matches the source on success (same bytes are
persisted). Behavior otherwise matches the design.
