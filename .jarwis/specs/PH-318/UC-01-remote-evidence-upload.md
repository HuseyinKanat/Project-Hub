# UC-PH-318-01: Remote agent kanıtı byte taşıyarak yükler (add_attachment_content)

> Kaynak format: UseCaseTemplate-StepMethod (Step-Method use case). Bu belge ticket'a
> `add_attachment(kind="usecase")` ile bağlanır ve UI'da popup içinde render edilir.
> Doldurma kuralları: her Main Flow adımı ölçülebilir tek etkileşim; Action = aktörün
> yaptığı, Reaction = sistemin gözlemlenebilir cevabı. Alternate = geçerli varyasyon,
> Exception = hata/başarısızlık yolu. Kullanılmayan bölümü "None" satırıyla bırak, SİLME.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-318-01 |
| Use Case Name: | Remote agent kanıtı byte taşıyarak yükler (add_attachment_content) |
| Description: | Kendi (hub-host OLMAYAN) makinesinde çalışan bir remote QA agent'ı, kanıt dosyasını `source_path` zero-copy ile yükleyemez — çünkü dosya server'ın diskinde değil. Yeni MCP tool `add_attachment_content(id, filename, kind, content_b64, run_id?, phase?)` baytı in-band taşır: server `content_b64`'ü decode eder, mevcut `add_attachment` ile AYNI validasyonlardan (content-type allowlist, 25 MiB post-decode cap, phase/kind/run_id kuralları) geçirir, AYNI `_persist_attachment` çekirdeğine ve AYNI RBAC'a (`attachment.add`) tabi tutar. Sonuç, host-lokal yolla üretilen satırdan byte-for-byte ayırt edilemezdir. |
| Actors: | Remote QA agent (`jarwis-qa@<owner>`) — birincil caller; remote implementer agent'ları (backend/frontend/... — ikincil caller'lar, `attachment.add` yetkili); project-hub server (hub — decode+validate+persist); yönetici/host operatörü (kanıtı UI'da izleyen gözlemci) |
| Triggers: | Remote bir agent, kendi makinesinde bir QA koşumunu (veya fix iterasyonunu) bitirir ve YALNIZ kendi lokal diskinde duran bir artifact'ı ticket'a bağlamak ister; `source_path` bu dosyaya erişemez (paylaşılan dosya sistemi yok). |
| Pre-Conditions: | Multi-user onboarding tamam (PH-317) — remote agent PH board'unda `jarwis-qa@<owner>` kimliği + geçerli token ile authenticate; caller `attachment.add` yetkili üye (implementers+qa+pm); `add_attachment_content` tool'u remote oturumun MCP yüzeyinde açık; artifact remote makinenin lokal diskinde mevcut; phase/kind/run_id kural altyapısı canlı (PH-311 phase migration, PH-313/315 kuralları uygulanmış). |
| Post-Conditions: | Main Flow: `content_b64` decode edilir, allowlist + 25 MiB post-decode + slug validasyonlarından geçer, AYNI `_persist_attachment` ile kalıcı olur; `attachment_added` event yazılır+publish edilir; artifact `list_attachments` + phase-gruplu UI'da (PH-312) görünür ve blob remote orijinaliyle byte-for-byte aynıdır (checksum_sha256 doğrular) · Alternate Flow: host-lokal kullanıcının mevcut `add_attachment(source_path=...)` zero-copy yolu DEĞİŞMEDEN çalışır; iki tool aynı çekirdekte buluşur · Exception Flow: post-decode >25 MiB veya allowlist-dışı content-type → persist ÖNCESİ 422, blob/satır/event yok, ticket değişmez; bozuk b64 → decode aşamasında temiz 422, yan etki yok |
| Includes: | None |
| Extension Points: | FE kanıt phase-hikâye görünümü (PH-312) yeni yüklenen artifact'ı, kaynağı `source_path` mı yoksa `content_b64` mü olduğundan bağımsız, aynı şekilde `list_attachments`'tan tüketir |
| References: | PH-318 (bu ticket) AC'leri; PH-296 (attachments add/list/get + `_persist_attachment` çekirdeği), PH-311 (phase kolonu + slug kuralı), PH-312 (phase-hikâye görünümü), PH-313 (metadata update/delete + RBAC rolleri), PH-315 (kind/run_id kuralları), PH-317 (owner-scoped actors — `jarwis-qa@<owner>`) |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Remote QA agent kendi (hub-host olmayan) makinesinde bir test iterasyonunu bitirir, artifact'ları (recording/report/png) LOKAL diskine yazar ve bir phase slug'ı seçer (ör. `iter-2-pass`). Dosya server diskinde olmadığından `source_path` zero-copy ona erişemez. | Artifact'lar remote makinenin lokal diskinde; phase slug'ı belirlenmiş. |
| 2 | Agent lokal dosyanın byte'larını okur, base64-encode eder ve `jarwis-qa@<owner>` MCP kanalından `add_attachment_content(id=<KEY>, filename=..., kind=..., content_b64=<b64>, run_id=..., phase="iter-2-pass")` çağırır. | Server byte-taşıyan ingest isteğini alır; byte'lar in-band gelir (paylaşılan dosya sistemi gerekmez). |
| 3 | Ingest isteği alındı (Step 2). | Server ÖNCE `attachment.add`'i authorize eder (PH-296 ile aynı RBAC; qa üye) — decode'dan ÖNCE; yetkisiz caller blob materyalize edilmeden reddedilir (büyük b64'ü boşa decode etmez). |
| 4 | Caller yetkili (Step 3). | Server `content_b64`'ü ham byte'lara base64-decode eder; iyi-biçimli b64 tam olarak orijinal byte'ları üretir. Bozuk b64 → temiz decode hatası (E2). |
| 5 | Byte'lar materyalize edildi (Step 4). | Server `source_path` yoluyla AYNI validasyon kapısını koşar: content-type allowlist + boyut ≤ 25 MiB POST-decode cap + phase slug (`^[a-z0-9]+(?:-[a-z0-9]+)*$` ≤40, PH-311) + kind/run_id kuralları (PH-313/315). |
| 6 | Payload geçerli (Step 5). | Server decode edilmiş byte'ları AYNI `_persist_attachment` çekirdeğine verir: byte'ları storage'a stream eder, `Attachment` satırını (phase DAHİL) ekler, `attachment_added` history yazar, event publish eder — host-lokal tool ile birebir aynı persistence yolu. |
| 7 | Satır commit edildi (Step 6). | Server `AttachmentResponse`'u (phase + checksum_sha256 + size ile) döndürür; `list_attachments` artık artifact'ı sunar ve yönetici onu ticket'ın phase-gruplu kanıt hikâyesinde (PH-312) görür — remote orijinaliyle byte-for-byte aynı. |

## Alternate Flows

### A1 – Host-lokal kullanıcı source_path'i değişmeden kullanır

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | A1 – Birincil (host-lokal) kullanıcının agent'ının artifact'ı hub-host diskindedir; byte taşımak yerine mevcut `add_attachment(source_path=...)` zero-copy tool'unu kullanır. |
| Post-Condition: | Mevcut zero-copy yolu tam olarak eskisi gibi davranır (PH-296/PH-311): b64 yok, decode yok; iki tool aynı `_persist_attachment`'te buluşup ayırt edilemez satır üretir. `add_attachment_content` ADDITIVE'dir, `source_path`'i değiştirmez. |
| Branch To: | Main Flow Step 6 (paylaşılan persist) |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Host-lokal agent `add_attachment(id=<KEY>, source_path=<`/repos` altında abs path>, kind=..., phase=...)` çağırır — `content_b64` YOK. | Server path'i PH-296 ile birebir doğrular (mutlak, `..` yok, `/repos` altında, gerçek dosya, ≤25 MiB stat-gate); decode adımı yok. |
| A1-2 | Path geçerli (A1-1). | Server dosyayı zero-copy okur ve AYNI `_persist_attachment` çekirdeğine girer → satır/blob/event byte-taşıyan varyantla aynı; mevcut caller'lar hiçbir davranış değişikliği görmez. |

## Exception Flows

### E1 – Oversize (>25 MiB post-decode) veya allowlist-dışı content-type (422, yan etkisiz)

| | |
|---|---|
| Branched From: | Main Flow, Step 5 |
| Flow Scenario: | E1 – Decode edilmiş byte'lar 25 MiB post-decode cap'ini aşar, VEYA content-type allowlist'te değildir (ör. executable). |
| Post-Condition: | İstek persist ÖNCESİ 422 (unprocessable) ile reddedilir; storage'a blob yazılmaz, satır yok, event yok; ticket değişmez (büyük decode'dan partial state kalmaz). |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Agent, decode edildiğinde >25 MiB olan (veya allowlist-dışı content-type taşıyan) bir `content_b64` gönderir. | Server decode edip decoded boyutu ölçer / content-type'ı sniff eder, kapıyı fail eder → 422; storage'a hiçbir şey stream edilmez, satır yok, event yok. |
| E1-2 | Agent artifact'ı küçültür/normalize eder (ör. recording'i sıkıştırır veya izinli bir tipe export eder) ve tekrar çağırır. | Payload artık cap içinde + allowlisted → Main Flow Step 6'dan devam eder ve artifact kalıcı olur. |

### E2 – Bozuk base64 (decode aşamasında temiz 422)

| | |
|---|---|
| Branched From: | Main Flow, Step 4 |
| Flow Scenario: | E2 – `content_b64` geçerli base64 değildir (transit'te kesilmiş/bozulmuş veya yanlış encode edilmiş). |
| Post-Condition: | Server Step 4'te temiz bir decode hatası (422) döner; decode hiç byte üretmediğinden validasyon yok, persist yok, event yok — ticket dokunulmaz. Hata size/content-type/persist adımlarından ÖNCE kesin (fail-fast). |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E2-1 | Agent `add_attachment_content(..., content_b64="not%%%valid$$$")` çağırır. | Server'ın base64 decode'u fail eder ve temiz, tipli bir decode hatası fırlatır → 422; byte materyalize edilmez, yan etki yok (size/content-type/persist'ten ÖNCE düşer). |
| E2-2 | Agent dosyayı doğru şekilde yeniden encode eder (ham byte'ların proper base64'ü) ve tekrar çağırır. | b64 artık decode olur → Main Flow Step 5'ten (validasyon) devam eder ve normal ilerler. |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-16 | 1.0 | Initial Version — multi-user onboarding: remote byte-carrying evidence upload (`add_attachment_content`) | jarwis-pm |
