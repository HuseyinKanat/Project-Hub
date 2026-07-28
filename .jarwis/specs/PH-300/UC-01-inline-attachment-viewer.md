# UC-PH-300-01: Attachment inline önizleme (JSON pretty-print + log/metin)

> Kaynak format: UseCaseTemplate-StepMethod. Ticket'a `add_attachment(kind="usecase")` ile
> bağlanır ve UI'da popup içinde render edilir. Action = aktörün yaptığı, Reaction = sistemin
> gözlemlenebilir cevabı; Alternate = geçerli varyasyon, Exception = hata yolu.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-300-01 |
| Use Case Name: | Attachment inline önizleme (JSON pretty-print + log/metin) |
| Description: | Kanıtlar UI'ında (PH-297) bir JSON (test_results) ya da log/metin attachment'ında kullanıcı "Görüntüle"ye bastığında, içerik `?token=`'lı content endpoint'inden size-cap'li fetch'lenir; JSON pretty-print (2-space) + top-level fold, log/metin ise monospace scrollable pane + wrap toggle olarak inline render edilir. |
| Actors: | Kullanıcı (kanıt inceleyen), sistem (AttachmentItem + `fetchAttachmentText` + content endpoint) |
| Triggers: | Kullanıcı Kanıtlar bölümünde bir JSON/log/metin attachment'ının "Görüntüle" düğmesine basar. |
| Pre-Conditions: | PH-297 Kanıtlar bölümü canlı; attachment `isTextLike` (mime veya `.log`/`.txt`/`.json` uzantı sniff'i — logcat çoğu kez text/plain ya da octet-stream gelir); content endpoint `?token=` auth'u çalışıyor; `package.json`'a dokunulmaz (prism owner P2/PH-299). |
| Post-Conditions: | Main Flow: JSON pretty (2-space) + top-level fold inline; mp4/png davranışları regresyonsuz (AC5) · Alternate Flow: log/metin monospace pane + wrap toggle · Exception Flow: size-cap aşımı → 'ilk N KB / tamamını indir', UI kilitlenmez; 403 → inline hata |
| Includes: | None |
| Extension Points: | Prism (P2/PH-299) merge olduysa JSON için progressive token renklendirme; yoksa düz monospace |
| References: | PH-300; AC1–AC5; plan:ph-ui-readability (Dalga A — P1/P2 ile paralel, package.json'a dokunmaz) |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Kullanıcı kind=report/json bir attachment satırında "Görüntüle"ye basar. | Sistem `fetchAttachmentText` ile `?token=`'lı content endpoint'inden içeriği (size-cap altında) çeker (AC4). |
| 2 | İçerik gelir. | Sistem `isTextLike` + JSON tespiti yapar (mime `application/json` ya da `.json` uzantı sniff'i) ve `JSON.parse` eder (AC1). |
| 3 | JSON ayrıştırılır. | Sistem içeriği 2-space stringify ile pretty-print eder ve top-level fold ile render eder (suite/passed/failed inline; ▸cases dizisi katlanabilir; AC1). |
| 4 | Kullanıcı 'Tümünü aç/kapat' ile top-level node'ları açar. | Dizi/nesne içerikleri inline genişler (ör. ▸cases '[ 4 öğe ]' → array içeriği açılır). |
| 5 | Kullanıcı aynı ticket'ta mp4/png kanıtlarına da bakar. | Mevcut mp4/png davranışları regresyonsuz kalır (video inline, png önizleme; AC5). |

## Alternate Flows

### A1 – Log/metin önizleme (monospace + wrap toggle)

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | A1 – Attachment `.log`/`.txt` (ya da text/plain / uzantı-sniff'li octet-stream). |
| Post-Condition: | Monospace scrollable pane; wrap toggle ile satır kaydırma açılır/kapanır; içerik okunur. |
| Branch To: | End |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Kullanıcı `seed.log.txt`'de "Görüntüle"ye basar. | Sistem içeriği monospace scrollable pane'de gösterir (AC2). |
| A1-2 | Kullanıcı "Satır kaydırma" toggle'ına basar. | `aria-pressed` true→false; `whiteSpace` pre-wrap→pre değişir (AC2). |

## Exception Flows

### E1 – Size-cap aşımı → indirme fallback

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | E1 – Dosya size-cap'in üstünde (ör. 600KB log). |
| Post-Condition: | 'İlk N KB + tamamını indir' fallback'i sunulur; içerik fetch'i atılmaz; UI kilitlenmez. |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Kullanıcı `big-qa.log` (600KB) satırına bakar. | "Görüntüle" yerine 'çok büyük — tamamını indir' fallback'i gösterilir; content fetch atılmaz (AC3). |

### E2 – Content fetch 403

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | E2 – `fetchAttachmentText` content endpoint'inden 403 alır (yetki yok). |
| Post-Condition: | Inline hata gösterilir; UI kilitlenmez, diğer içerik etkilenmez. |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E2-1 | Fetch 403 döner. | Sistem inline hata mesajı gösterir; kullanıcı listenin geri kalanını kullanmaya devam eder (AC4). |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (spec-doc paketi) | jarwis-pm |
