# TC-01: Rol chip renk map'i — tüm Jarwis rolleri + bilinmeyen deterministik fallback

> StepMethod test case (UseCaseTemplate-StepMethod uyarlaması). Ticket'a
> `add_attachment(kind="testcase")` ile bağlanır; UI'da Test Plan bölümü altında popup render edilir.
> Bir TC tek davranış kümesini sınar; her Expected Response gözlemlenebilir/ölçülebilir.

## Summary

| Item | Description |
|---|---|
| Test Case ID: | TC-PH-307-01 |
| Test Case Name: | ROLE_TOKEN tüm Jarwis rollerini kapsar; map-dışı rol deterministik hash→palet fallback alır; terminal token muted korunur |
| Description: | CommentCard `ROLE_TOKEN` map'inin eksik 10 rolü (android-dev, ios-dev, unity-dev, unity-scene-manager, unity-platform, data-engineer, data-labeler, ml-engineer, ml-analyst, coordinator) tanıması, map-dışı rolün deterministik hash→palet (`--lane-*`) fallback alması (tek muted renge düşme YOK), curated rollerin korunması ve terminal token'ların (done/user) muted mono kalması bug-fix'ini kanıtlar. |
| Related Use Case: | —: bug ticket, UC zorunlu değil |
| Related AC: | AC1 (10 yeni rol renkli chip, muted default değil), AC2 (bilinen 16 rol deterministik tek renk), AC3 (map-dışı 'future-role' hash fallback — null/görünmez/tek-muted DEĞİL), AC4 (HANDOFF terminal token done/user muted mono korunur), AC5 (unit: yeni roller + fallback, mevcut 8 regresyonsuz), AC6 (ml_engineer map-dışı → `--lane-*` renkli, her render aynı), AC7 (pm curated `--role-pm`, label değişmedi), AC8 (html.light fallback `--lane-*` override), AC9 (unit resolveRoleChip determinism + known/unknown/null) |
| Type / Priority: | negative + edge / P1 (ticket: high) |
| Actors / Environment: | jarwis-qa (Coordinator browser relay); worktree vite dev :5186; sentetik (vite dev modül import — gerçek resolver) |
| Test Data: | Sentetik hint'ler: ml_analyst, unity_dev, android_dev, future_role, pm, qa, backend_dev, ml_engineer, null/'' |
| Pre-Conditions: | `ROLE_TOKEN` + index.css `:root`/`html.light` `--role-*`/`--lane-*` token seti güncel; anahtarlar server `agent_role_hint` ile hizalı (underscore/dash toleransı; coordinator↔orchestrator alias) |
| Post-Conditions: | Her rol deterministik renk; map-dışı hash fallback; null/'' → chip yok |
| References: | PH-307; commit b11988c; `CommentCard.tsx` ROLE_TOKEN + index.css `--role-*`/`--lane-*`; `.jarwis/logs/PH-307/` |

## Test Steps

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| 1 | TC-1: map-dışı + yeni roller resolve edilir | AC1/AC3/AC6 — ml_analyst→`--lane-sky`, unity_dev→`--lane-teal`, android_dev→`--lane-cyan`, future_role→`--lane-indigo`; 4/4 FARKLI hue; hiçbiri null/muted |
| 2 | TC-2: curated roller resolve edilir | AC7 — pm/qa/backend_dev → `--role-*` curated korundu (backend_dev label 'be') |
| 3 | TC-3: determinism ×2 | AC2/AC9 — tüm hint'lerde determinism ×2 (aynı rol → aynı renk) |
| 4 | TC-4: dark + light var'lar + null | AC8 — tüm `--lane-*`/`--role-*` var'ları dark VE light'ta tanımlı |

## Negative / Alternate Scenarios

### E1 – Terminal token + null/'' girdi

| | |
|---|---|
| Branched From: | Test Steps, Step 4 |
| Flow Scenario: | E1 – HANDOFF terminal token (done/user) + null/'' rol hint |
| Expected Post-Condition: | done/user muted mono stili korunur; null/'' → chip render edilmez (görünmez, hash'lenmez) |

| Step | Action/Cause/Stimulus | Expected Reaction/Effect/Response |
|---|---|---|
| E1-1 | null/'' rol hint + done/user terminal token | AC4 — null/'' → chip yok; done/user muted mono korunur |

## Execution Record

| Date | Environment | Result | Evidence | Executed By |
|---|---|---|---|---|
| 2026-07-14 | Coordinator browser relay; worktree vite dev :5186; sentetik (vite dev modül import — gerçek resolver) | PASS 4/4 | TC-1 ml_analyst→`--lane-sky`, unity_dev→`--lane-teal`, android_dev→`--lane-cyan`, future_role→`--lane-indigo` — 4/4 FARKLI hue, hiçbiri null/muted; TC-2 pm/qa/backend_dev → `--role-*` curated korundu (backend_dev label 'be'); TC-3 determinism ×2 tüm hint'lerde; TC-4 tüm `--lane-*`/`--role-*` var'ları dark VE light'ta tanımlı, null/'' → chip yok. commit b11988c. | jarwis-qa (Coordinator browser relay) |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-14 | 1.0 | Retroactive backfill (gerçek koşum kayıtlarından) | jarwis-qa |
