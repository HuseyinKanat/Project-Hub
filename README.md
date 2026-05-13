# ProjectHub

> Lokal makinede çalışan, MCP-first, Jira-vari proje/ticket yönetim sistemi. Admin (insan) + role-based agent'lar için tasarlandı.

## Hızlı bakış

- **Backend:** FastAPI (Python 3.12) + SQLAlchemy 2 + PostgreSQL + Redis
- **Frontend:** React 18 + Vite + Tailwind + shadcn/ui
- **MCP:** Tek server, tool katalog + event stream
- **Auth:** Bearer token (agent), session (admin)
- **Git:** GitHub entegrasyonu (branch create, webhook ingestion, interleaved timeline)

## Önemli dosyalar (oku önce)

| Dosya | İçerik |
|---|---|
| [`docs/project_plan.md`](docs/project_plan.md) | Tam plan dökümanı (vision, scope, data model, MCP, workflow) |
| [`rules.md`](rules.md) | Agent'ların **uyması zorunlu** kuralları |
| [`skills.md`](skills.md) | Tekrarlayan pattern'ler ve how-to recipe'leri |

## Klasör yapısı

```
project-hub/
├── README.md
├── rules.md                # Agent rules (MUST/MUST NOT)
├── skills.md               # Agent skills (how-to patterns)
├── docker-compose.yml
├── .env.example
├── .gitignore
├── docs/
│   ├── project_plan.md     # Full plan
│   ├── mcp-tools.md        # MCP tool spec
│   ├── permissions.md      # Permission grammar
│   └── flows/              # (TBD) Mermaid sequence diagrams
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── app/
│   │   ├── api/            # REST + MCP routes
│   │   ├── core/           # config, auth, permissions, exceptions
│   │   ├── db/             # models + migrations
│   │   ├── mcp/            # MCP server + tool implementations
│   │   ├── services/       # business logic (permission checks here)
│   │   ├── git/            # GitHub integration + webhook handler
│   │   └── events/         # event bus + websocket gateway
│   └── tests/
└── frontend/
    ├── package.json
    ├── vite.config.ts
    └── src/
        ├── pages/          # Board, TicketDetail, Settings
        ├── components/     # Kanban, TicketCard, ActivityTimeline
        ├── stores/         # Zustand
        ├── api/            # TanStack Query hooks
        ├── ws/             # WebSocket client
        └── types/          # Generated from OpenAPI
```

## İlk çalıştırma

> **⚠ Geliştirme tamamen Docker üzerinden yapılır. Lokal Python/Node kurmaya gerek yok.** Detay: `rules.md` § 1.

Ön gereksinimler: `docker`, `docker compose`, `git`.

```bash
cp .env.example .env
# .env'i düzenle: GITHUB_PAT, ADMIN_PASSWORD, vs.

docker compose up -d                                    # tüm servisler ayağa
docker compose exec backend alembic upgrade head        # DB schema
docker compose exec backend python -m app.cli bootstrap # admin + default board
```

UI: `http://localhost:5173`  
MCP: `http://localhost:8000/mcp`  
API docs: `http://localhost:8000/docs`

## Günlük geliştirme komutları

```bash
docker compose logs -f backend                          # backend log
docker compose exec backend pytest                      # test
docker compose exec backend ruff check .                # lint
docker compose exec backend mypy --strict app           # typecheck
docker compose exec backend alembic revision --autogenerate -m "..."  # yeni migration
docker compose exec frontend npm run typecheck          # frontend tip kontrolü
docker compose exec postgres psql -U projecthub -d projecthub  # DB shell
```

Kaynak kod değişiklikleri host'tan editör ile yapılır — volume mount sayesinde anında container içinde görünür (backend `--reload`, frontend Vite HMR).

Mobile erişim için Tailscale önerilir (host makinede çalışır, container'da değil). `docs/operations.md` (TBD).
