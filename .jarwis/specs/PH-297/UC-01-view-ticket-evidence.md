# UC-PH-297-01: Ticket kanıtlarını görüntüle ve yükle

> Kaynak format: UseCaseTemplate-StepMethod. Ticket'a `add_attachment(kind="usecase")` ile
> bağlanır ve UI'da popup içinde render edilir. Action = aktörün yaptığı, Reaction = sistemin
> gözlemlenebilir cevabı; Alternate = geçerli varyasyon, Exception = hata yolu.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-297-01 |
| Use Case Name: | Ticket kanıtlarını görüntüle ve yükle (Evidence UI) |
| Description: | Bir yönetici/kullanıcı ticket detay sayfasını açtığında, PH-296 list endpoint'inden beslenen "Kanıtlar / Ekler" bölümü her attachment'ı (dosya adı, kind/source rozeti, boyut, yükleyen, tarih) run_id'ye göre gruplayarak listeler; png önizlemesi (lightbox), inline mp4 oynatıcı (seek), indirme ve yetkiliyse yükleme kontrolü sunar. |
| Actors: | Yönetici/kullanıcı (ticket inceleyen), QA agent (`jarwis-qa`, MCP ile attachment ekleyen), sistem (frontend Evidence bölümü + backend content endpoint) |
| Triggers: | Kullanıcı bir ticket'ın detay sayfasını (`/boards/:board/tickets/:key`) açar. |
| Pre-Conditions: | PH-296 backend attachment API'leri canlı; content endpoint `?token=` auth'u çalışıyor (headerless `<img>`/`<video>` için load-bearing); kullanıcının rolü en az read/preview/download için yeterli. |
| Post-Conditions: | Main Flow: Kanıtlar bölümü run_id gruplarıyla render, png lightbox + mp4 seek + indirme çalışır · Alternate Flow: yetkili kullanıcı dosya yükler, progressbar %100, liste tam-reload'suz yenilenir · Exception Flow: backend 413/415/403 → inline spesifik hata, mevcut liste değişmez |
| Includes: | None |
| Extension Points: | Yeni attachment MCP ile eklendiğinde `attachment_added` WS event'iyle canlı yenileme (Main Flow Step 7) |
| References: | PH-297; AC1–AC17; blocked_by PH-296 (Ticket evidence attachments) |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Kullanıcı ticket detay sayfasını açar. | Sistem `[ticket-attachments]` query'siyle PH-296 list endpoint'ini çağırır; veri gelene kadar loading göstergesi (AC7). |
| 2 | Liste döner. | Kanıtlar bölümü her attachment'ı dosya adı + kind/source TEXT rozeti (qa-flow \| agent \| human) + boyut + yükleyen + tarih ile, run_id'ye göre gruplayarak (null → varsayılan grup) render eder (AC1, AC11). |
| 3 | Kullanıcı bir png/jpg thumbnail düğmesini tıklama/klavye ile etkinleştirir. | Focus-trap'li lightbox açılır, açıklayıcı alt-text okunur; Esc kapatır ve focus tetikleyiciye döner (AC2, AC12). |
| 4 | Kullanıcı bir mp4 satırında oynat'a basar. | Inline `<video controls>` backend content endpoint'inden `?token=` ile stream eder; tam-sayfa gezinme yok (AC3). |
| 5 | Kullanıcı video'da rastgele konuma seek eder. | Backend Range/206 desteğiyle istenen konum oynar (AC13). |
| 6 | Kullanıcı bir attachment'ın indirme kontrolüne basar. | Tarayıcı dosyayı orijinal adıyla indirir (`download` attr; AC4). |
| 7 | Başka bir client MCP ile attachment ekler (kullanıcı sayfayı açık tutarken). | `attachment_added` WS event'i gelir → Kanıtlar listesi manuel reload olmadan canlı yenilenir (AC10, AC16). |

## Alternate Flows

### A1 – Yetkili kullanıcı dosya yükler

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | A1 – write-rolü (qa / *_dev / pm / admin) kullanıcı bölümün yükleme kontrolüyle multipart dosya yükler. |
| Post-Condition: | Dosya yüklenir; progressbar `aria-valuenow=100`; liste tam-reload olmadan yenilenir, yeni dosya görünür. |
| Branch To: | Main Flow Step 2 (liste re-fetch) |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Yetkili kullanıcı yükleme kontrolünde dosya seçip gönderir. | Sistem FormData multipart POST atar; `Content-Type: application/json` GÖNDERMEZ (boundary korunur; AC17). |
| A1-2 | Yükleme sürer. | `role=progressbar` yüzdeyi yansıtır (AC5, AC15). |
| A1-3 | Backend 201 döner. | Sistem otomatik re-fetch eder; yeni dosya listede görünür, tam-sayfa reload yok (AC5). |

### A2 – Read-only rol: yükleme gizli

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | A2 – Yükleme yetkisi olmayan (read-only) kullanıcı bölümü görüntüler. |
| Post-Condition: | Yükleme kontrolü gizli/disabled; okuma/önizleme/indirme çalışmaya devam eder. |
| Branch To: | Main Flow Step 3 (önizleme/indirme akışı) |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A2-1 | Read-only rol Kanıtlar bölümünü açar. | Yükleme kontrolü gizli/disabled render edilir; liste/video/indirme okunur kalır (AC8, AC14). |

## Exception Flows

### E1 – Backend yüklemeyi reddeder (413 / 415 / 403)

| | |
|---|---|
| Branched From: | Alternate Flow A1, Step A1-3 |
| Flow Scenario: | E1 – Seçilen dosya çok büyük (413) / desteklenmeyen tür (415) / yetkisiz (403). |
| Post-Condition: | Net, insan-okunur inline hata (jenerik toast değil); mevcut liste değişmez. |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Kullanıcı desteklenmeyen tür (ör. pdf) yükler. | Backend 415 döner → sistem inline `aria-live` "Desteklenmeyen tür" gösterir; liste aynı kalır (AC6, AC15). |
| E1-2 | Kullanıcı geçerli tür/boyutta dosyayla tekrar dener. | A1-3'ten devam eder; ticket geçerli kanıt alır. |

### E2 – Liste fetch hatası / boş durum

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | E2 – Attachment listesi fetch'i hata verir ya da ticket'ın hiç attachment'ı yok. |
| Post-Condition: | Fetch hatasında retry'lı hata mesajı; boş durumda empty-state mesajı; UI kilitlenmez. |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E2-1 | Liste fetch'i başarısız olur. | Sistem retry seçenekli hata mesajı gösterir (AC7). |
| E2-2 | Ticket'ın hiç attachment'ı yoktur. | Sistem empty-state mesajı gösterir (loading/empty/error durumları ayrık; AC7, AC11). |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (spec-doc paketi) | jarwis-pm |
