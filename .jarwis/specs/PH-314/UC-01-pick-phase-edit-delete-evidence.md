# UC-PH-314-01: Kanıt yükleme fazını seç, düzenle veya sil (UI)

> Kaynak format: UseCaseTemplate-StepMethod. Ticket'a `add_attachment(kind="usecase")` ile
> bağlanır ve UI'da popup içinde render edilir. Action = aktörün yaptığı, Reaction = sistemin
> gözlemlenebilir cevabı; Alternate = geçerli varyasyon, Exception = hata yolu.

## Summary

| Item | Description |
|---|---|
| Use Case ID: | UC-PH-314-01 |
| Use Case Name: | Kanıt yükleme fazını seç, düzenle veya sil (UI) |
| Description: | Ticket detayındaki Kanıtlar bölümünde insan operatör, kanıt yüklerken bir phase seçer (repro / iter-N-fail / iter-N-pass / before / after / boş=faz'sız); mevcut bir kanıtın phase'ini satır menüsünden düzenler (PATCH) veya kanıtı confirm'li sil (DELETE) ile kaldırır. Kontroller RBAC-farkında (yetki yoksa gizli) ve hikâye görünümü değişiklik sonrası anında yenilenir (react-query invalidate). Spec-doc'lara (kind=usecase/testcase) phase SUNULMAZ. |
| Actors: | İnsan operatör (pm/qa → yükle+düzenle+sil; implementer → yükle+düzenle); frontend Kanıtlar bölümü + BE update/delete endpoint'leri |
| Triggers: | Operatör bir ticket detay sayfasını açar ve bir kanıt yükler / mevcut kanıtın phase'ini düzeltir / yanlış kanıtı siler. |
| Pre-Conditions: | BE `update_attachment` + `delete_attachment` canlı (blocked_by DRAFT-be done); PH-312 phase-hikâye görünümü + PH-297 viewer'lar mevcut; operatörün board rolü biliniyor (RBAC gizleme için); yükleme formunda Tür + run_id inputları mevcut (phase seçici bu ticket'ta eklenir). |
| Post-Conditions: | Main Flow: seçilen phase ile kanıt yüklenir ve doğru hikâye grubunda anında görünür (manuel reload yok) · Alternate Flow: phase düzenle → PATCH + anında yeniden gruplama; sil → confirm + DELETE + satır anında düşer · Exception Flow: delete yetkisi yok → kontrol gizli (zorlanırsa BE 403, liste bozulmaz); spec-doc → phase seçici/edit sunulmaz |
| Includes: | None |
| Extension Points: | Yeni phased yükleme `attachment_added` WS event'iyle canlı hikâye yenilenmesini tetikler (PH-297/PH-312'den reuse) |
| References: | DRAFT-fe (bu ticket); blocked_by DRAFT-be (BE metadata CRUD); PH-311 (phase), PH-312 (story view), PH-297 (evidence UI), PH-310 (spec-doc DocPopup); AC1–AC8 |

## Main Flow

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| 1 | Operatör ticket detayında Kanıtlar bölümündeki yükleme formunu açar ve bir evidence kind seçer (spec-doc değil). | Form, Tür + run_id inputlarının yanında bir phase seçici gösterir: `repro / iter-N-fail / iter-N-pass / before / after / (boş=faz'sız)`. |
| 2 | Operatör `iter-N-fail` veya `iter-N-pass` seçer. | Seçici N inputunu mevcut kanıtlardan türetilen öneriyle (gözlenen max iterasyon numarası + 1) prefill eder. |
| 3 | Operatör bir phase (ör. `iter-2-pass`) seçip dosyayı yükler. | FE POST multipart'a `phase` form alanını ekler; başarılı yanıtta ilgili liste query'si invalidate edilir. |
| 4 | Yükleme tamamlanır. | Yeni kanıt seçilen phase'i taşır ve PH-312 hikâye görünümünde doğru gruba anında (manuel reload olmadan) yerleşir. |

## Alternate Flows

### A1 – Mevcut kanıtın phase'ini düzenle (PATCH)

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | A1 – Operatör (update yetkili) bir kanıt satırının phase'ini satır menüsünden düzeltir. |
| Post-Condition: | PATCH gönderilir; hikâye görünümü öğeyi yeni gruba anında taşır (query invalidate); blob değişmez. |
| Branch To: | Main Flow Step 4 (anında yenilenme) |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A1-1 | Operatör kanıt satırında "faz düzenle"yi seçer, yeni phase girer/seçer ve kaydeder. | FE `PATCH /api/tickets/<KEY>/attachments/<id>` gönderir; başarıda liste query'sini invalidate eder ve öğe yeni hikâye grubunda görünür. |

### A2 – Kanıtı sil (confirm + DELETE)

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | A2 – Operatör (pm/qa) yanlış yüklenmiş bir kanıtı satır menüsünden siler. |
| Post-Condition: | Confirm onaylanırsa DELETE gönderilir ve satır listeden anında düşer; iptal edilirse hiçbir istek gitmez, liste değişmez. |
| Branch To: | End |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| A2-1 | Operatör "sil"i seçer; bir confirm diyaloğu çıkar. | Operatör onaylar → FE `DELETE /api/tickets/<KEY>/attachments/<id>` gönderir, liste query'sini invalidate eder, satır düşer. İptal → hiçbir istek gitmez. |

## Exception Flows

### E1 – Delete yetkisi yok (kontrol gizli; zorlanırsa 403)

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | E1 – Operatörün board rolü `attachment.delete` taşımaz (ör. implementer/viewer). |
| Post-Condition: | Sil kontrolü UI'da hiç gösterilmez; bir şekilde tetiklenirse BE 403 döner ve mevcut liste bozulmadan hata yüzeye çıkar. |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E1-1 | Delete yetkisiz operatör bir kanıt satırını görüntüler. | FE, aktörün RBAC'ine göre sil kontrolünü gizler (edit yetkisi varsa "faz düzenle" görünür kalır). |
| E1-2 | Kontrol stale UI nedeniyle bir şekilde zorlanır. | BE 403 döner; FE non-destructive bir hata gösterir, mevcut kanıt listesi bozulmaz (optimistic corruption yok). |

### E2 – Spec-doc'a phase sunulmaz

| | |
|---|---|
| Branched From: | Main Flow, Step 1 |
| Flow Scenario: | E2 – Attachment bir spec-doc (kind=usecase/testcase) — kanıt değil. |
| Post-Condition: | Spec-doc için ne phase seçici ne de phase-düzenle sunulur; phase yalnız evidence kind'larına (recording/screenshot/report/log...) uygulanır. |

| Step | Action/Cause/Stimulus | Reaction/Effect/Response |
|---|---|---|
| E2-1 | Bölüm bir spec-doc satırı (kind=usecase) render eder. | FE bu satır için phase seçici ve phase-düzenle kontrolünü GİZLER; spec-doc DocPopup davranışı (PH-310) değişmeden kalır. |

## Revision History

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-16 | 1.0 | Initial Version | jarwis-pm |
