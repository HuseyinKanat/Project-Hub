# TC-01: ui-readability paket nitleri — mixed-id benzersizliği + over-cap link a11y + light diff kontrast

> StepMethod test case (UseCaseTemplate-StepMethod uyarlaması). Ticket'a
> `add_attachment(kind="testcase")` ile bağlanır; UI'da Test Plan bölümü altında popup render edilir.
> Bir TC tek davranış kümesini sınar; her Expected Response gözlemlenebilir/ölçülebilir.

## Summary

| Item | Description |
|---|---|
| Test Case ID: | TC-PH-306-01 |
| Test Case Name: | parseCriteria mixed explicit+implicit id benzersizliği + over-cap download link'inde false aria-disabled kaldırma + light diff punctuation kontrast (documented acceptance) |
| Description: | plan:ph-ui-readability review/QA'larından kalan kararlaştırılmış nit'leri kanıtlar: (AC3) parseCriteria karışık explicit `AC1:` + id-siz GWT girdisinde tüm kart id'lerinin unique kaldığı (auto-counter reserved id atlar), (AC4) over-cap spec-doc download link'inden false `aria-disabled` kaldırıldığı (fonksiyonel link), (AC2) light diff punctuation kontrastının reprodüksiyonla ≥4.5:1 çıktığı → documented acceptance. AC1 (buildDocSections spec) PH-308'de TicketDocView silindiği için DROP. |
| Related Use Case: | —: chore ticket, UC zorunlu değil |
| Related AC: | AC1 [DROPPED] (buildDocSections/docSections moot — `grep buildDocSections\|docSections` = 0 hit; TicketDocView PH-308'de silindi, commit 7a776ab), AC2 (light diff punctuation kontrast ≥4.5:1 veya documented), AC3 (mixed-id badge uniqueness `Set(ids).size===ids.length`), AC4 (over-cap download link'inde `aria-disabled` yok + href/download/aria-label duruyor) |
| Type / Priority: | edge + negative / P2 (ticket: low) |
| Actors / Environment: | jarwis-qa (Coordinator browser relay); worktree vite dev :5189 (media-light) |
| Test Data: | PH-310 (10 AC checklist); `big-uc.md` over-cap çipi; light tema diff token'ları (punctuation #475569) |
| Pre-Conditions: | parseCriteria pre-scan fix'i mevcut; SpecDocChips over-cap branch render edilebilir; light tema aktif |
| Post-Conditions: | Rozet id'leri unique; over-cap link enabled announce edilir + indirir; palet kodu değişmedi (documented acceptance) |
| References: | PH-306; commit 3896be0, 437caab; `src/lib/criteria/parseCriteria.ts` (+test), `pages/TicketDetail.tsx` SpecDocChips; `.jarwis/logs/PH-306/` |

## Test Steps

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| 1 | TC-1: PH-310 (10 AC) leaf rozetleri render + parseCriteria unit | AC3 — AC1..AC10 rozetleri unique; collision fix unit'i reviewer'da pre-fix FAIL doğruladı (suite 14/14) |
| 2 | TC-2: `big-uc.md` over-cap spec-doc çipi render | AC4 — over-cap çipinde `aria-disabled` YOK; `download=1` linki + 'indir' `aria-label` duruyor |
| 3 | TC-4: node:test suite + tsc (reviewer re-run) | AC3 — node:test 14/14 + tsc clean |

## Negative / Alternate Scenarios

### E1 – Karışık explicit+implicit checklist id çakışması (uniqueness guard)

| | |
|---|---|
| Branched From: | Test Steps, Step 1 |
| Flow Scenario: | E1 – acceptance_criteria açık `AC1:` + id-siz GWT girdisi karışık; auto-counter `AC${n}` çakışması riski |
| Expected Post-Condition: | Tüm kart id'leri unique (`new Set(ids).size === ids.length`); explicit AC1 korunur; pre-fix reviewer'da FAIL |

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| E1-1 | Mixed-id checklist parseCriteria (her iki sıralama) | AC3 — auto-counter reserved/assigned id'leri atlar → unique; explicit AC1 duruyor; pre-fix unit FAIL, post-fix PASS |

### E2 – Light diff punctuation kontrast (documented acceptance)

| | |
|---|---|
| Branched From: | Test Steps, Step 2 / None |
| Flow Scenario: | E2 – Light temada tinted satırda punctuation/operator token kontrastı ölçülür (QA'nın 2.46:1 raporu reprodüksiyonu) |
| Expected Post-Condition: | Ölçülen ≥4.5:1 (AA small text) her iki tint'te → palet kodu shipping edilmez; 2.46 root-cause (dark-artefakt) belgelenir |

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| E2-1 | TC-3: light token punctuation(#475569) add/del tint WCAG ölç | AC2 — add-tint 6.74:1 / del-tint 6.48:1 (≥4.5 AA); 2.46 raporu dark-artefakt teyitli → documented acceptance geçerli |

## Execution Record

| Date | Environment | Result | Evidence | Executed By |
|---|---|---|---|---|
| 2026-07-14 | Coordinator browser relay; worktree vite dev :5189 (media-light) | PASS 4/4 | TC-1 PH-310 (10 AC) leaf rozetler AC1..AC10 unique; collision fix unit reviewer'da pre-fix FAIL doğruladı (14/14); TC-2 `big-uc.md` over-cap çipinde `aria-disabled` YOK, `download=1` linki + 'indir' `aria-label` duruyor; TC-3 light punctuation(#475569) add-tint 6.74:1 / del-tint 6.48:1 (≥4.5 AA; 2.46 raporu dark-artefakt teyitli) → documented acceptance; TC-4 node:test 14/14 + tsc clean. AC1 DROP (grep 0 hit, TicketDocView PH-308'de silindi). commit 3896be0, 437caab. | jarwis-qa (Coordinator browser relay) |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (gerçek koşum kayıtlarından) | jarwis-qa |
