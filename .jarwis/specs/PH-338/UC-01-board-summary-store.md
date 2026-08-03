# UC-01: Per-board proje özetini API/MCP üzerinden oku ve yaz

> Kaynak format: UseCaseTemplate-StepMethod (Step-Method use case). Bu belge ticket'a
> `add_attachment(kind="usecase")` ile bağlanır ve UI'da popup içinde render edilir.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-01 |
| Use Case Name: | Per-board proje özetini API/MCP üzerinden oku ve yaz |
| Description: | Coordinator (agent, MCP write) veya board üyesi (REST/UI), bir board'un TEKİL proje özetini (Türkçe bölümler + milestones) okur veya upsert eder. Board başına en fazla bir özet tutulur (0..1). |
| Actors: | Coordinator (agent — MCP write, epic kapanışında), board üyesi (REST/UI upsert), herhangi board-member agent (MCP read) |
| Triggers: | Epic kapanışında Coordinator MCP write; UI'dan upsert; agent/UI MCP/REST read |
| Pre-Conditions: | Board mevcut; çağıran board üyesi; write için yazma-yetkili rol (pm/admin/orchestrator) |
| Post-Conditions: | Main Flow: özet tekil olarak persist edilir/döner, write→read tutarlı · Alternate Flow: A1 ilk oluşturma (create) vs mevcut güncelleme (update) tek kayıt · Exception Flow: E1 non-member/yetkisiz veya geçersiz veri reddedilir, kısmi yazım yok |
| Includes: | None |
| Extension Points: | None |
| References: | PH-338 (bu ticket), PH-336 (board_notes precedent — additive tablo + 404→403 gate + 3-seam MCP), services/progress.py (auth sırası), AC1–AC7 |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Çağıran board'un özetini ister (REST `GET /api/boards/{id}/summary` veya MCP `get_board_summary(board)`) | Sistem `get_board` (yoksa 404) → `require_board_member` (403) kontrol eder; özeti (bölümler + milestones + son güncelleme meta) read-only döner |
| 2 | Coordinator/kullanıcı özeti upsert eder (REST `PUT/POST` veya MCP `set_board_summary`/`update_board_summary`) | Sistem yazma yetkisini doğrular (board üyesi + pm/admin/orchestrator), board başına TEKİL kaydı oluşturur/günceller |
| 3 | Sonraki read (REST GET / MCP get_board_summary) | Sistem yazılan içeriği döner (write→read tutarlı) |

## Alternate Flows

### A1 – İlk oluşturma vs güncelleme

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | A1 – Board'un henüz özeti yok (create) veya zaten var (update) |
| Post-Condition: | Her iki durumda da board başına TEK kayıt/satır kalır (kopya yok) |
| Branch To: | Main Flow Step 3 |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Özet yokken upsert | Yeni tekil kayıt oluşur (create) |
| A1-2 | Özet varken upsert | Mevcut kayıt güncellenir; ikinci satır/kopya OLUŞMAZ (UNIQUE(board_id) veya tekil-kolon semantiği) |

## Exception Flows

### E1 – Yetkisiz yazma / geçersiz veri

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | E1 – Non-member veya read-only rol upsert dener; ya da geçersiz milestone status / boş zorunlu alan gönderilir |
| Post-Condition: | REST 403/422 (MCP isError); hiçbir kısmi yazım veya kopya kalmaz |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Non-member / yazma-yetkisiz rol upsert eder | REST 403 / MCP isError (permission_denied); kayıt değişmez |
| E1-2 | Geçersiz milestone status veya boş zorunlu alan gönderilir | 422; kayıt değişmez (kısmi yazım yok) |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-08-03 | 1.0 | Initial Version | jarwis-pm |
