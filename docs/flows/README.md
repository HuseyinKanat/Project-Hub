# Flows

Sequence ve state diyagramları. Her dosyada üstte **Status** satırı bulunur:

- ✅ **Implemented** — kod, test ve docs senkronize.
- 🟡 **Partial** — backend var, UI veya event yayını eksik.
- 📝 **Planned** — sadece tasarım, kod yok.

| Flow | Status | Dosya |
|---|---|---|
| Ticket create | ✅ Implemented | [`ticket-create.md`](./ticket-create.md) |
| State transition + field gates | ✅ Implemented | [`state-transition.md`](./state-transition.md) |
| Claim / release / force-release | ✅ Implemented | [`claim-release.md`](./claim-release.md) |
| Agent phase (live badge) | 🟡 Partial (no WS broadcast yet) | [`agent-phase.md`](./agent-phase.md) |
| Git webhook ingestion | 📝 Planned | [`git-webhook.md`](./git-webhook.md) |
