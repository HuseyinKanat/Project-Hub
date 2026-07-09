---
description: Planning Council — çok-sesli planlama: YAGNI süzgeci + konsensüs + paralel yerleşim; yalnız kararlaştırılan ticket'lar açılır
---

Kullanıcı bir planlama isteği verdi: `$ARGUMENTS`

Sen Coordinator'sın. `~/Jarwis/flows/plan.md`'yi Read et ve **Planning Council** akışını yürüt:

1. `.jarwis/plans/<slug>/council.md` aç (slug: isteğin 3-5 kelimelik kebab-case özeti).
2. Turları sırayla invoke et — her tura önceki council.md içeriğini ver, her invoke prompt'una "derinlemesine düşün — tasarım tartışması, hız değil isabet" yönergesini ekle:
   - R1 PROPOSE → Task(pm): isteği P1..Pn maddelerine dök (⛔ create_ticket YASAK — yalnız council.md)
   - R2a CHALLENGE-teknik → Task(architect): bağımlılık + kaba files_touched_globs + risk
   - R2b CHALLENGE-YAGNI → Task(reviewer): her maddeye keep|cut|defer|merge|split + gerekçe
   - R3 REBUTTAL → Task(pm): accept-cut | defend (yeni kanıtla) | modify
   - (uzlaşmazlık → maddeyi "tartışmalı" işaretle; en fazla 2 döngü)
   - R4 PARALLEL LAYOUT → Task(architect): kalan maddeler → blocked_by graph + katmanlar (contracts/parallel.md §1)
3. DECISION GATE: kullanıcıya konsolide özet — keep/cut/defer/tartışmalı listesi + katman planı. Onayını bekle; `[user-mandated]` maddeler cut edilemez (force kullanıcıda).
4. TICKETIZE → Task(pm): YALNIZ onaylılar; blocked_by + files_touched_globs (taslak) + `plan:<slug>` label + description'a Consensus bloğu.
5. Normal pipeline'ı başlat (transition map + bounded-parallel dispatch).

Council boyunca hiçbir state transition yok (ticket yok); TICKETIZE sonrası `contracts/exit-protocol.md` §2 normal işler.
