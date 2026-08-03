# UC-01: "Genel Bakış" tab'inde özeti ve milestone'ları görüntüle/düzenle

> Kaynak format: UseCaseTemplate-StepMethod (Step-Method use case). Bu belge ticket'a
> `add_attachment(kind="usecase")` ile bağlanır ve UI'da popup içinde render edilir.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-01 |
| Use Case Name: | "Genel Bakış" tab'inde özeti ve milestone'ları görüntüle/düzenle |
| Description: | Kullanıcı overview (`#overview`) tab'inde Türkçe özet bölümlerini (amaç/durum/ilerleme/highlights) ve görsel milestone timeline'ını görür; board üyesi içerikleri düzenleyip kaydeder (PH-338 REST upsert). |
| Actors: | Board kullanıcısı (viewer — görüntüleme), board üyesi (düzenleme) |
| Triggers: | Kullanıcı overview tab'ini açar; "düzenle" → "kaydet" |
| Pre-Conditions: | PH-337 tab iskeleti mevcut (`#overview`); PH-338 REST + tipler mevcut; board görüntülenebilir; düzenleme için board üyesi |
| Post-Conditions: | Main Flow: bölümler + görsel milestone timeline render, düzenleme PH-338'e kaydedilir · Alternate Flow: A1 summary henüz yok → boş-state + editor girişi · Exception Flow: E1 kaydetme hatasında girilen içerik korunur |
| Includes: | None |
| Extension Points: | None |
| References: | PH-339 (bu ticket), PH-337 (tab iskeleti), PH-338 (summary REST + tipler), PH-336 (UC E1 body-preserve kalıbı), AC1–AC7 |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Kullanıcı `#overview` tab'ini açar | Sistem PH-338 GET ile özeti çeker; yükleniyor durumunu (loading) gösterir |
| 2 | Özet gelir | Sistem amaç / genel durum / ilerleme / highlights bölümlerini (maddelendirilmiş) + `order`'a göre görsel milestone timeline'ını (durum renkli: planlı/aktif/tamam) render eder; epic-progress paneli de tab'de görünür |
| 3 | Board üyesi "düzenle"ye basar | Sistem editor'ü açar (bölümler + milestone'lar düzenlenebilir) |
| 4 | Kullanıcı kaydeder | Sistem PH-338 upsert'i çağırır; başarı sonrası refetch/invalidate ile güncel içeriği gösterir |

## Alternate Flows

### A1 – Henüz özet yok

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | A1 – GET summary null/404 döner (board'un henüz özeti yok) |
| Post-Condition: | Anlamlı boş-state + oluştur/düzenle girişi gösterilir |
| Branch To: | Main Flow Step 3 |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Summary yok | Sistem "henüz özet yok" boş-state'ini + oluştur/düzenle CTA'sını gösterir |

## Exception Flows

### E1 – Kaydetme hatası

| | |
|---|---|
| Branched From: | Main Flow, Step 4 |
| Flow Scenario: | E1 – Upsert REST çağrısı hata verir (403 / 422 / 5xx) |
| Post-Condition: | Inline hata gösterilir; kullanıcının girdiği içerik KORUNUR (veri kaybı yok), retry mümkün |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Upsert fail döner | Sistem inline hata gösterir; girilen bölüm/milestone içeriği form'da korunur (PH-336 UC E1 kalıbı) |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-08-03 | 1.0 | Initial Version | jarwis-pm |
