# Planning Council — ph-ui-readability
Kaynak istek (kullanıcı, 2026-07-14): project-hub UI okunabilirlik paketi — (1) ticket field formatları okunaksız, yorumlar tekdüze/uzun; (2) AC/TC alanları klasik SW süreç dokümanları (use-case/test-case şablonu) tarzında yapılandırılsın + popup döküman viewer; (3) JSON/logcat attachment içeriği okunabilir olsun (şu an sadece dosya linki); (4) kod değişikliği (diff) görünümünde dile özel rendering yok, kötü görünüyor; (5) board/ticket sayfaları kendini ekranın ortasına daraltıyor (geniş ekranda saçma kısıt); (6) "genel UI tasklarım var" (belirsiz). Talimat: paralel olanlar ayrı worktree_lerde full-force paralel geliştirilecek (cap=3).

## [COUNCIL R1 pm] 2026-07-14 15:10

Repo taraması: FE = React + TS + Tailwind, tek design-token katmanı (`index.css` "Cyan on Black"; light/dark var override). İlgili yüzeyler: `components/Layout.tsx` (global `max-w-7xl`=1280px kelepçe — TÜM sayfaları ortalıyor), `pages/TicketDetail.tsx` (field render + `CommentCard` + 2 elle-yazılmış modal), `components/diff/{DiffViewer,FileDiffView,HunkView}.tsx` (HunkView satır içeriği düz `whitespace-pre font-mono` — highlight yok), `components/attachments/{AttachmentItem,AttachmentsSection,Lightbox}.tsx` (image→Lightbox, video→`<video>`, diğer→SADECE indir), `MarkdownRenderer.tsx` (field+comment ORTAK render motoru; code-block'ta dil highlight yok), `MarkdownFieldEditor.tsx` (AC/TC inline edit). Backend'e dokunmadan çözülebilenleri FE-only tuttum; #2 için backend şema değişikliğini YAGNI ile eledim (P4 not).

### P1 — Geniş-ekran layout: içerik kelepçesini kaldır (#5)
- Değer: `Layout.tsx` her sayfayı `max-w-7xl` (1280px) ile ortalıyor; ≥1440px ekranda board + ticket sayfaları ortaya sıkışıp iki yanda büyük ölü alan bırakıyor. En somut şikayet, tek-dosya-ağırlıklı, düşük risk.
- Kaba AC: (a) ≥1440px viewport'ta ana container belirgin genişler (ör. `max-w-screen-2xl`/akışkan + makul iç padding); (b) `td-grid` içerik kolonu kazanılan genişliği kullanır, sidebar 320px sabit; (c) <1024px mobil tek-kolon davranışı bozulmaz; (d) Boards+BoardDetail+TicketDetail+Space aynı kuralı alır.
- Kaynak: "(5) board/ticket sayfaları kendini ekranın ortasına daraltıyor (geniş ekranda saçma kısıt)" | [user-mandated]

### P2 — Diff dile-özel syntax highlighting (#4)
- Değer: `HunkView` satır içeriğini renklendirmesiz düz basıyor → kod diff'i okunaksız. `components/diff/` klasörüne izole, temiz paralel lane.
- Kaba AC: (a) dosya uzantısından (`file.path`) dil çıkarımı + satır-bazında token renklendirme; (b) diff satır semantiği KORUNUR (add/del arka planı, +/- glyph, eski/yeni satır no, collapse eşiği); (c) bilinmeyen uzantı/parse hatası → mevcut düz render'a güvenli fallback; (d) light+dark token uyumu; (e) büyük diff'te perf makul (highlighter satır bazında, lazy).
- Kaynak: "(4) kod değişikliği (diff) görünümünde dile özel rendering yok, kötü görünüyor" | [user-mandated]

### P3 — Attachment içerik görüntüleyici: JSON + log/logcat + text (#3)
- Değer: `AttachmentItem` yalnız image/video önizliyor; `.json/.log/.txt/logcat` için sadece indir linki → agent kanıtı (logcat, report) UI'da okunamıyor. `components/attachments/` + `api/client.ts` (text content fetch) izole.
- Kaba AC: (a) text-ailesi content-type (json/plain/log/logcat) için satıriçi genişletilebilir içerik önizleme; (b) JSON pretty-print + katlanabilir; (c) log/logcat mono + satır-no + wrap toggle; (d) büyük dosyada boyut-cap + "tümünü indir" fallback; (e) binary/image/video davranışı değişmez.
- Kaynak: "(3) JSON/logcat attachment içeriği okunabilir olsun (şu an sadece dosya linki)" | [user-mandated]

### P4 — Yapılandırılmış AC/TC render: use-case / test-case şablonu (#2a)
- Değer: AC/TC şu an düz markdown; klasik SW süreç-doküman görünümü yok. Mevcut markdown-convention'ı (GIVEN-WHEN-THEN, test-case adımları) TESPİT edip yapılandırılmış kart/tablo render eden katman — backend şeması DEĞİŞMEDEN.
- Kaba AC: (a) `acceptance_criteria` içindeki GWT blokları etiketli kart olarak render; (b) test-case kalıbı (ön-koşul / adım / beklenen) tablo veya numaralı adım olarak render; (c) kalıba uymayan içerik düz markdown'a fallback (kırılmaz); (d) inline düzenleme (`MarkdownFieldEditor`) bozulmaz — render katmanı yalnız read-view'a eklenir.
- Not (YAGNI — backend alternatifi ELENDİ): structured AC/TC kolonları/entity = migration + `schemas.py` + MCP create/update + PM/QA playbook değişimi (geniş blast-radius). Hedef OKUNABİLİRLİK olduğu için FE-convention yeterli; backend-şema yolunu challenge round isterse promote etsin.
- Kaynak: "(2) AC/TC alanları ... use-case/test-case şablonu tarzında yapılandırılsın" | [user-mandated]

### P5 — Popup ticket-spec doküman viewer (#2b)
- Değer: Kullanıcı açıkça "popup döküman viewer" istedi — ticket'ı temiz, okunur tek-doküman (Description + AC + TC + technical alanlar) olarak modal'da sunar; inline-edit akışından ayrı "oku" modu. P4 render katmanını yeniden kullanır.
- Kaba AC: (a) ticket başlığında "Doküman görünümü" aksiyonu → modal; (b) Description + AC + TC + technical_depth + impact_analysis okunur sırayla, P4 yapılandırılmış render ile; (c) Escape / dış-tık / close ile kapanır + focus yönetimi; (d) salt-okunur; (e) boş alanlar zarif gizlenir/işaretlenir.
- Bağımlılık: P4 (render katmanı). Modal kabuğu için P8 (opsiyonel ortak altyapı).
- Kaynak: "(2) ... + popup döküman viewer" | [user-mandated]

### P6 — Handoff / sistem-yorumu okunabilir kartlar (#1 — yorumlar)
- Değer: Yorum trafiğinin çoğu makine-üretimi `[HANDOFF X→Y]`, `[BLOCKED]`, `[RECOVERY]`, `[ESCALATION]` işaretli; hepsi düz markdown → "tekdüze/uzun". Marker tespit edip rol-renkli, from→to chip'li, karar-rozetli yapılandırılmış kart render → hızlı tarama. `CommentCard`'a izole.
- Kaba AC: (a) bilinen marker tespit → yapılandırılmış başlık (from-role→to-role chip + tip rozeti); (b) gövde okunur, uzun-yorum collapse KORUNUR (300 char); (c) marker'sız normal yorum eskisi gibi render; (d) light+dark rol-token uyumu.
- Kaynak: "(1) ... yorumlar tekdüze/uzun" | [user-mandated]

### P7 — Field / markdown okunabilirlik tabanı (#1 — field formatları)
- Değer: "ticket field formatları okunaksız" — `MarkdownRenderer` field + comment + doc-viewer ORTAK motoru; başlık hiyerarşisi / spacing / kod-blok kontrastı iyileştirmesi P4/P5/P6'yı da besleyen taban. Küçük, foundational.
- Kaba AC: (a) MarkdownRenderer başlık ölçeği + paragraf/liste spacing + kod-blok kontrastı okunurluk için ayarlanır; (b) field panel yoğunluğu (`field-body`) tarama için ayarlanır; (c) token-tabanlı kalır, görsel-regresyon riski düşük.
- Not: P6 ile birleştirilebilir (council kararı — ikisi de #1'e hizmet ediyor ama biri styling, biri detection logic).
- Kaynak: "(1) ticket field formatları okunaksız" | [user-mandated] (concrete; #6 belirsizinden ayrı)

### P8 — (opsiyonel / enabler) Paylaşılan Modal/Popup primitive
- Değer: Halihazırda 3 elle-yazılmış modal var (TicketDetail branch-diff + delete, Lightbox); P5 + (opsiyonel) P3 ile 5'e çıkar → "rule of three" aşıldı, de-dup meşru (spekülatif değil). Tek erişilebilir `ui/Modal` kabuğu (overlay + scrim + Escape + dış-tık + focus-trap + close) çıkarımı.
- Kaba AC: (a) erişilebilir modal primitive; (b) branch-diff + delete + Lightbox refactor (davranış korunur); (c) P5 bunu tüketir.
- ⚠️ Trade-off (COUNCIL KARAR VERSİN): sequence-first yapılırsa P5'i serileştirir → cap=3 tam-paralel hedefiyle çelişir. Alternatif: P5/P3 mevcut inline modal kalıbını kullanır, bu follow-up refactor'a ertelenir. Tek ZORUNLU tüketici = P5; P3 inline-expand olarak modal'sız yapılabilir.
- Kaynak: türetilmiş (kullanıcı-mandate DEĞİL) — YAGNI-checked de-dup önerisi

### P9 — AÇIK SORU: "genel UI taskları" (#6)
- Değer: (6) belirsiz; scope icat etmiyorum. Ticketize'dan ÖNCE kullanıcı somut liste vermeli.
- Açık soru: #6'da hangi somut işler var? (ör. board kart yoğunluğu, filtre/sıralama, boş-durum ekranları, mobil, klavye-nav, bildirim UX?) — enumerate edilene kadar DEFER.
- Kaynak: "(6) genel UI tasklarım var (belirsiz)" | kullanıcı-input bekliyor

### PM parallelizability okuması (layout fazına ön-girdi)
- Temiz dosya-disjoint lane'ler (cap=3 ilk dalga adayı): **P1** (`Layout.tsx`+css), **P2** (`diff/`), **P3** (`attachments/`+`api/client.ts`).
- `TicketDetail.tsx` = HOTSPOT: P4-wiring, P5, P6 hepsi bu dosyaya dokunur → aralarında seri eğilimli. P7 `MarkdownRenderer.tsx`+`index.css`'e dokunur (P1 ile css çakışma riski — koordine).
- Öneri (challenge/layout round'a): 1. dalga P1‖P2‖P3; P7 tabanı erken; sonra P4→P5 (bağımlı zincir), P6 TicketDetail penceresinde sıralı/koordine; P8 council kararına bağlı.

## [COUNCIL R2 architect] 2026-07-14 10:35

Kod tabanını inceledim (Layout, diff/, attachments/, MarkdownRenderer, MarkdownFieldEditor, TicketDetail 1179 satır, parseDiff, index.css, package.json). R1'in çoğu okuması doğru — **4 teknik düzeltme** ile paralellik ciddi artıyor:

**CHALLENGE özeti (R1'e itiraz/düzeltme):**
1. **P1 index.css'e DOKUNMASIN** → sadece `Layout.tsx`'te iki `max-w-7xl`'i Tailwind `max-w-screen-2xl` utility'siyle değiştir. Böylece P1 ⟂ P7 (css çakışması ELENİR). R1 "Layout+css" demişti; css gereksiz.
2. **P4 TicketDetail'e DOKUNMASIN** → yapılandırılmış render'ı `MarkdownFieldEditor`'ın read/preview view'ına gömüyoruz (satır 63 `readView` + `Tab=preview` mevcut), TicketDetail'in `typeFields.map`'ine değil. Böylece P4 hotspot DIŞINDA kalır → P4 ‖ P5/P6 mümkün. Bu en büyük paralellik kazancı.
3. **P6 "izole değil"** → `CommentCard` ayrı dosya DEĞİL, `TicketDetail.tsx` içinde (satır 848). R1 "CommentCard'a izole" demiş ama fiziksel olarak hotspot'un içinde. P6 ya TicketDetail'i eller (serileşir) ya da önce `components/CommentCard.tsx`'e EXTRACT edilir (öneri: extract, P6'nın parçası).
4. **P8 = ERTELE/DÜŞÜR** (aşağıda gerekçeli). Tek zorunlu tüketici P5'i serileştirmesi + TicketDetail 3-yönlü çakışma yaratması, cap=3 tam-paralel mandatıyla çelişiyor.

### Per-P teknik şekil + kritik risk

- **P1 — Layout genişletme**: `Layout.tsx` header (satır 21) + main (satır 63) `max-w-7xl`→`max-w-screen-2xl` (1536px) veya akışkan+cap. `td-grid` zaten `1fr 320px` (index.css 506) → içerik kolonu kazanılan genişliği OTOMATİK yer, ekstra iş yok. Mobil `@media(max-width:1023px)` tek-kolon (608) korunur. **Risk**: Boards/BoardDetail/Space kendi iç `max-w-*`'ını taşıyabilir → AC(d) doğrulamada bu 3 sayfa grep'lenmeli; ama global kelepçe tek noktada (Layout). Düşük risk, tek-dosya.
- **P2 — diff highlighting**: `HunkView` içerik hücresi (satır 112) `whitespace-pre` düz `{line.content}` → TEK enjeksiyon noktası. **Kritik**: `HunkView` `file.path` ALMIYOR (FileDiffView satır 90 sadece `hunk` pas'lıyor); dil, `file.path`'in mevcut olduğu `FileDiffView`'dan (satır 58-61) HunkView'a prop olarak GEÇİRİLMELİ. Satır-bazlı stateless highlight (aşağıda kütüphane kararı). **Risk**: çok-satırlı construct (template literal/blok yorum) satır sınırında yanlış tokenize olabilir — diff'lerde kabul edilebilir, dokümante et; tam-dosya bağlam reconstruction YAPMA (perf/karmaşıklık patlar). Fallback: bilinmeyen uzantı→düz render (mevcut davranış).
- **P3 — attachment içerik viewer**: `AttachmentItem` `else` dalı (satır 51-61) yalnız ikon+indir. `api/client.ts` `attachmentContentUrl` sadece URL builder (token `?token=` query'de, Bearer YOK) → yeni `fetchAttachmentText(key,id)` = `fetch(contentUrl).then(r=>r.text())`, `size_bytes` cap'i ÖNCE kontrol (öneri 256KB–1MB, aşınca indir-fallback). `grouping.ts`'e `isTextLike(content_type, filename)` (logcat çoğu `application/octet-stream` → uzantı sniff ŞART). JSON pretty = native `JSON.parse`+`stringify` (dep yok). **Risk**: büyük logcat DOM'u kilitler → cap + wrap-toggle + lazy expand zorunlu.
- **P4 — yapılandırılmış AC/TC**: yeni `lib/criteria/parseCriteria.ts` (deterministik parser, aşağıda şema) + yeni `components/StructuredCriteria.tsx`, `MarkdownFieldEditor`'a `variant="criteria"` prop'uyla wire. **Kritik risk**: `MarkdownFieldEditor` TÜM alanlarca kullanılıyor (description/technical_depth dahil) → structured render SADECE `acceptance_criteria`+test alanlarına scope'lanmalı (variant prop), yoksa açıklamayı da yanlış kartlar. Kalıp yoksa düz markdown fallback (kırılmaz).
- **P5 — popup doküman viewer**: yeni `components/TicketDocView.tsx` + TicketDetail header'a buton + modal render (satır ~455 civarı buton kümesi). P4 render katmanını salt-okunur tüketir. Modal a11y: Lightbox pattern'ini INLINE kopyala (P8 bekleme — Lightbox zaten "mirrors existing modal markup" diye kendisi de böyle yaptı). **Blocked_by P4** (structured render olmadan doküman görünümü yarım). TicketDetail hotspot → P6 ile serileşir.
- **P6 — handoff yorum kartları**: `[HANDOFF X→Y]`/`[BLOCKED]`/`[RECOVERY]`/`[ESCALATION]` marker'ları exit-protocol'de DETERMINISTIK → `^\[(HANDOFF|BLOCKED|RECOVERY|ESCALATION)\]` + `(\w+)[→-]>?(\w+)` parse. `RoleChip` (satır 859) + `Avatar` mevcut, yeniden kullan; collapse 300-char (satır 846) KORUNUR. **Kritik**: `CommentCard` TicketDetail içinde (satır 848) → önce `components/CommentCard.tsx`'e extract et (P6 kapsamı), sonra izole çalış. Extract TicketDetail'i bir kez eller (import + ~35 satır çıkar).
- **P7 — markdown/field tabanı**: `MarkdownRenderer.tsx` başlık ölçeği (h1 `text-base`, h2 `text-sm`, h3 `text-xs` — satır 91-131 çok sıkışık) + `index.css` `.field-body`/`.ac-list` (521-523). P4 struct render'ı KULLANMAZ (ayrı component) → sadece MarkdownRenderer+css. **Risk**: MarkdownRenderer HER YERDE (field+comment+mermaid+doc-view) render motoru → görsel regresyon yüzeyi geniş; token-tabanlı kal, ölçek değişimini muhafazakar tut.
- **P8 — Modal primitive**: VERDICT aşağıda (ertele). `lib/a11y.ts` `onActivateKeyDown` mevcut ama focus-trap util YOK (Lightbox inline yapıyor). `ui/` dizini yok.
- **P9 — "genel UI taskları"**: DEFER — kullanıcı somut liste vermeden ticketize edilemez. Katılıyorum, scope icat yok.

### P2 KÜTÜPHANE KARARI (decisive) → `prism-react-renderer` v2

package.json'da highlight kütüphanesi YOK (`mermaid` var ama diyagram). Değerlendirme:
- **shiki**: TextMate-grammar, en doğru AMA ağır (async init + WASM oniguruma + runtime grammar/theme yükleme). Diff için async loading-state + bundle şişmesi → **RED**.
- **highlight.js (raw)**: satırlar arası STATEFUL; diff'i satır-satır beslemek zor, wiring fazla → **RED** (lowlight AST varyantı mümkün ama daha çok tel).
- **prism-react-renderer v2**: (a) token-array render-prop API → mevcut diff `<td>` içerik hücresine token'ları YENİDEN YAPILANDIRMADAN gömer (diff tablo semantiği korunur); (b) küçük bundle; (c) SENKRON (loading-state yok); (d) saf ESM, Vite-native; (e) JS theme objesiyle light/dark iki palet; (f) bilinmeyen dil→düz metin doğal fallback. **SEÇİM: prism-react-renderer.**
- **Cross-lane sinerji**: P3 JSON pretty-print de aynı prism'i kullanabilir (json dili). Öneri: **P2 prism dep'ini ekler (owner), P3 rebase'de hazır bulur** → package.json çakışması Tier-1 mekanik (dep satırı/lockfile). Not: P4 struct-render prism GEREKTİRMEZ (kart/tablo, kod değil).

### P4 DETERMINISTIK ŞEMA (parse edilebilir — agent'lar da yazacak/okuyacak)

Progressive-enhancement DETECTOR (hard schema değil — kalıp yoksa düz markdown, mevcut ticket'lar kırılmaz). Backend şeması DEĞİŞMEZ (FE-convention, P4 YAGNI notu korunur).

GWT (acceptance_criteria):
```
### AC1: <kısa başlık>
- GIVEN <bağlam>
- WHEN <aksiyon>
- THEN <beklenen>
- AND <ek>        (opsiyonel, önceki cümleye bağlanır)
```
Parser: `^#{1,4}\s+AC\d+` ile blok böl; blok içinde `^[-*]?\s*(GIVEN|WHEN|THEN|AND|BUT)\b`i. ≥1 GIVEN + ≥1 THEN → geçerli senaryo kartı; değilse fallback.

Test-case (test alanı):
```
### TC1: <başlık>
- Önkoşul: <precondition>
- Adımlar:
  1. adım
  2. adım
- Beklenen: <expected>
```
Parser: `^#{1,4}\s+TC\d+`, sonra etiketli satır `^(Önkoşul|Precondition|Adımlar|Steps|Beklenen|Expected)\s*:` → tablo/numaralı adım render.

Özellikler: (a) regex-deterministik, (b) insan-okunur, (c) agent-yazılabilir (PM/Architect emit edebilir), (d) fallback-güvenli → **kademeli adopsiyon** (render fallback'li olduğu için PM/Architect playbook'ları sonradan bu konvansiyona geçebilir; bu FE-scope DIŞI doc işi, ayrı). Bu şemayı ticketize'da P4 AC'sine sabitlemenizi öneririm.

### P8 VERDICT → ERTELE (bu paketten çıkar, follow-up tech-debt ticket)

Rule-of-three teknik olarak KARŞILANIYOR (branch-diff modal TicketDetail:592 + delete modal :638 + Lightbox = 3; P5 = 4.). Lightbox zaten tam a11y pattern'i (focus-trap+Escape+backdrop+focus-return) taşıyor → referans impl. GERÇEK duplikasyon + TicketDetail modallarında focus-trap EKSİK (yalnız doc-level Escape, 296-309) var. **AMA** maliyet/fayda şu an ters:
- P8'i şimdi yapmak P5'i SERİLEŞTİRİR (P5 modal kabuğunu bekler) → tam-paralel mandat ihlali.
- P8 refactoru TicketDetail'in İKİ modalını eller → P5+P6 ile 3-yönlü hotspot çakışması.
- **Karar**: P5/P3 modalını Lightbox pattern'ini INLINE kopyalayarak yap (Lightbox'ın kendisi böyle doğdu). P8 = paket SONRASI ayrı refactor ticket'ı (`ui/Modal` + 4 tüketiciyi konsolide + TicketDetail modallarına focus-trap kazandır). P5 AC'sine "modal focus-trap+Escape+focus-return ZORUNLU (Lightbox pattern)" ekle → a11y borcu birikmesin. Cost>benefit NOW; benefit>cost LATER (izole pencerede).

### blocked_by kenarları

- **P5 → blocked_by P4** (HARD — doküman görünümü structured render'a bağımlı). Tek gerçek hard kenar.
- P5/P6/(P8) → `TicketDetail.tsx` ORTAK → **disjoint-globs testi başarısız → aralarında SERİ** (blocked_by değil, dosya-çakışma serileştirmesi). P6 extract'ten sonra P6-CommentCard izole olsa da extract-commit TicketDetail'i eller → yine seri pencere.
- P2 ↔ P3 → `package.json` prism dep ORTAK → SOFT koordinasyon (P2 owner; blocked_by DEĞİL, re-conflict-check Tier-1).
- P1 ⟂ P7 (P1 index.css'e dokunmazsa — CHALLENGE #1). P4 ⟂ P7 (ayrı component vs MarkdownRenderer). P4 ⟂ P5/P6 (MarkdownFieldEditor vs TicketDetail — CHALLENGE #2).
- Diğer tüm çiftler bağımsız.

### files_touched_globs TAHMİNİ + disjoint lane planı (cap=3, full-force)

| P | files_touched_globs (tahmin) | Lane |
|---|---|---|
| P1 | `frontend/src/components/Layout.tsx` | layout |
| P2 | `frontend/src/components/diff/**`, `frontend/src/lib/diff/**`, `frontend/package.json` | diff |
| P3 | `frontend/src/components/attachments/**`, `frontend/src/api/client.ts`, `frontend/package.json`(prism reuse) | attachments |
| P4 | `frontend/src/lib/criteria/**`, `frontend/src/components/StructuredCriteria.tsx`, `frontend/src/components/MarkdownFieldEditor.tsx` | criteria |
| P5 | `frontend/src/components/TicketDocView.tsx`, `frontend/src/pages/TicketDetail.tsx` | ticketdetail |
| P6 | `frontend/src/components/CommentCard.tsx`(extract), `frontend/src/pages/TicketDetail.tsx` | ticketdetail |
| P7 | `frontend/src/components/MarkdownRenderer.tsx`, `frontend/src/index.css` | markdown-base |
| P8 (ertelendi) | `frontend/src/components/ui/Modal.tsx`, `frontend/src/components/attachments/Lightbox.tsx`, `frontend/src/pages/TicketDetail.tsx` | modal (FOLLOW-UP) |

**Hotspot gerçeği**: `TicketDetail.tsx`'i GERÇEKTEN ellemek zorunda olanlar = SADECE P5, P6, P8. P1/P2/P3/P4/P7 hotspot'tan tamamen KAÇINIR (CHALLENGE #2+#3 sayesinde P4 de kaçar). `package.json`: P2(owner)+P3. `index.css`: yalnız P7 (P1 kaçtı).

**Dalga planı (cap=3):**
- **Dalga A**: P1 ‖ P2 ‖ P3 — tam disjoint (pkg.json P2/P3 soft-koordine, P2 önce merge).
- **Dalga B**: P4 ‖ P7 ‖ P6 — disjoint (P4=MarkdownFieldEditor, P7=MarkdownRenderer+css, P6=CommentCard extract+TicketDetail; P6 hotspot'ta YALNIZ çünkü P5 henüz yok).
- **Dalga C (kuyruk)**: P5 (blocked_by P4 çözülünce) — TicketDetail'de P6 bitmiş, temiz pencere.
- **Follow-up**: P8 ayrı tech-debt ticket (paket dışı).
- Net: 3+3 paralel + P5 kuyruğu. Tek hard-serial zincir P4→P5. 8 iş ~3 dalgada.

### SPLIT/MERGE önerileri

- **P7 vs P6 = AYRI TUT** (R1 açık soru). Farklı dosya (MarkdownRenderer+css vs CommentCard), farklı içerik (global styling vs comment-özel detection). Merge → şişer, MarkdownRenderer+css+TicketDetail'i tek ticket'ta toplar, çakışma yüzeyini büyütür, review'u zorlaştırır. Ayrı = daha iyi paralellik + küçük review.
- **P8 = paketten ÇIKAR** → follow-up refactor (yukarı gerekçe).
- **P4 = split GEREKMEZ** ama variant-scope zorunlu (kritik risk notu).
- **P2/P3 = split yok**; prism dep sahipliği P2'de (koordinasyon notu).
- **P6 = extract prep** kapsam-içi (ayrı micro-ticket abartı).
- Yeni ticket ÖNERİLMEZ (P9 hariç kullanıcı-input bekliyor).

## [COUNCIL R2 reviewer] 2026-07-14 10:37

Bias = LAZY DEFAULT (ihtiyaç kanıtı yoksa yük). Kod tabanını bağımsız taradım (Layout, HunkView, AttachmentItem, MarkdownRenderer, MarkdownFieldEditor, TicketDetail:848 CommentCard, package.json, modal grep). Architect R2 ile büyük ölçüde ÖRTÜŞÜYORUM — 8/9 verdict konsensüs; tek gerçek ayrışma P7 SIRALAMA. Aşağıda kanıt + net verdict.

### Repo-grounded kanıt (bağımsız doğrulama)
- **Modal sayımı — hem R1 hem R2-architect UNDERCOUNT**: R1 "3", architect "3 (rule-of-three met)". Gerçek: `grep -rlE 'fixed inset-0 z-(40|50)|role="dialog"'` → **13 DOSYA / ~15 modal örneği** (AddMemberModal, NewTicketDialog, WorkflowList×2, WorkflowStateList, EdgePropertyPanel, NodePropertyPanel, SonarIssueDrawer, repository/{RemoveRepoConfirm,DetachConfirm,RotateSecret}Modal, BoardSettings, Lightbox, TicketDetail×2). `SonarIssueDrawer.tsx` pattern'i BELGELEMİŞ ("REUSES the app's hand-rolled overlay-modal pattern ... `ui/` dir (none exists)"). → Duplikasyon iddia edilenden ~5x FAZLA. Bu, P8-defer'i İKİ yönden sağlamlaştırır: (i) ayrı refactor ticket'ı fazlasıyla meşru (15 modal), (ii) ama tam da bu yüzden 13 dosyayı elleyen refactor bu pakette KOŞARSA full-force-paralel'i kesin serileştirir → paket-dışı ŞART.
- **MarkdownRenderer zaten iyi-stilli (P7 evidence)**: 261 satır, HER element (h1-h3/ul/ol/table/blockquote/p/code/pre) bilinçli token + `compact` varyant + `space-y-2`. "Okunaksız" iddiası SOMUT eksik göstermiyor. Architect de aynı fikirde ("görsel regresyon yüzeyi geniş; muhafazakar tut"). Vague-AC + global-motor = en zayıf-kanıtlı madde.
- **Highlighter dep YOK** → architect'in `prism-react-renderer v2` seçimini ONAYLIYORUM: senkron API benim "hafif+fallback" AC-pin kaygımı çözüyor (loading-state yok, token-array diff `<td>`'ye gömülür, bilinmeyen dil→düz fallback). P3'ün json'da aynı prism'i reuse etmesi (P2 owner) doğru sinerji.
- **CommentCard TicketDetail İÇİNDE (satır 848)** → architect'in düzeltmesi haklı: R1 "CommentCard'a izole" demiş ama fiziksel olarak hotspot'un içinde. P6 = önce `components/CommentCard.tsx` EXTRACT (kapsam-içi), sonra izole. Verdict'imi buna göre rafine ettim.

### Verdict tablosu (challenge (a)-(e) dahil)
| P | Verdict | Gerekçe (1 cümle) |
|---|---|---|
| P1 | **keep** | User-mandated, tek-dosya (Layout.tsx 21+63); architect'in "index.css'e DOKUNMA, sadece `max-w-screen-2xl` swap"ını onaylıyorum → P1⟂P7 css çakışması eriyor. |
| P2 | **keep** | User-mandated, `diff/`'e izole; `prism-react-renderer v2` (senkron+fallback) doğru seçim, `file.path`→HunkView prop geçişi kritik-ama-küçük. |
| P3 | **keep** (AC daralt) | User-mandated; JSON pretty = native stringify + prism-json (dep-free/minimal), collapsible FULL-tree DEĞİL top-level fold + size-cap + wrap-toggle — balonlanmasın; architect'in minimal impl'i zaten bunu yapıyor. |
| P4 | **keep** (variant-scope ZORUNLU) | (c) User-mandated (#2a); render-only + katı fallback, KRİTİK: structured render SADECE acceptance_criteria+test alanlarına scope'lanmalı (architect variant-prop) yoksa description/technical_depth'i yanlış kartlar; genel-amaçlı DSL YASAK. |
| P5 | **keep** (P4-bağımlı, P8-değil) | (e) EVET P4'ten ayrı iş (field-transform vs tek-doküman modal besteleme) ama blocked_by P4; modal Lightbox pattern'ini INLINE kopyalasın (P8 bekleme), AC'ye focus-trap+Escape+focus-return ZORUNLU. |
| P6 | **keep** (extract-first) | User-mandated (#1); marker-parse from→to + tip rozeti gerçek logic (author yalnız "from"), ama CommentCard TicketDetail'den `components/CommentCard.tsx`'e EXTRACT edildikten sonra izole (architect'in catch'i, kapsam-içi). |
| P7 | **defer** (in-package, SON dalga + reassess-gate) | (a) User-mandated (#1)→cut yok; ama MarkdownRenderer zaten iyi-stilli + GLOBAL motor + vague AC → P1/P4/P6 LANDIKTAN SONRA koş, "hâlâ somut okunaksızlık var mı?" gate'iyle; **architect'ten AYRILIYORUM** (o Dalga-B erken-paralel diyor — aşağıda). |
| P8 | **defer** (paket-dışı tech-debt, P5-gate DEĞİL) | (b) Non-mandated, sıfır okunabilirlik değeri; ~15 modal (iddia edilen 3 değil) 13 dosyalık refactor full-force-paralel'i serileştirir → ayrı follow-up ticket, bu pakette koşma; architect ile TAM konsensüs. |
| P9 | **cut** (ticket değil ≡ architect'in "defer"i) | (d) Tanımsız scope AC-test edilemez → placeholder ticket AÇMA, kullanıcı somut liste versin → her madde ayrı ticket; operasyonel olarak architect'in defer'iyle AYNI sonuç (şimdi ticket yok). |

### Architect ile TEK ayrışma — P7 sıralaması (consensus round çözsün)
- **Architect**: P7'yi Dalga-B'de erken-paralel (P4 ‖ P7 ‖ P6) — dosya-disjoint (MarkdownRenderer+css) olduğu için paralel-güvenli.
- **Ben (defer-last)**: Dosya-disjoint DOĞRU (hard çakışma yok) ama P7 GLOBAL motoru (CommentCard/field-readView/doc-viewer/P4-fallback HEPSİ MarkdownRenderer'ı tüketir) → P4/P5/P6 ile EŞ ZAMANLI koşarsa hepsi KAYAN tipografi baseline'ına karşı geliştirilir (soft/görsel çakışma + merge-order duyarlı). YAGNI biası: (i) AC vague + kanıt zayıf (renderer zaten iyi), (ii) somut yapısal işler (P4 kartları, P6 yorum kartları) LANDİKTAN SONRA "field'lar hâlâ okunaksız mı?" sorusu netleşir — belki P7 küçülür/gereksizleşir (asıl YAGNI kazancı). → P7'yi SON dalgaya al + reassess-gate. Cut değil (mandate), sadece EN SONA.
- **Neden challenge round'a taşıyorum**: ikisi de savunulabilir; benim mandated LAZY-DEFAULT bias'ım berabereyi "kanıtlanana kadar global-değişimi erteleme"ye kırar. Consensus/layout round net karar versin: erken-paralel-B mi, defer-last-C+reassess mi.

### Paralel yerleşim (benim önerim — architect'in tablosuyla uyumlu, P7 farkı hariç)
- **Dalga A (cap=3, disjoint)**: P1 ‖ P2 ‖ P3 (pkg.json P2-owner soft-koordine).
- **Dalga B**: P4 ‖ P6 (P4=MarkdownFieldEditor+yeni component; P6=CommentCard extract+TicketDetail).
- **Dalga C (kuyruk)**: P5 (blocked_by P4; TicketDetail'de P6 bitmiş temiz pencere) → sonra **P7** (residual + reassess-gate).
- **Paket-dışı**: P8 (ayrı refactor ticket), P9 (kullanıcı-input).
- Ticketleşecek (bu paket): **P1, P2, P3, P4, P5, P6, P7** (7). P5→blocked_by→P4 tek hard kenar. P8/P9 dışarıda.

## [COUNCIL R3 pm] 2026-07-14 15:50

R2 architect + R2 reviewer'ı okudum. Konsensüs güçlü (8/9); tek açık konu **P7 sıralaması**. Architect'in 4 teknik düzeltmesini (P1 css'e dokunma, P4 variant-scope hotspot-dışı, P6 extract-first, P8 ertele) ve reviewer'ın şekil daraltmalarını (P3 narrow-AC, P5 Lightbox-inline, P8 15-modal kanıtı) KABUL ediyorum — paralelliği artırıyor, kanıtlı. Verdict'lerim:

### P1–P6 — ACCEPT (şekil kararları paket tanımına KİLİTLENDİ)
- **P1 — accept.** Şekil: `Layout.tsx`-only `max-w-7xl`→`max-w-screen-2xl` (header:21+main:63); **index.css'e DOKUNMA** → P1⟂P7 css çakışması eriyor (architect #1). AC(d): Boards/BoardDetail/Space iç `max-w-*` grep-doğrula.
- **P2 — accept.** Şekil: `prism-react-renderer v2` (senkron+fallback), P2 = prism dep **OWNER** (package.json); `file.path`→HunkView prop (FileDiffView:58); diff tablo semantiği KORUNUR; bilinmeyen dil→düz fallback; çok-satır construct satır-sınırı yanlış-tokenize KABUL + dokümante.
- **P3 — accept (narrow-AC).** Şekil (reviewer): JSON = native `JSON.parse`/`stringify` + prism-json reuse; collapsible = **TOP-LEVEL fold** (full-tree DEĞİL); size-cap 256KB–1MB→indir-fallback; wrap-toggle; `isTextLike(content_type,filename)` uzantı-sniff (logcat=octet-stream); binary/image/video değişmez.
- **P4 — accept (variant-scope ZORUNLU).** Şekil: structured render SADECE `acceptance_criteria`+test alanlarına `variant="criteria"` ile scope — description/technical_depth'e DOKUNMAZ; architect'in deterministik şeması (GWT `^#{1,4}\s+AC\d+`+GIVEN/WHEN/THEN; TC `^#{1,4}\s+TC\d+`+Önkoşul/Adımlar/Beklenen) AC'ye SABİTLENDİ; genel-amaçlı DSL YASAK; kalıp yoksa markdown-fallback.
- **P5 — accept (P4-bağımlı, Lightbox-inline).** Şekil: `TicketDocView.tsx`+TicketDetail header buton; P4 render'ı salt-okunur tüketir; modal = **Lightbox pattern INLINE** (P8 BEKLEME); AC'ye focus-trap+Escape+focus-return+dış-tık ZORUNLU.
- **P6 — accept (extract-first).** Şekil: önce `components/CommentCard.tsx` EXTRACT (TicketDetail:848, import+~35 satır — kapsam-içi), sonra izole marker-parse; `^\[(HANDOFF|BLOCKED|RECOVERY|ESCALATION)\]`+from→to chip+tip rozeti; RoleChip+Avatar reuse; 300-char collapse KORUNUR; marker'sız yorum değişmez.

### P7 — KARAR: ACCEPT Reviewer (DEFER-LAST + reassess-gate; **CUT DEĞİL** — mandate korunur) + PM refinement

Divergence: Architect Dalga-B erken-paralel (P4‖P7‖P6, dosya-disjoint); Reviewer defer-last+reassess-gate. **Reviewer'ı seçiyorum.** Gerekçe:

1. **İki pozisyon da git-düzeyinde geçerli** (P7 dosya-disjoint: MarkdownRenderer+css ⟂ P4/P6 — hard çakışma YOK). Tiebreaker çakışma değil → **scope-discovery + baseline-stability**.
2. **P7'nin gerçek kapsamı P4/P6'nın BIRAKTIĞININ fonksiyonu.** P4 AC-yüzeyini karta, P6 handoff-yorum-yüzeyini karta çevirir; geriye kalan ham-markdown = description+technical_depth+impact_analysis+(konvansiyon-dışı) AC-fallback+marker'sız yorumlar. P7'nin hedefi TAM bu residual — şiddeti ancak P4/P6 LANDIKTAN sonra ölçülebilir. Sona almak "vague global-restyle"ı "hedefli residual-fix"e çevirir = asıl YAGNI kazancı.
3. **Kanıt asimetrisi**: MarkdownRenderer 261 satır, her element bilinçli-token+compact variant (reviewer bağımsız doğruladı). "Okunaksız" iddiası SOMUT eksik göstermiyor → erken-paralel = kanıtsız global değişimi P4/P5/P6 in-flight'ken enjekte etmek.
4. **Merge-baseline hijyeni**: P7 paylaşılan render motorunun tipografisini oynatır; P4-fallback/P5-docview/P6-body HEPSİ bu motoru tüketir. Eş-zamanlı → her tüketici KAYAN baseline'a karşı review/QA (soft/görsel çakışma, merge-order duyarlı). Sona almak: her tüketiciye stabil baseline, P7'ye stabil hedef.

**PM refinement — reassess-gate hesap-verebilir (sessiz-drop DEĞİL):** user #1 ("field formatları okunaksız") mandate; gate bir LOOPHOLE olamaz. Gate = KARAR NOKTASI:
- P7 kuyruğun önüne gelince (P4/P6 merged) residual ham-markdown İNCELENİR → iki çıktıdan biri:
  - **(a)** somut okunaksızlığı hedefleyen **RE-SCOPED AC** ile ilerle (hangi alan/element hâlâ kötü — spesifik), VEYA
  - **(b)** residual temizse → kullanıcıya İADE: "P4/P6 mandate'i çözdü, residual restyle ≈ yok — kapat/kalan sıkıntıyı belirt". Mandate KANITLA kapanır, sessizce düşmez.
- Her yolda mandate KANITLA discharge; P7 ticketize EDİLİR (paket-içi), yalnız timing+scope-precision değişir.

**Throughput dürüstlüğü**: Dalga B 2-geniş (P4‖P6); 3. cap-slot'u P7 ile DOLDURMUYORUM (P5 P4-blocked, başka bağımsız iş yok). Bir wave-slot < baseline-stability+scope-discovery+mandate-with-evidence. Bilinçli trade.

### P8 — ACCEPT out-of-package (reviewer'ın 15-modal kanıtı defer'i GÜÇLENDİRİR)
Reviewer `grep 'fixed inset-0 z-(40|50)|role="dialog"'` → ~15 modal/13 dosya (iddia 3 değil). İKİ yön: (i) de-dup follow-up FAZLASIYLA meşru (15-modal konsolidasyon gerçek değer), (ii) ama 13-dosya blast-radius full-force-paralel'i kesin serileştirir → bu pakette KOŞMAZ. P5 inline Lightbox kullanır → paket P8'e bağlı DEĞİL. P8 = ayrı `ui/Modal` refactor ticket'ı (paket sonrası) + TicketDetail modallarına focus-trap.

### P9 — ACCEPT user-iade (ticket YOK)
Tanımsız scope AC-test-edilemez → placeholder AÇMA. PM #6 için kullanıcıdan somut enumerasyon ister (kart yoğunluğu? filtre/sıralama? boş-durum? mobil? klavye-nav?). Enumerate edilince madde başına ticket/mini-council. Kapı AÇIK — cut değil, user-input gate.

### PM-eklenen follow-up (yeni) — P4 konvansiyon-adopsiyonu
P4 RENDERER'ı teslim eder ama kart görünmesi için ticket İÇERİĞİ GWT/TC şemasını izlemeli. Mevcut ticket'ların çoğu izlemiyor → P4 anlık değeri: uyan=kart, uymayan=güvenli fallback. TAM değer (tüm ticket'lar structured) için PM/Architect playbook'ları şemayı EMIT etmeli = FE-scope DIŞI doc işi → follow-up (non-FE). Yüzeye çıkarıyorum ki "P4 neden eski ticket'ları düzeltmedi?" sürprizi olmasın — progressive-enhancement bilinçli.

### R3 SONUÇ — paket tanımı

**Ticketize (bu paket) — 7 ticket:**

**P1 — Geniş-ekran layout: içerik kelepçesini kaldır** · type=chore
- Kapsam: `Layout.tsx` `max-w-7xl`→`max-w-screen-2xl`; board+ticket geniş ekranı kullanır, mobil tek-kolon korunur.
- Şekil: Layout.tsx-only (header:21+main:63); index.css'e DOKUNMA; AC(d) Boards/BoardDetail/Space grep.
- blocked_by: —
- globs: `frontend/src/components/Layout.tsx`

**P2 — Diff dile-özel syntax highlighting** · type=feature
- Kapsam: HunkView satır içeriğini prism ile token-renklendir; diff semantiği korunur.
- Şekil: `prism-react-renderer v2` (senkron+fallback), P2=dep OWNER; `file.path`→HunkView prop; bilinmeyen dil→fallback; çok-satır satır-sınırı dokümante.
- blocked_by: —
- globs: `frontend/src/components/diff/**`, `frontend/src/lib/diff/**`, `frontend/package.json`
- Not: P3 aynı prism'i json'da reuse (P2 owner; soft-koordine Tier-1).

**P3 — Attachment içerik görüntüleyici (JSON+log/logcat+text)** · type=feature · narrow-AC
- Kapsam: text-ailesi attachment satıriçi genişletilebilir önizleme (JSON pretty, log mono+satır-no).
- Şekil: native stringify+prism-json; TOP-LEVEL fold (full-tree değil); size-cap→indir-fallback; wrap-toggle; `isTextLike()` uzantı-sniff; binary/image/video değişmez.
- blocked_by: — (P2 prism soft, hard değil)
- globs: `frontend/src/components/attachments/**`, `frontend/src/api/client.ts`, `frontend/src/lib/grouping.ts`, `frontend/package.json`

**P4 — Yapılandırılmış AC/TC render (variant-scope)** · type=feature
- Kapsam: AC+test alanlarında GWT/TC konvansiyonu tespit→etiketli kart/tablo; kalıp yoksa markdown-fallback.
- Şekil: `variant="criteria"` SADECE AC+test (description/technical_depth'e DOKUNMA); deterministik şema AC'ye sabit; genel-DSL YASAK.
- blocked_by: —
- globs: `frontend/src/lib/criteria/**`, `frontend/src/components/StructuredCriteria.tsx`, `frontend/src/components/MarkdownFieldEditor.tsx`

**P5 — Popup ticket-spec doküman viewer** · type=feature
- Kapsam: ticket'ı salt-okunur tek-doküman modal (Description+AC+TC+technical+impact), P4 render'ı tüketir.
- Şekil: `TicketDocView.tsx`+header buton; modal=Lightbox pattern INLINE (P8 bekleme); AC focus-trap+Escape+focus-return+dış-tık ZORUNLU; boş alan zarif gizle.
- blocked_by: **P4** (HARD)
- globs: `frontend/src/components/TicketDocView.tsx`, `frontend/src/pages/TicketDetail.tsx`
- Not: P6 ile TicketDetail glob-overlap → SERİ (P6 önce; P5 post-extract temiz dosyada).

**P6 — Handoff/sistem-yorumu okunabilir kartlar (extract-first)** · type=feature
- Kapsam: marker'lı yorumları (HANDOFF/BLOCKED/RECOVERY/ESCALATION) rol-renkli from→to chip'li karta render.
- Şekil: önce `components/CommentCard.tsx` EXTRACT (TicketDetail:848), sonra izole; from→to+tip rozeti; RoleChip+Avatar reuse; 300-char collapse KORUNUR; marker'sız yorum değişmez.
- blocked_by: —
- globs: `frontend/src/components/CommentCard.tsx`, `frontend/src/pages/TicketDetail.tsx`
- Not: P5 ile TicketDetail glob-overlap → SERİ (P6 ÖNCE).

**P7 — Field/markdown okunabilirlik tabanı** · type=chore · DEFER-LAST+reassess-gate
- Kapsam: MarkdownRenderer başlık ölçeği+spacing+kod-blok kontrastı; residual ham-markdown okunurluğu.
- Şekil: **SON** (P4/P6 sonrası); **reassess-gate** kuyrukta → (a) re-scoped somut AC ile ilerle VEYA (b) residual temizse kullanıcıya iade; token-tabanlı+muhafazakar; CUT DEĞİL (user #1 mandate).
- blocked_by: **P4, P6** (reassess için ikisi landmeli)
- globs: `frontend/src/components/MarkdownRenderer.tsx`, `frontend/src/index.css`
- Not: runtime en-son (P5 merged sonrası tercih, baseline); P5‖P7 disjoint-globs → reassess P7'yi substantive bulursa Coordinator throughput opsiyonu.

**Paket-dışı (ticketize EDİLMEZ):**
- **P8 — Paylaşılan Modal primitive** → follow-up tech-debt ticket (paket SONRASI): ~15 modal/13 dosya `ui/Modal` konsolidasyon+focus-trap; blast-radius full-force-paralel'i serileştirir. Paket P8'e bağlı DEĞİL (P5 inline Lightbox).
- **P9 — "genel UI taskları" (#6)** → user-iade, ticket YOK: kullanıcıdan somut liste; enumerate edilince madde başına ticket/mini-council.
- **P4-konvansiyon-adopsiyonu (PM-eklenen)** → follow-up doc (non-FE): PM/Architect playbook GWT/TC emit etsin; P4 progressive-enhancement.

**Dalga planı (cap=3, full-force):**
- **Dalga A**: P1 ‖ P2 ‖ P3 (pkg.json P2-owner önce-merge soft-koordine)
- **Dalga B**: P4 ‖ P6 (disjoint; 2-geniş — 3. slot bilinçli boş, P7 dolmaz)
- **Dalga C**: P5 (blocked_by P4; P6-sonrası TicketDetail temiz) → **P7** (reassess-gate, en son)
- **Paket-dışı**: P8, P9, P4-convention
- Hard kenar: P5→P4, P7→{P4,P6}. Glob-serialize: P5↔P6 (TicketDetail), P2↔P3 (package.json soft).
- Net: 3+2 paralel + P5→P7 kuyruk; 7 ticket ~3 dalga.

## [CLOSE coordinator] 2026-07-14
Paket teslim: 7/7 ticket done+merged (PH-298 dca06af · PH-300 62d53d3 · PH-299 d5c818d · PH-301 48d0f14 · PH-302 3982ce5 · PH-303 6b3e2d0 · PH-304 19301a0), hepsi origin'de.
Dalga gerçekleşmesi: A 3-paralel (298‖299‖300) → slot-doldurmalı B (301, 302) → C seri (303 → 304 reassess).
Reassess-gate ÇALIŞTI: PH-304 kanıt-temelli RESCOPE (başlık merdiveni 18/16/14 + ritim; kontrast endişesi ölçümle düşürüldü).
1 Tier-1 merge conflict'i (299: codewiki log.md+frontend.md) union'la Coordinator'da çözüldü.
Follow-up'lar: PH-305 (P8 Modal primitive) + PH-306 (3 nit) backlog'da. P9 kullanıcı enumerasyonunda.
QA modeli: Coordinator browser'ı 'eller' (DOM/geometry/computed-style ground-truth), jarwis-qa verdict sahibi.
