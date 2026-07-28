# UC-PH-313-01: Kanıt metadata'sını düzelt veya kaldır

> Kaynak format: UseCaseTemplate-StepMethod. Ticket'a `add_attachment(kind="usecase")` ile
> bağlanır ve UI'da popup içinde render edilir. Action = aktörün yaptığı, Reaction = sistemin
> gözlemlenebilir cevabı; Alternate = geçerli varyasyon, Exception = hata yolu.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-313-01 |
| Use Case Name: | Kanıt metadata'sını düzelt veya kaldır |
| Description: | Bir agent veya insan (pm/qa/implementer), mevcut bir attachment'ın YALNIZ metadata'sını (phase / kind / run_id) düzeltir ya da yanlış yüklenmiş bir attachment'ı tümüyle siler. Update blob'a DOKUNMAZ (bytes / storage_key / filename / content_type / size_bytes / checksum_sha256 değişmez); delete satır + blob + event'i kaldırır ve idempotent DEĞİLDİR (ikinci çağrı 404). Her iki işlem de eski→yeni değerlerle audit event'i yazar. |
| Actors: | pm/qa agent'ları (update + delete); implementer agent'ları (yalnız update); UI üzerinden insan operatör (kardeş FE ticket) |
| Triggers: | Bir kanıt yanlış/eksik phase (veya kind/run_id) ile yüklenmiş ve düzeltilmesi gerekiyor; ya da yanlış kind'la yüklenmiş bir fixture (ör. PH-309'daki kind=usecase smoke-doc) silinecek. |
| Pre-Conditions: | Attachment var (PH-296 add ile oluşmuş); caller ticket'ın board'unda gerekli yetkiye üye — `attachment.update` → implementers+qa+pm, `attachment.delete` → yalnız pm+qa; board-roles backfill migration uygulanmış (PH-296 idempotent pattern). |
| Post-Conditions: | Main Flow: yalnız verilen metadata alanları değişir, blob byte-for-byte korunur, `attachment_updated` event'i (old→new) yazılır+publish edilir, yanıt güncel metadata döner · Alternate Flow: delete satırı + blob'u + `attachment_deleted` event'ini kaldırır, öğe `list_attachments`'tan düşer · Exception Flow: geçersiz slug → 422 (persist yok); ikinci delete → 404 (idempotent değil); yetkisiz → 403 (existence sızmaz) |
| Includes: | None |
| Extension Points: | FE faz-düzenle/sil kontrolleri (kardeş ticket, blocked_by bu ticket) bu endpoint'leri tüketir |
| References: | DRAFT-be (bu ticket); PH-296 (attachments add/list/get), PH-311 (phase kolonu), PH-312 (phase-hikâye görünümü); AC1–AC8 |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Yetkili caller bir attachment'ın yanlış/eksik phase (veya kind/run_id) taşıdığını fark eder ve `update_attachment(id=<KEY>, attachment_id=<uuid>, fields={phase:"iter-2-pass"})` çağırır (MCP) — ya da REST `PATCH /api/tickets/<KEY>/attachments/<id>`. | Server metadata-güncelleme isteğini YALNIZ verilen alanlarla alır (partial update). |
| 2 | İstek alındı (Step 1). | Server ÖNCE `attachment.update`'i authorize eder (existence sızmasın diye lookup'tan önce), sonra attachment'ı ticket altında bulur. |
| 3 | Yetki + varlık doğrulandı (Step 2). | Server verilen alanları validate eder: `phase` slug `^[a-z0-9]+(?:-[a-z0-9]+)*$` ≤40 char (null izinli — PH-311 ile aynı kural); `kind` serbest ≤40; `run_id` serbest. Verilmeyen alanlara DOKUNULMAZ. |
| 4 | Alanlar geçerli (Step 3). | Server SADECE metadata kolonlarını update eder; blob bytes / storage_key / filename / content_type / size_bytes / checksum_sha256 hiç okunmaz/yazılmaz (blob IMMUTABLE — byte değişimi için delete + re-add). |
| 5 | Satır güncellendi (Step 4). | Server `attachment_updated` history event'ini eski→yeni değerlerle yazar ve canlı abonelere publish eder. |
| 6 | Commit tamam (Step 5). | Server güncel `AttachmentResponse`'u döndürür; `list_attachments` yeni phase/kind/run_id'i sunar → FE hikâye görünümü öğeyi doğru gruba taşır. |

## Alternate Flows

### A1 – Yanlış yüklenmiş attachment'ı sil (satır + blob + event)

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | A1 – pm/qa, yanlış kind'la yüklenmiş bir attachment'ı (ör. kind=usecase smoke-doc.md) tümüyle kaldırır. |
| Post-Condition: | Attachment satırı silinir, storage_key altındaki blob diskten silinir, `attachment_deleted` event'i (metadata snapshot) yazılır; öğe `list_attachments`'tan düşer. |
| Branch To: | End |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | pm/qa caller `delete_attachment(id=<KEY>, attachment_id=<uuid>)` çağırır (MCP) — ya da REST `DELETE /api/tickets/<KEY>/attachments/<id>`. | Server ÖNCE `attachment.delete`'i (dar RBAC: yalnız pm+qa) authorize eder, sonra attachment'ı ticket altında bulur. |
| A1-2 | Yetki + varlık doğrulandı (A1-1). | Server attachment satırını siler, storage_key altındaki blob'u diskten kaldırır, `attachment_deleted` history event'ini metadata snapshot'ıyla yazar+publish eder ve başarı döner; öğe artık `list_attachments`'ta görünmez. |

## Exception Flows

### E1 – Geçersiz phase slug (422, hiçbir şey persist edilmez)

| | |
|---|---|
| Branched From: | Main Flow, Step 3 |
| Flow Scenario: | E1 – Update payload'ı geçersiz `phase` taşır (büyük harf/boşluk/baş-son tire/`..`/>40 char), ör. `phase="Iter 2!"`. |
| Post-Condition: | İstek persist ÖNCESİ 422 (unprocessable) ile reddedilir; mevcut satır olduğu gibi kalır, blob değişmez, event yok. |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Caller `update_attachment(..., fields={phase:"Iter 2!"})` çağırır. | Server slug-validasyonu fail → 422 alan hatasıyla; disk/DB write yok, mevcut satır değişmez. |
| E1-2 | Caller slug'ı normalize eder (`iter-2-fail`) ve tekrar çağırır. | Phase artık geçerli; Main Flow Step 4'ten devam eder ve metadata güncellenir. |

### E2 – İkinci delete (404, idempotent değil) / yetkisiz caller (403)

| | |
|---|---|
| Branched From: | Alternate Flow A1, Step A1-1 |
| Flow Scenario: | E2 – Zaten silinmiş bir attachment tekrar silinmeye çalışılır; ya da implementer (delete yetkisi yok) delete dener. |
| Post-Condition: | İkinci delete → 404 not_found (crash yok, ticket değişmez); yetkisiz caller → 403 (existence sızmaz), attachment korunur. |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E2-1 | Caller aynı `attachment_id` için `delete_attachment`'ı İKİNCİ kez çağırır. | Satır artık yok → server 404 not_found döner; delete idempotent DEĞİLDİR, ama sunucu sağlıklı kalır. |
| E2-2 | Yalnız `attachment.update` yetkili bir implementer `delete_attachment` çağırır. | Server `attachment.delete` yetkisini bulamaz → 403; attachment silinmez, existence sızdırılmaz. |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-16 | 1.0 | Initial Version | jarwis-pm |
