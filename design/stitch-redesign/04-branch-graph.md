# 04 — Board Detail · Branch Graph  ⭐ (SourceTree-style)

**Route:** `/boards/:boardKey#graph` (Branch Graph sekmesi)
**Screenshot:** `screenshots/04-branch-graph.png`, `screenshots/04-branch-graph-commit-selected.png`

## Amaç (ne işe yarar)

Board'a bağlı git deposunun **commit/branch geçmişini** SourceTree / GitKraken tarzı gösterir. Agent'lar ticket'lara commit attıkça bu graph **canlı** güncellenir; yeni commit'ler en üste düşer ve kısa süre vurgulanır (amber pulse). Kullanıcı buradan hangi branch'in nerede olduğunu, hangi commit'in hangi ticket'a (PH-XX) bağlı olduğunu görür ve bir commit'e tıklayıp **diff'ini** sağ panelde inceler.

> **Bu redesign'ın ana hedefi:** "Branch graph kısmı SourceTree gibi görünmeli." Yani sol tarafta branch listesi, ortada renkli **lane gutter** (şerit + nokta + merge eğrileri) ile dikey commit listesi, sağda seçili commit'in diff'i.

## Mevcut UI (PH-167 ile zaten SourceTree-style 3-pane'e geçti)

3 panel:
1. **Branch sidebar (sol, ~176px):** "BRANCHES" başlığı; "All" + her branch satırı (renk noktası + isim, default branch'te "HEAD" rozeti). Seçili branch indigo vurgulu; tıklayınca commit listesi o branch'e filtrelenir.
2. **Commit list (orta, esnek):** sütun başlığı `SHA · Message · Author · Time`. Her satır (36px): solda **SVG lane gutter** (renkli dikey şerit + commit noktası; merge commit'te büyük nokta + eğri; pass-through lane'ler soluk), sonra kısa SHA (mono), commit özeti (truncate), ref rozetleri (branch/tag; HEAD koyu), **ticket key chip'leri** (PH-XX, indigo, tıklanır), author, relative time. Seçili satır indigo zemin; yeni commit amber zemin + nokta glow.
3. **Diff panel (sağ, ~384px, koşullu):** bir commit seçilince açılır. Üstte 12-haneli SHA + özet + kapat (X); altında dosya-dosya **DiffViewer** (eklenen/silinen satırlar renkli).

Lane renkleri sabit paletten (`laneColor`), maksimum 10 lane. Boş / repo-bağlı-değil / hata / yükleniyor durumları ayrı ele alınır.

## Stitch Prompt

```
Design a SourceTree / GitKraken–style git history view for ProjectHub
(developer dashboard, dark theme primary, slate + indigo accent, monospace for
SHAs and branch names). This is a live, real-time commit graph.

Three-pane horizontal layout, full height under the page header:

PANE 1 — Branch sidebar (fixed ~180px, bordered card):
- Tiny uppercase heading "BRANCHES".
- A list of selectable rows: first an "All" row, then each branch. Each row has
  a small colored dot (the branch's lane color), the branch name (monospace,
  truncated), and the default branch shows a dark "HEAD" tag on the right.
- The selected branch row is highlighted indigo.

PANE 2 — Commit list (flexible width, bordered card, scrollable):
- A thin column header: "SHA | Message | Author | Time".
- Dense rows (~36px each). The LEFT of every row is an SVG "lane gutter": colored
  vertical lines (one per active branch lane), a filled commit dot on this
  commit's lane, merge commits drawn as a larger dot with curved lines joining
  parent lanes, and faint pass-through lines for lanes that continue above and
  below. Colors come from a fixed multi-hue lane palette (indigo, teal, amber,
  rose, green, violet…), capped at ~10 lanes.
- After the gutter, each row shows: short SHA (monospace, muted), the commit
  summary (single line, truncated), small ref badges (branch/tag chips; HEAD in
  a solid dark chip), one or two clickable ticket-key chips (e.g. "PH-167",
  indigo), the author name (muted), and a relative time ("3m ago", right-aligned).
- Selected row: indigo-tinted background, SHA colored to its lane. 
- A "just arrived" row (live websocket commit): amber-tinted background and the
  commit dot has a colored glow.

PANE 3 — Commit diff panel (~384px, appears only when a commit is selected):
- Header: 12-char SHA (monospace, muted) + commit summary + a close "X".
- Body: a file-by-file unified diff viewer — file path headers, green added
  lines, red removed lines, line numbers, monospace.

Also include these states as separate frames:
- Empty: centered git-branch icon + "No commits yet — pushes appear here live".
- Not connected: "No repo connected to this board — connect in Settings".
- Loading: centered spinner "Loading…".

The overall feel MUST read like a professional desktop git client (SourceTree),
not a flowchart. Tight rows, colored lanes, scannable. Light + dark variants;
dark is primary.
```

## İyileştirme yönü (öneri)

- **Graph kalitesi:** lane'lerin sürekliliğini ve merge eğrilerini daha akıcı çiz (şu an satır-bazlı SVG; Stitch'ten "smooth bezier lanes" iste). Octopus/merge görselleştirmesini netleştir.
- **Üst araç çubuğu:** branch ara, "sadece ticket'lı commit'ler", author filtresi, fetch/refresh, "uncommitted changes" satırı (working copy) en üstte.
- **Commit satırı zenginleştirme:** ahead/behind sayacı, tag ikonları, PR rozeti (linked PR), avatar.
- **Diff panel:** split/unified toggle, dosya ağacı, word-level highlight, "ticket'a git" kısayolu.
- **Yoğunluk modu:** compact/comfortable satır yüksekliği toggle.
- Branch sidebar'da local/remote gruplama ve klasör (slash) hiyerarşisi (ör. `ph-167/...`).
