# Git Webhook Ingestion Flow

**Status:** 📝 Planned (Phase 4)
**Spec:** `docs/project_plan.md` §8 (Git Integration)
**Code:** _yok_ — `backend/app/git/` ve `backend/app/api/webhooks/` henüz boş iskelet.

## Goal

GitHub webhook'larından gelen `push` ve `pull_request` event'lerini ticket key'ine bağlayıp `TicketHistory`'ye `git_*` event'leri olarak yazmak; UI'da [interleaved activity timeline](../project_plan.md#73-interleaved-activity-timeline) için tek kaynak oluşturmak.

## Planned Endpoint

```
POST /api/webhooks/github/{board_id}?secret=<webhook_secret>
X-GitHub-Event: push | pull_request
X-Hub-Signature-256: sha256=...
```

## Sequence

```mermaid
sequenceDiagram
    autonumber
    actor GH as GitHub
    participant API as POST /api/webhooks/github/{board_id}
    participant Sig as verify HMAC signature<br/>(per-board secret)
    participant Parse as parse payload<br/>regex \b([A-Z]+-\d+)\b
    participant Svc as services.git.link_*
    participant Hist as write_history
    participant DB as Postgres
    participant Redis as Redis pub-sub

    GH->>API: webhook delivery + HMAC
    API->>Sig: verify with board.webhook_secret
    alt invalid signature
        Sig-->>GH: 401
    end

    alt event = push
        API->>Parse: for each commit, extract TICKET_KEY
        loop each (commit, ticket_key)
            Parse->>Svc: link_commit(ticket_key, sha, message, author, url)
            Svc->>DB: ensure ticket exists in this board
            Svc->>Hist: write_history(git_commit_linked,<br/>metadata={sha, message, author, url})
            Svc->>Redis: PUBLISH git_commit_linked
        end
    else event = pull_request (opened)
        Parse->>Svc: link_pr(ticket_keys[], pr_url, title, author)
        Svc->>Hist: write_history(git_pr_opened, metadata={pr_url, title})
        Svc->>Redis: PUBLISH git_pr_opened
    else event = pull_request (closed, merged=true)
        Svc->>Hist: write_history(git_pr_merged)
    else event = pull_request (closed, merged=false)
        Svc->>Hist: write_history(git_pr_closed)
    end

    API-->>GH: 200 { processed: N }
```

## Branch Create (outbound, MCP tool)

```mermaid
sequenceDiagram
    autonumber
    actor Agent
    participant Svc as services.git.create_branch_for_ticket
    participant GH as GitHub API (PyGithub)
    participant Hist
    participant DB

    Agent->>Svc: create_branch_for_ticket(ticket_id, base_branch="main")
    Svc->>Svc: require_permission("git.create_branch")
    Svc->>DB: SELECT ticket (key, title)
    Svc->>Svc: branch = "<KEY>-<slugify(title)>"
    Svc->>GH: get_branch(base_branch).commit.sha
    Svc->>GH: create_ref(refs/heads/<branch>, sha)
    Svc->>Hist: write_history(git_branch_created, metadata={branch, base})
    Svc-->>Agent: { branch, url }
```

## Ticket Key Parsing

- Regex: `\b([A-Z]+-\d+)\b`
- Bir commit/PR birden fazla ticket key içerebilir → her birine ayrı history satırı.
- Bilinmeyen / silinmiş ticket key'leri loglanır ama webhook 200 döner (delivery retry'ı önlemek için).

## Out of Scope (v1)

- ❌ Otomatik PR-merge → state transition (manuel kalır; bkz. `project_plan.md` §8.5).
- ❌ GitLab/Bitbucket — sadece GitHub.
- ❌ Commit body-level "fixes #123" semantic kapatma.
