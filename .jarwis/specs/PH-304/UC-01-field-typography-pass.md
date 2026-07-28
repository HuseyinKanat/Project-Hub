# UC-PH-304-01: Alan/markdown tipografi pass'i (reassess-gate'li)

> Kaynak format: UseCaseTemplate-StepMethod. Ticket'a `add_attachment(kind="usecase")` ile
> bağlanır ve UI'da popup içinde render edilir. Action = aktörün yaptığı, Reaction = sistemin
> gözlemlenebilir cevabı; Alternate = geçerli varyasyon, Exception = hata yolu.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-304-01 |
| Use Case Name: | Alan/markdown tipografi pass'i (reassess-gate'li) |
| Description: | P4 (PH-301) + P6 (PH-302) merge olduktan sonra Architect'in reassess-gate'inde kalan ham-markdown okunabilirlik acısı ölçülür; karar (a) ölçülebilir AC'lerle re-scoped tipografi pass'i (MarkdownRenderer başlık hiyerarşisi/spacing/kontrast + `index.css` `.field-body`) VEYA (b) 'mandate P4/P6 ile çözüldü' kanıtıyla kullanıcıya iade. (a) yolunda font-size merdiveni h1=18/h2=16/h3=14/body=13 strictly decreasing olur. |
| Actors: | Architect (gate kararını veren), kullanıcı (alan gövdelerini okuyan), sistem (MarkdownRenderer + `index.css` `.field-body`) |
| Triggers: | PH-301 (P4) ve PH-302 (P6) merge olur → Architect approve turunda reassess-gate açılır. |
| Pre-Conditions: | P4 + P6 merged; MarkdownRenderer mevcut ve bilinçli-token'lı (261 satır); değişiklik yalnız `.field-body` scope'unda (global tema dokunulmaz). |
| Post-Conditions: | Main Flow: (a) başlık/spacing/liste tipografisi iyileşir, font-size merdiveni strictly decreasing + weight/kontrast dark/light'ta doğru · Alternate Flow: (b) kanıtlı iade, kod değişikliği yok · Exception Flow: guardrail ihlali (code block/table/blockquote/link stili değişir) → reddedilir |
| Includes: | None |
| Extension Points: | None |
| References: | PH-304; AC1–AC9; blocked_by PH-301 (P4) + PH-302 (P6); plan:ph-ui-readability (Dalga C sonu, reassess-gate) |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | P4 (PH-301) + P6 (PH-302) merge olur. | Architect reassess-gate'i açar ve kalan residual ham-markdown acısını inceler. |
| 2 | Architect gate kararını verir (a yolu). | Kararı ticket'a yazar: residual analiz özeti + yeni ölçülebilir AC seti (AC1). |
| 3 | (a) yolu uygulanır. | Sistem MarkdownRenderer başlık hiyerarşisi + spacing + kontrast + `index.css` `.field-body`'yi düzenler (YALNIZ `.field-body` scope; global tema dokunulmaz; AC3). |
| 4 | Kullanıcı çok-başlıklı bir alan gövdesi görüntüler. | Computed font-size h1=18 / h2=16 / h3=14 / body=13px; strictly decreasing, bitişik başlık seviyeleri arası ≥2px (AC4). |
| 5 | Kullanıcı paragraf sonrası liste görüntüler. | Her `li` (13px) her başlıktan (min h3=14) küçük; inversiyon yok (AC5). |
| 6 | Kullanıcı çoklu `##` bölümü görüntüler. | h1/h2 üstü margin (≥16px) paragraf-arası boşluktan (8px) strictly büyük (AC6). |
| 7 | Kullanıcı dark ve light modda bakar. | Başlıklar weight ≥600 + text-primary; body 400 + text-secondary (AC7). |
| 8 | Kullanıcı MarkdownCompact görünümüne bakar. | h1≤14, monotonik boyut, taşma yok — kompakt render regresyonsuz (AC8). |

## Alternate Flows

### A1 – (b) mandate P4/P6 ile çözüldü, kullanıcıya iade

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | A1 – Architect residual incelemesinde ham-markdown acısının P4+P6 ile giderildiğini tespit eder. |
| Post-Condition: | Kanıtlı iade önerisi ticket'a yazılır; kod değişikliği yok, mandate kullanıcıya iade edilir. |
| Branch To: | End |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Architect residual'i ölçer. | Ham-markdown acısı P4/P6 ile çözülmüş → gate kararı (b): kanıtlı iade önerisi ticket'a yazılır (AC1). |

## Exception Flows

### E1 – Guardrail ihlali (izin-dışı stil değişikliği)

| | |
|---|---|
| Branched From: | Main Flow, Step 3 |
| Flow Scenario: | E1 – (a) yolundaki bir değişiklik code block/inline/table/blockquote/link stiline dokunur (yalnız başlık/paragraf/liste boyutu + wrapper ritmi olmalıydı). |
| Post-Condition: | Değişiklik reddedilir; code-block/table/blockquote/link stilleri byte-identik kalır. |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Bir değişiklik code-block bg/border/mono stilini değiştirir. | Guardrail (AC9) ihlali → reject; yalnız başlık/paragraf/liste boyutu + ritim diff'ine izin verilir. |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (spec-doc paketi) | jarwis-pm |
