# UC-PH-299-01: Diff'te dile-özel sözdizimi renklendirme

> Kaynak format: UseCaseTemplate-StepMethod. Ticket'a `add_attachment(kind="usecase")` ile
> bağlanır ve UI'da popup içinde render edilir. Action = aktörün yaptığı, Reaction = sistemin
> gözlemlenebilir cevabı; Alternate = geçerli varyasyon, Exception = hata yolu.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-299-01 |
| Use Case Name: | Diff'te dile-özel sözdizimi renklendirme (HunkView prism token) |
| Description: | Kullanıcı bir kod değişikliğini (FileDiffView/HunkView) görüntülediğinde, prism-react-renderer v2 ile satır-içerik hücreleri dile özel token renkleriyle (keyword/string/comment ayrımı) boyanır; diff semantiği (+/- arka planları, hunk başlıkları, satır numaraları) highlighting sonrası birebir korunur. |
| Actors: | Kullanıcı (diff inceleyen), sistem (FileDiffView + HunkView + prism-react-renderer tokenizer) |
| Triggers: | Kullanıcı bir dosyanın diff'ini açar (FileDiffView → HunkView render). |
| Pre-Conditions: | prism-react-renderer v2 `package.json`'a pinli eklendi (bu ticket prism owner'ı); `file.path` uzantısından dil çıkarımı FileDiffView'dan HunkView'a prop olarak geçer. |
| Post-Conditions: | Main Flow: py/ts/tsx/json/css/md satırları token renkleriyle boyanır, diff semantiği korunur · Alternate Flow: bilinmeyen uzantı → düz-metin fallback · Exception Flow: tokenize edilemeyen satır → o satır plain render, +/- + satır no bozulmaz |
| Includes: | None |
| Extension Points: | None |
| References: | PH-299; AC1–AC5; plan:ph-ui-readability (Dalga A — P1/P3 ile paralel; prism owner) |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Kullanıcı ts/tsx bir dosyanın diff'ini açar. | FileDiffView `file.path`'ten dili çıkarır ve HunkView'a dil prop'u geçer. |
| 2 | HunkView satır-içerik hücrelerini render eder. | Her satır için prism-react-renderer v2 senkron token dizisi üretir; keyword/string/comment/plain token'ları design-token renkleriyle boyanır (AC1). |
| 3 | Renkli token'lar satır içeriğine yerleşir. | Sistem +/- satır arka planlarını, hunk başlıklarını ve satır-numarası kolonlarını token render'ının ÜSTÜNDE birebir korur (AC2). |
| 4 | Kullanıcı 500+ satırlık büyük bir diff'i kaydırır. | Satır-bazlı senkron tokenizasyon sayesinde fark edilir jank olmaz (AC5). |
| 5 | Kullanıcı bir hunk'ı collapse butonuyla katlar/açar. | +/- glyph + satır-no kolonları + renkli token'lar tutarlı kalır. |
| 6 | Kullanıcı meta/bağlam satırlarına bakar. | Dil grameri dışı satırlar plain token olarak render edilir (renksiz ama okunur; diff yapısı korunur). |

## Alternate Flows

### A1 – Bilinmeyen uzantı → düz-metin fallback

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | A1 – Dosya bilinmeyen/desteklenmeyen uzantıya sahip; dil çıkarılamıyor. |
| Post-Condition: | Mevcut düz görünümle render; hata veya boş satır yok, diff semantiği korunur. |
| Branch To: | Main Flow Step 3 (diff semantiği katmanı) |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Kullanıcı bilinmeyen uzantılı dosya diff'ini açar. | HunkView dil bulamaz → satırlar düz-metin olarak render edilir; +/- arka planları ve satır no korunur (AC3). |

## Exception Flows

### E1 – Satır tokenize edilemiyor (gramer kenarı)

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | E1 – prism belirli bir satırı token'layamaz (beklenmedik içerik / gramer kenarı). |
| Post-Condition: | O satır plain render'a düşer; +/- arka planı, hunk başlığı, satır no bozulmaz; boş satır/çökme yok. |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | HunkView bir satırda token dizisi üretemez. | Sistem satırı düz metin olarak gösterir, diff yapısını (arka plan + satır no) korur; render kırılmaz (AC2, AC3). |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (spec-doc paketi) | jarwis-pm |
