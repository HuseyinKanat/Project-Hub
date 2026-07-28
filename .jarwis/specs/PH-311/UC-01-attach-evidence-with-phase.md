# UC-PH-311-01: Attach evidence with a phase label

> Kaynak format: UseCaseTemplate-StepMethod. Ticket'a `add_attachment(kind="usecase")` ile
> bağlanır ve UI'da popup içinde render edilir. Action = aktörün yaptığı, Reaction = sistemin
> gözlemlenebilir cevabı; Alternate = geçerli varyasyon, Exception = hata yolu.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-311-01 |
| Use Case Name: | Attach evidence with a phase label |
| Description: | Kanıt yükleyen bir agent (QA veya implementer), her artifact'ı düzeltme kronolojisindeki yerini belirten opsiyonel bir `phase` slug'ıyla etiketler (repro / iter-N-fail / iter-N-pass / before / after). Server phase'i slug-validate eder, attachment satırında saklar ve list/get'te döndürür — böylece düzeltme "hikâyesi" sonradan kurulabilir. Backward-compat: phase omitted → NULL. |
| Actors: | QA agent (`jarwis-qa`), implementer agent'ları (backend/frontend/... kanıt üreten) |
| Triggers: | Bir agent reproduce koşumunu veya bir fix iterasyonunu bitirir ve artifact'ları düzeltme zaman çizelgesindeki yerini kaydedecek şekilde ticket'a bağlar. |
| Pre-Conditions: | `phase` kolonu canlı (migration uygulanmış); ticket var; caller ticket'ın board'unda `attachment.add` yetkili üye; artifact'lar `$HOME` altında mutlak host path'lerde (in-container `/repos`); bir phase slug'ı seçilmiş (veya bilinçli omitted). |
| Post-Conditions: | Main Flow: attachment geçerli bir `phase` ile kalıcı; `attachment_added` event yazılır; `phase` list/get ile döner · Alternate Flow: phase omitted → NULL saklanır, tam geriye-uyumlu (PH-296/PH-297) · Exception Flow: geçersiz phase slug → persist ÖNCESİ 422, satır yok, blob yok, event yok, ticket değişmez |
| Includes: | None |
| Extension Points: | FE kanıt phase-hikâye görünümü (kardeş ticket) `list_attachments`'tan `phase`'i tüketir |
| References: | DRAFT-be (bu ticket); PH-296 (attachments), PH-297 (evidence UI); AC1–AC7 |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Agent bir repro/fix iterasyonunu bitirir, artifact'ları host `$HOME` (in-container `/repos`) altına yazar ve bir phase slug'ı seçer (ör. `repro`, `iter-2-pass`). | Artifact'lar diskte mutlak path'lerde; phase slug'ı belirlenmiş. |
| 2 | Agent `add_attachment(id=<KEY>, source_path=<abs path>, kind=..., run_id=..., phase="repro")` çağırır (MCP) — ya da REST multipart'ta `phase` form alanıyla. | Server ingest isteğini `phase` parametresiyle alır. |
| 3 | Ingest isteği alındı (Step 2). | Server ÖNCE `attachment.add`'i authorize eder, sonra path'i (mutlak, `..` yok, `/repos` altında, gerçek dosya) + size'ı (≤ 25 MiB stat-gate) + content-type allowlist'i doğrular (PH-296 pipeline'ı değişmez). |
| 4 | Payload geçerli (Step 3). | Server `phase`'i `^[a-z0-9]+(?:-[a-z0-9]+)*$` ≤40 char'a karşı slug-validate eder; NULL/omitted bu kontrolü atlar. |
| 5 | Phase geçerli (Step 4). | Server byte'ları storage'a stream eder, `Attachment` satırını `phase` DAHİL ekler, `attachment_added` history yazar, event publish eder. |
| 6 | Satır commit edildi (Step 5). | Server `AttachmentResponse`'u `phase` ile döndürür; `list_attachments` artık artifact'ı phase'iyle sunar → FE hikâye görünümü besleniyor. |

## Alternate Flows

### A1 – Phase omitted (geriye-uyumlu, NULL)

| | |
|---|---|
| Branched From: | Main Flow, Step 4 |
| Flow Scenario: | A1 – Caller `phase`'i omit eder (veya null gönderir) — legacy PH-296/PH-297 davranışı. |
| Post-Condition: | Attachment `phase = NULL` ile kalıcı; yanıt `phase` alanını `null` serialize eder; FE `run_id` gruplamasına fallback eder. |
| Branch To: | Main Flow Step 5 (persist) |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Caller `add_attachment`'ı phase argümanı OLMADAN çağırır. | Server slug-validasyonu atlar (NULL izinli) ve satırı `phase = NULL` ile persist eder. |
| A1-2 | Yanıt döner. | `AttachmentResponse.phase` null; PH-297 `run_id`-gruplu görünüm değişmeden render eder. |

## Exception Flows

### E1 – Geçersiz phase slug (422, hiçbir şey persist edilmez)

| | |
|---|---|
| Branched From: | Main Flow, Step 4 |
| Flow Scenario: | E1 – Phase slug-validasyonundan geçemez (büyük harf, boşluk, baş/son tire, `..` veya >40 char), ör. `phase="Iter 2!"`. |
| Post-Condition: | İstek persist ÖNCESİ 422 ile reddedilir; blob yok, satır yok, event yok; ticket değişmez. |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Agent `add_attachment`'ı `phase="Iter 2!"` ile çağırır. | Server slug-validasyonu fail → 422 (unprocessable) alan hatasıyla; disk write yok, satır yok. |
| E1-2 | Agent slug'ı normalize eder (`iter-2-fail`) ve tekrar çağırır. | Phase artık geçerli; Main Flow Step 5'ten devam eder ve artifact phase'iyle saklanır. |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-16 | 1.0 | Initial Version | jarwis-pm |
