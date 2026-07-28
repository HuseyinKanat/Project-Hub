# UC-01: Rol token'ıyla kimlik ve board üyeliklerini sorgula (whoami)

> Kaynak format: UseCaseTemplate-StepMethod (Step-Method use case). Bu belge ticket'a
> `add_attachment(kind="usecase")` ile bağlanır ve UI'da popup içinde render edilir.
> Doldurma kuralları: her Main Flow adımı ölçülebilir tek etkileşim; Action = aktörün
> yaptığı, Reaction = sistemin gözlemlenebilir cevabı. Alternate = geçerli varyasyon,
> Exception = hata/başarısızlık yolu. Kullanılmayan bölümü "None" satırıyla bırak, SİLME.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-01 |
| Use Case Name: | Rol token'ıyla kimlik ve board üyeliklerini sorgula (whoami) |
| Description: | Rol token'lı bir agent veya kullanıcı, tek read-only `whoami` çağrısıyla kendi aktör kimliğini (display_name, actor_id, role, owner_slug) ve board üyeliklerini öğrenir — paylaşımlı board'a comment yazıp audit trail'i kirletmeden. |
| Actors: | Rol token'lı agent (Jarwis sub-agent) veya kullanıcı |
| Triggers: | Oturum başı identity-smoke; ya da kullanıcının elindeki token'ın hangi aktöre ait olduğunu teşhis etme ihtiyacı |
| Pre-Conditions: | Çağıran geçerli bir rol token'ı taşır (bu feature ÖNCESİ mint'lenmiş mevcut token'lar dahil — rotasyon gerekmez); project-hub erişilebilir |
| Post-Conditions: | Main Flow: kimlik + membership payload'ı döner, sistem durumu değişmez · Alternate Flow: owner_slug @owner token'da slug değeri / owner'sız token'da null · Exception Flow: auth hatası döner, kimlik sızmaz, mutasyon olmaz |
| Includes: | None |
| Extension Points: | None |
| References: | PH-330; PH-330 acceptance_criteria (7 madde: tools/list görünürlük, kimlik çözümleme, owner_slug, board_memberships, read-only audit, rotasyonsuz adoption, auth); identity-smoke protokolü (roles/coordinator.md) |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Actor rol-scoped MCP server üzerinden `whoami` çağırır (argümansız) | Sistem bearer token'ı doğrular ve tek bir aktör kaydına çözer (kimlik token'dan gelir, argümandan değil) |
| 2 | Actor işlemenin tamamlanmasını bekler | Sistem display_name, actor_id (UUID) ve role'ü okur; owner_slug'ı (değer veya null) ve board_memberships'i [{board_key, role}] toplar; tek read-only payload olarak döner — hiçbir ticket/comment/state/history mutasyonu olmaz |
| 3 | Actor dönen kimliği inceler | Actor token'ın hangi aktöre karşılık geldiğini board'a hiçbir şey yazmadan öğrenir; deterministik kimlik teyidi (ör. identity-smoke gate'i) mümkün olur |

## Alternate Flows

### A1 – Çok-kullanıcılı (@owner) token

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | A1 – Token, display_name'i `jarwis-<role>@<owner>` biçiminde olan çok-kullanıcılı bir aktöre ait |
| Post-Condition: | Dönen owner_slug ilgili owner slug'ını (ör. emrehan) taşır; board_memberships o aktörün üyeliklerini yansıtır |
| Branch To: | Main Flow, Step 3 |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Actor `jarwis-<role>@<owner>` biçimli çok-kullanıcı token'ıyla `whoami` çağırır | Sistem owner_slug'ı aktörün owner slug'ıyla doldurur ve Main Flow Step 3 ile devam eder |

### A2 – Owner'sız (tek-kullanıcı) token

| | |
|---|---|
| Branched From: | Main Flow, Step 2 |
| Flow Scenario: | A2 – Token, display_name'i `jarwis-<role>` biçiminde olan, owner atanmamış tek-kullanıcı bir aktöre ait |
| Post-Condition: | Dönen owner_slug null'dur |
| Branch To: | Main Flow, Step 3 |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A2-1 | Actor owner'sız tek-kullanıcı token'ıyla `whoami` çağırır | Sistem owner_slug'ı null döner (single-user semantiği) ve Main Flow Step 3 ile devam eder |

## Exception Flows

### E1 – Geçersiz / eksik / süresi dolmuş token

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | E1 – Çağrıda token yok ya da token geçersiz/süresi dolmuş |
| Post-Condition: | Auth hatası (401/403) döner; hiçbir kimlik/anonim veri sızmaz; hiçbir mutasyon olmaz |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Actor token'sız ya da geçersiz/süresi dolmuş token'la `whoami` çağırır | Sistem token'ı doğrulayamaz; 401/403 auth hatası döner, yanıtta hiçbir kimlik alanı yer almaz |
| E1-2 | Actor hata yanıtını alır | Actor token'ın geçersiz olduğunu anlar; arkada hiçbir audit trail kaydı oluşmamıştır |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-21 | 1.0 | Initial Version | jarwis-pm |
