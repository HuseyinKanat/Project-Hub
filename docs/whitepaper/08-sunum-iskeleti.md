# Sunum İskeleti — Jarwis × project-hub

> **Bir cümlede:** Bu doküman, whitepaper setini birkaç slaytlık bir sunuma çeviren hazır iskelettir — her slayt için başlık, ekranda görünecek özet, konuşmacı notu, önerilen görsel ve kaynak doküman verilir.

Bu iskelet iki kesim sunar: **(A) Çekirdek 12-slayt anlatısı** (tam sunum) ve **(B) 6-slayt kısa kesim** (lightning talk). Anlatı yayı: **problem → tez → iki bileşen → birleşim → 4 hedef → ölçülmüş kanıt → kapanış**. Görseller için ilgili dokümanlardaki mermaid diyagramları doğrudan ekran görüntüsü/yeniden-çizim olarak kullanılabilir.

Tasarım notu: her slaytta **tek bir mesaj** olsun; bullet'lar konuşma değil, hatırlatma. Konuşmacı notları cümleyi taşır.

---

## A) Çekirdek anlatı — 12 slayt

### Slayt 1 — Başlık
- **Ekranda:** "Kontrol Altında Agentic Geliştirme — Jarwis × project-hub"; alt başlık: *davranış kuralları + state-of-truth motoru*.
- **Konuşmacı notu:** LLM ajanları kod yazabiliyor; asıl mesele onları günlerce, kontrollü, hafızalı ve denetlenebilir çalıştırabilmek. Bu sunum o sistemi anlatıyor.
- **Görsel:** İki-kutu logo/şema (Jarwis ↔ project-hub).
- **Kaynak:** [01](01-vizyon-amac.md), [00](00-index.md)

### Slayt 2 — Problem: agentic geliştirme neden kontrolden çıkar
- **Ekranda:** 4 başlık — Görünürlük yok · Durum kaybı · Teknik borç görünmez · Context-switch'te bağlam kaybı.
- **Konuşmacı notu:** Dört sorun birbirini besler: görünürlük olmadan durum kaybı fark edilmez, durum kaybı borcu gizler, context-switch birikmiş bağlamı siler. Klasik araçlar (Jira) insanı varsayar; ajan ikinci sınıf vatandaş.
- **Görsel:** 4 kutu → kısır döngü oku.
- **Kaynak:** [01 §1](01-vizyon-amac.md)

### Slayt 3 — Tez: sorumlulukları ayır
- **Ekranda:** "State + hafıza + kontrol" (project-hub) **ayrı**, "Davranış + orkestrasyon" (Jarwis) **ayrı". Tablo: bileşen / rol / doğa.
- **Konuşmacı notu:** Ayrım keyfi değil — değişim hızları farklı. State katmanı sağlam mühendislikle bir kez kurulur; davranış katmanı markdown olduğu için deneyle/benchmark ile özgürce iterate edilir ve kalıcı veriyi asla bozmaz.
- **Görsel:** [01](01-vizyon-amac.md) `graph TB` sistem mimarisi (User → Coordinator → MCP → DB/UI; sub-agent → repo).
- **Kaynak:** [01 §2](01-vizyon-amac.md)

### Slayt 4 — Bileşen 1: project-hub (state-of-truth motoru)
- **Ekranda:** Tek servis katmanı → 3 yüzey (REST · WebSocket · MCP); "kural tek noktada → 3 yüzeyde garantili tutarlı". Stack rozetleri.
- **Konuşmacı notu:** API de MCP de *aynı* servis fonksiyonunu çağırır; state machine + permission tek yerde yaşar. "Ajan başka bir yoldan kuralı atlattı" sınıfı hatalar baştan elenir. MCP ayrı deployment değil — FastAPI içinde bir route grubu.
- **Görsel:** [02](02-projecthub-mimari.md) `graph TB` katman mimarisi.
- **Kaynak:** [02 §1](02-projecthub-mimari.md)

### Slayt 5 — State machine + field gate'ler (kontrolün kalbi)
- **Ekranda:** 7-state diyagramı; 3 zorunlu gate tablosu (in_review←technical_depth+AC · in_test←test_plan · done←impact_analysis).
- **Konuşmacı notu:** Bir transition dört testi sırayla geçer: edge var mı → actor yetkili mi → permission var mı → field gate dolu mu. Derinlik dökümante edilmeden ticket fiziksel olarak ilerleyemez. "claim = assignee" eşitliği otonom agent deadlock'unu kapatır.
- **Görsel:** [02](02-projecthub-mimari.md#state-machine-ve-field-gateler) `stateDiagram-v2`.
- **Kaynak:** [02 §3](02-projecthub-mimari.md#state-machine-ve-field-gateler)

### Slayt 6 — Bileşen 2: Jarwis + single-driver Coordinator
- **Ekranda:** "Coordinator hem orchestrator HEM transition driver. Sub-agent state'e DOKUNMAZ." N×M hata → 1×M.
- **Konuşmacı notu:** Tüm permission/invalid_transition/missing-tool hataları tek noktada toplanır; sub-agent simple kalır. Enforcement davranışla değil **tool whitelist** ile: sub-agent'ın araç listesinde transition tool'u fiziksel olarak yok → "yasak" değil "yapamaz".
- **Görsel:** [04](04-jarwis-ruleset.md) `graph TB` üç katman + [01](01-vizyon-amac.md) `sequenceDiagram` (tek prompt akışı).
- **Kaynak:** [04 §3](04-jarwis-ruleset.md#single-driver-mimari)

### Slayt 7 — Roller, flow ve handoff
- **Ekranda:** 6 rol tablosu (sorumluluk + state etkisi); feature/bug/hotfix/refactor flow'ları; `[HANDOFF X→Y]`.
- **Konuşmacı notu:** Aynı rol havuzu, farklı giriş/sıra. Bug flow QA-first (önce failing test). Severity kısayolu yok — kritik bug bile gate'lerden geçer; gerçek aciliyet hotfix flow'una gider. Veri devri ticket alanları + handoff yorumlarıyla, ağızdan ağıza değil.
- **Görsel:** [04](04-jarwis-ruleset.md#transition-map) transition `sequenceDiagram`.
- **Kaynak:** [04 §4, §6, §7](04-jarwis-ruleset.md#roller)

### Slayt 8 — Birleşim: üç katman tek pipeline'da
- **Ekranda:** Per-role token izolasyonu (6 token, 1 endpoint, 6 actor); ticket = kontrat yüzeyi; `jarwis-init.sh` tek komut.
- **Konuşmacı notu:** Her rol kendi bearer token'ıyla bağlanır → ticket history'de gerçek kimlik görünür (audit). Tüm kurulum tek idempotent komutla; multi-project'te aynı 6 actor, board membership ile izolasyon. Bağlantı dokusu Coordinator'da.
- **Görsel:** [05](05-entegrasyon-mimari.md) `graph TD` üç-katman + iki-katmanlı izolasyon `graph LR`.
- **Kaynak:** [05 §1, §2, §5](05-entegrasyon-mimari.md)

### Slayt 9 — Görünürlük + kalite: git, SonarQube, canlı UI
- **Ekranda:** Conventional commit → ticket linkage; branch graph (merged/open ring); SonarQube "Scan now = enqueue, watcher = execute"; canlı WebSocket Kanban + heartbeat.
- **Konuşmacı notu:** Backend read-only / never-500 / cache-first — entegrasyon arızası pipeline'ı asla bloklamaz. Kara kutu → cam kutu: her agent değişikliği ticket'a bağlanır, görsel doğrulanır, kalite borcu ölçülür. SonarQube durumu dürüst (`no_analysis` ≠ `unreachable`), secret-free.
- **Görsel:** [03](03-projecthub-entegrasyonlar.md) commit→WS→frontend `sequenceDiagram`.
- **Kaynak:** [03](03-projecthub-entegrasyonlar.md)

### Slayt 10 — Dört hedef → somut mekanizma
- **Ekranda:** 4 sütun: Long-term memory (4 katman) · Context-switch (okuma zinciri) · Kontrol (7 katman) · Technical depth (gate'ler).
- **Konuşmacı notu:** Hedefler soyut istek değil, mimariye gömülü. Hafıza dört katmanlı ve hiçbiri silinmez; yeni oturum audit→handoff→log→codewiki→memory zinciriyle sıfır yeniden-keşifle devam eder; kontrol 7 katmanda; derinlik field gate'lerle zorunlu.
- **Görsel:** [06](06-hedefler-derinlemesine.md) hafıza akışı `graph TD` + kontrol katmanları `graph TD`.
- **Kaynak:** [06](06-hedefler-derinlemesine.md#long-term-memory)

### Slayt 11 — Ölçülmüş kanıt: optimizasyon + snapshot equivalence
- **Ekranda:** İter tablosu (iter-0 $8.88/9.44M → iter-5 $6.29/5.70M); "Davranış korunmadıkça kazanç sayılmaz"; Serena reddi.
- **Konuşmacı notu:** Token tasarrufu kolay; zor olan davranışı koruyarak tasarruf. Sabit pilot + snapshot fingerprint (state_path + fields_present + handoff) her adımda korundu. En büyük kazanç tool-trim (−39.5%). Serena ölçüldü, net negatif çıktı, default'a alınmadı — ölçüp reddetme cesareti.
- **Görsel:** [07](07-optimizasyon-yolculugu.md) metodoloji `flowchart LR` + iter tablosu.
- **Kaynak:** [07](07-optimizasyon-yolculugu.md)

### Slayt 12 — Kapanış: mühendislik kültürü
- **Ekranda:** "Sistem sadece çalışmıyor — ne kadara çalıştığını biliyoruz ve davranışını bozmadan ucuzlatabiliyoruz." 3 alınacak ders.
- **Konuşmacı notu:** (1) Sorumluluk ayrımı (state vs davranış) iterasyonu güvenli kılar. (2) Kontrol tek sürücüde + tool whitelist + field gate ile yapısal. (3) Hafıza + ölçüm, agentic geliştirmeyi tahmin edilebilir kılar. Kapanışta canlı kanıtları vurgula (codewiki 98 işlem/105 ref, PH-148/PH-168 gibi gerçek-test düzeltmeleri).
- **Görsel:** [01](01-vizyon-amac.md) WHY/WHAT/WHEN `graph LR` üçgeni.
- **Kaynak:** [06](06-hedefler-derinlemesine.md), [07 §10](07-optimizasyon-yolculugu.md)

---

## B) 6-slayt kısa kesim (lightning talk)

| # | Slayt | Mesaj | Kaynak |
|---|---|---|---|
| 1 | **Problem** | Ajanlar kod yazar; kontrol/görünürlük/hafıza yoksa kaos | [01 §1](01-vizyon-amac.md) |
| 2 | **Tez + iki bileşen** | State (project-hub) ile davranışı (Jarwis) ayır | [01 §2](01-vizyon-amac.md) |
| 3 | **Kontrol** | Single-driver Coordinator + state machine + field gate | [04 §3](04-jarwis-ruleset.md#single-driver-mimari) · [02 §3](02-projecthub-mimari.md#state-machine-ve-field-gateler) |
| 4 | **Hafıza + birleşim** | 4 katmanlı hafıza · ticket kontrat yüzeyi · WHY/WHAT/WHEN | [06 §1](06-hedefler-derinlemesine.md#long-term-memory) · [05 §4](05-entegrasyon-mimari.md#why-what-when-ucgeni-ve-codewiki) |
| 5 | **Kanıt** | $8.88→$6.29, snapshot equivalence ile davranış korundu | [07](07-optimizasyon-yolculugu.md) |
| 6 | **Kapanış** | Ölçülen, denetlenebilir, hafızalı agentic geliştirme | [00](00-index.md) |

---

## Konuşma yayı (tek paragraf — açılış/kapanış için)

> "LLM ajanları artık kod yazabiliyor; asıl problem onları kontrollü, görünür ve hafızalı çalıştırabilmek. Biz bunu sorumluluğu ikiye bölerek çözdük: **project-hub** durumu, hafızayı ve insan kontrol yüzeyini tutan sağlam bir state-of-truth motoru; **Jarwis** ise davranışı yöneten, kod içermeyen bir markdown kural seti. Tek bir Coordinator state machine'i sürer, sub-agent'lar state'e dokunamaz; teknik derinlik field gate'lerle zorunlu; hafıza dört katmanda ve hiçbiri silinmiyor. Ve bütün bunu ölçüyoruz — davranışı bozmadan maliyeti %29 düşürdük, hatta bir optimizasyonu (Serena) ölçüp reddedecek kadar disiplinli. Sonuç: sadece çalışan değil, *ne kadara çalıştığını bildiğimiz ve davranışını bozmadan iyileştirebildiğimiz* bir agentic geliştirme sistemi."

---

## Görsel envanteri (dokümanlardan hazır mermaid'ler)

| Görsel | Tip | Kaynak | Hangi slayt |
|---|---|---|---|
| Sistem mimarisi (User→Coordinator→MCP→DB/UI) | `graph TB` | [01](01-vizyon-amac.md) | 3 |
| Tek-prompt Coordinator akışı | `sequenceDiagram` | [01](01-vizyon-amac.md) | 6 |
| WHY/WHAT/WHEN üçgeni | `graph LR` | [01](01-vizyon-amac.md) | 12 |
| project-hub katman mimarisi | `graph TB` | [02](02-projecthub-mimari.md) | 4 |
| Veri modeli | `erDiagram` | [02](02-projecthub-mimari.md) | (yedek) |
| State machine | `stateDiagram-v2` | [02](02-projecthub-mimari.md#state-machine-ve-field-gateler) | 5 |
| commit→WS→frontend | `sequenceDiagram` | [03](03-projecthub-entegrasyonlar.md) | 9 |
| Üç katman modeli | `graph TB` | [04](04-jarwis-ruleset.md) | 6 |
| Transition map akışı | `sequenceDiagram` | [04](04-jarwis-ruleset.md#transition-map) | 7 |
| Eager/lazy/never import | `graph LR` | [04](04-jarwis-ruleset.md#eager-vs-lazy-import) | (yedek) |
| Üç-katman bağlanışı + token izolasyonu | `graph TD` / `graph LR` | [05](05-entegrasyon-mimari.md) | 8 |
| Uçtan uca tek-prompt akışı | `sequenceDiagram` | [05](05-entegrasyon-mimari.md) | 8 (yedek) |
| Hafıza akışı + kontrol katmanları | `graph TD` | [06](06-hedefler-derinlemesine.md) | 10 |
| Metodoloji ölçüm akışı | `flowchart LR` | [07](07-optimizasyon-yolculugu.md) | 11 |
| Serena karar grafiği | `graph TD` | [07](07-optimizasyon-yolculugu.md) | 11 (yedek) |

---

## İlgili dokümanlar

- [00 — Genel bakış ve okuma sırası](00-index.md)
- [01 — Vizyon ve amaç](01-vizyon-amac.md)
- [02 — project-hub mimarisi](02-projecthub-mimari.md)
- [03 — project-hub entegrasyonları](03-projecthub-entegrasyonlar.md)
- [04 — Jarwis ruleset](04-jarwis-ruleset.md)
- [05 — Entegrasyon mimarisi](05-entegrasyon-mimari.md)
- [06 — Hedefler derinlemesine](06-hedefler-derinlemesine.md)
- [07 — Optimizasyon yolculuğu](07-optimizasyon-yolculugu.md)
