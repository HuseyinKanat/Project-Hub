# UC-01: "Genel Bakış" tab'ini aç ve epic-progress'i görüntüle

> Kaynak format: UseCaseTemplate-StepMethod (Step-Method use case). Bu belge ticket'a
> `add_attachment(kind="usecase")` ile bağlanır ve UI'da popup içinde render edilir.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-01 |
| Use Case Name: | "Genel Bakış" tab'ini aç ve epic-progress'i görüntüle |
| Description: | Kullanıcı board detay sayfasında yeni "Genel Bakış" tab'ine geçer; eskiden board'un üstünde (BoardDetail.tsx:476) mount edilen epic-progress paneli artık bu tab'in içinde görünür. Tab seçimi URL hash'inde (`#overview`) kalıcıdır. |
| Actors: | Board kullanıcısı (viewer / member) |
| Triggers: | Kullanıcı "Genel Bakış" tab'ine tıklar VEYA `#overview` hash'li bir URL açar |
| Pre-Conditions: | Board detay sayfası yüklü; kullanıcı board'u görüntüleme yetkisine sahip |
| Post-Conditions: | Main Flow: overview tab aktif, EpicProgressPanel tab içinde render, `location.hash === "#overview"`, üstteki eski mount kaldırılmış · Alternate Flow: A1 `#overview` derin linkiyle tab başlangıçta aktif · Exception Flow: E1 progress endpoint hatasında panel inline degrade eder, board bozulmaz |
| Includes: | None |
| Extension Points: | PH-339 — Türkçe özet bölümleri + görsel milestone timeline + editor aynı `#overview` paneline eklenir |
| References: | PH-337 (bu ticket), PH-335 (EpicProgressPanel + UC-01 E1 inline-degrade), AC1–AC5 |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Kullanıcı board detay sayfasını açar | Sistem tab strip'i render eder (Kanban / Branch Graph / Quality / Space / Genel Bakış); EpicProgressPanel ARTIK tab strip'in üstünde çizilmez |
| 2 | Kullanıcı "Genel Bakış" tab'ine tıklar | Sistem overview panelini aktifleştirir ve `switchTab` ile URL hash'ini `#overview` yapar (aria-selected geçer) |
| 3 | Overview paneli render olur | Sistem `EpicProgressPanel`'i `#panel-overview` içinde gösterir; bileşen davranışı (progress rollup) korunur |
| 4 | Kullanıcı başka bir tab'e geçer | Sistem ilgili paneli gösterir + hash'i günceller; overview içeriği board'un tepesinde tekrar belirmez (tek mount, tab içinde) |

## Alternate Flows

### A1 – `#overview` derin linki

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | A1 – Kullanıcı doğrudan `#overview` hash'li URL ile sayfaya gelir |
| Post-Condition: | Overview tab'i başlangıçta aktif olarak açılır |
| Branch To: | Main Flow Step 3 |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Kullanıcı `.../boards/<key>#overview` URL'ini açar | `initialTab()` hash'i okur ve overview tab'ini aktif başlatır (diğer hash'li tab'lerle aynı kalıp) |

## Exception Flows

### E1 – Progress endpoint hatası

| | |
|---|---|
| Branched From: | Main Flow, Step 3 |
| Flow Scenario: | E1 – EpicProgressPanel'in progress query'si hata verir |
| Post-Condition: | Hata INLINE panelde gösterilir; tab strip ve diğer tab'ler çalışır kalır (board bozulmaz) |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Progress query fail döner | Panel PH-335 UC-01 E1 davranışını korur (inline hata/degrade); overview tab'i ve diğer içerik erişilebilir kalır |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-08-03 | 1.0 | Initial Version | jarwis-pm |
