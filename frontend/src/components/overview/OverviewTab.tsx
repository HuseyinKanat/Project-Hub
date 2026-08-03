/**
 * OverviewTab.tsx — PH-337 ("Genel Bakış" board tab, thin container).
 *
 * The board's "Genel Bakış" (Overview) tab content. Introduced by PH-337 as a
 * DELIBERATELY thin container: it fetches no data and holds no state — it only
 * mounts the derived epic-progress rollup (EpicProgressPanel), which was moved
 * here from its old strip-top mount in BoardDetail.tsx. Because the panel is now
 * rendered inside a conditionally-mounted tab, its self-owned
 * `["board", boardKey, "epic-progress"]` query is tab-lazy — it fires when the
 * overview tab opens rather than on every board load (same pattern as the Space
 * tab). The panel's own empty/error/loading behaviour is unchanged (PH-335).
 *
 * EXPANSION POINT (PH-339): the Turkish summary sections (amaç / genel durum /
 * ilerleme / highlights), the visual milestone timeline, and the edit UI will be
 * slotted in below the epic-progress panel (see the marked slot in the JSX). Keep
 * this container thin so PH-339's additions cause minimal merge churn in a shared
 * file.
 */
import { EpicProgressPanel } from "@/components/progress/EpicProgressPanel";

export function OverviewTab({ boardKey }: Readonly<{ boardKey: string }>) {
  return (
    <div className="space-y-4">
      <EpicProgressPanel boardKey={boardKey} />
      {/* PH-339 genişleme slot'u: Türkçe özet bölümleri (amaç / genel durum /
          ilerleme / highlights) + görsel milestone timeline + editor buraya
          mount olacak. Veri PH-338 REST'inden gelir; bu container ince kalır. */}
    </div>
  );
}
