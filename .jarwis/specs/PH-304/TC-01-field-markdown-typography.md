# TC-01: Field/markdown tipografi pass'i — başlık hiyerarşisi + ritim + kontrast

> StepMethod test case (UseCaseTemplate-StepMethod uyarlaması). Ticket'a
> `add_attachment(kind="testcase")` ile bağlanır; UI'da Test Plan bölümü altında popup render edilir.
> Bir TC tek davranış kümesini sınar; her Expected Response gözlemlenebilir/ölçülebilir.

## Summary

| Item | Description |
|---|---|
| Test Case ID: | TC-PH-304-01 |
| Test Case Name: | Field gövde tipografisi — monoton azalan başlık boyutu, başlık>li, başlık margin>paragraf ritmi, weight/renk; code-block guardrail byte-identik |
| Description: | Reassess-gate'in RESCOPE (a) yoluyla kesinleşen tipografi pass'ini kanıtlar: MarkdownRenderer başlık boyutları strictly decreasing (h1=18/h2=16/h3=14/body=13), liste boyutu başlıklardan küçük, başlık üst-margin (≥16px) paragraf boşluğundan (8px) büyük, başlık weight≥600+text-primary / body 400+text-secondary; code-block/inline/table/blockquote stilleri byte-identik. |
| Related Use Case: | UC-PH-304-01 (Field markdown'ı okunur başlık hiyerarşisinde gör) |
| Related AC: | AC1 (gate kararı ticket'a yazıldı — RESCOPE (a)), AC4 (h1=18/h2=16/h3=14/body=13 strictly decreasing, bitişik ≥2px), AC5 (li < başlık, inversiyon yok), AC6 (başlık üst-margin ≥16 > paragraf 8), AC7 (başlık ≥600+primary / body 400+secondary), AC8 (MarkdownCompact regresyonsuz), AC9 (code/inline/table/blockquote/link byte-identik) |
| Type / Priority: | regression + edge / P2 (ticket: low) |
| Actors / Environment: | jarwis-qa (Coordinator browser relay); worktree vite dev :5184 |
| Test Data: | Canlı PH-296 field'ları + sentetik element-selector CSS prob; reviewer built-CSS |
| Pre-Conditions: | P4 (PH-301) + P6 (PH-302) merged → Architect residual gate kararı RESCOPE (a); index.css değişiklikleri yalnız `.field-body` scope'unda |
| Post-Conditions: | Field gövde başlık/spacing/liste tipografisi iyileşti; MarkdownRenderer tüketicileri + code-block regresyonsuz |
| References: | PH-304; ticket `test_plan` (4/4 TC, karma kanıt); reviewer built-CSS 18(text-lg)/14(text-base); `.jarwis/logs/PH-304/` |

## Test Steps

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| 1 | TC-1: Canlı PH-296 field'ında başlık/paragraf/liste boyutlarını ölç | AC4/AC5 — h2=16px/600/primary, p=13px, li=13px (inversiyon YOK); h1/h3 kanıt zinciri: canlı h2 ölçümü + reviewer built-CSS 18(text-lg)/14(text-base) + diff sınıf haritası birebir |
| 2 | TC-2: Dikey ritim ölçülür | AC6 — canlı h2-üstü 16px, p-arası 8px, ilk çocuk 0; sentetik prob (element-selector CSS): h1-üstü 16px, h3-üstü 12px |
| 3 | TC-3: Ağırlık + renk ölçülür | AC7 — başlık 600 + primary; body 400 + secondary |

## Negative / Alternate Scenarios

### E1 – Code-block / table / blockquote guardrail (byte-identik)

| | |
|---|---|
| Branched From: | Test Steps, Step 1 |
| Flow Scenario: | E1 – Guardrail stilleri değişmemeli (diff yalnız başlık/paragraf/liste boyutu + wrapper ritmi) |
| Expected Post-Condition: | code-block/inline/table/blockquote/link byte-identik; MarkdownCompact regresyonsuz |

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| E1-1 | TC-4: code-block guardrail kontrol | AC9/AC8 — bg-inset + 1px border + mono değişmedi |

## Execution Record

| Date | Environment | Result | Evidence | Executed By |
|---|---|---|---|---|
| 2026-07-14 | Coordinator browser relay; worktree vite dev :5184; PH-296 canlı + sentetik prob | PASS (kanıt zinciri karma: canlı + built-CSS + sentetik-ritim) | TC-1 canlı h2=16px/600/primary, p=13px, li=13px (inversiyon YOK); h1/h3 = canlı h2 ölçümü + reviewer built-CSS 18(text-lg)/14(text-base) + diff sınıf haritası birebir; TC-2 canlı h2-üstü 16px / p-arası 8px / ilk çocuk 0 + sentetik h1-üstü 16px / h3-üstü 12px; TC-3 başlık 600+primary / body 400+secondary; TC-4 code-block bg-inset + 1px border + mono değişmedi. | jarwis-qa (Coordinator browser relay) |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (gerçek koşum kayıtlarından) | jarwis-qa |
