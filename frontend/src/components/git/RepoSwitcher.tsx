/**
 * RepoSwitcher.tsx — PH-224 (C4 — branch-view repo switcher)
 *
 * A compact repo selector rendered in a thin header bar ABOVE the BranchGraph
 * `.bg-wrap` grid, letting the user switch which repo's branch screen the
 * Branch view shows when a board has >1 git repo.
 *
 * Visibility contract (no-regression core, AC1/AC2):
 *   - The PARENT decides whether to render this at all — it MUST only mount
 *     `RepoSwitcher` when `repositories.length > 1`. A single-repo board never
 *     sees a selector and gets ZERO layout shift. As a belt-and-braces guard
 *     the component also returns `null` for `repositories.length <= 1`.
 *
 * Selection model:
 *   - `value` is the selected repo **slug** (stable, URL-safe; sourced from the
 *     `?repo=` URL param at the container, default → primary).
 *   - `onChange(slug)` is fired on selection; the container persists it to
 *     `?repo=` + container state and re-keys the BranchGraph subtree.
 *
 * A native `<select>` is used: keyboard-accessible for free (Tab focus, arrow
 * navigation, type-ahead, Esc-close), styled with the Branch view's shadcn
 * tokens so it feels native to the pane rather than bolted on. The primary repo
 * carries a small "primary" badge in its visible label (matching the HEAD-chip
 * style used in BranchSidebar / CommitRow refs).
 *
 * @see docs/codewiki/components/frontend.md — PH-224 repo switcher
 */

import { GitFork } from "lucide-react";

import { cn } from "@/lib/utils";
import type { RepositoryResponse } from "@/types/git";

interface RepoSwitcherProps {
  /** All repos linked to the board (from api.git.listRepositories). */
  repositories: RepositoryResponse[];
  /** Currently selected repo slug. */
  value: string;
  /** Fired with the newly selected repo slug. */
  onChange: (slug: string) => void;
  className?: string;
}

/** Human label for a repo option — `name` preferred, slug fallback. */
function repoLabel(repo: RepositoryResponse): string {
  const base = repo.name?.trim() || repo.slug;
  return repo.is_primary ? `${base} (primary)` : base;
}

export function RepoSwitcher({
  repositories,
  value,
  onChange,
  className,
}: Readonly<RepoSwitcherProps>) {
  // Belt-and-braces: the parent gates on length > 1, but never render a
  // single-option selector even if mounted directly (AC1 no-regression).
  if (repositories.length <= 1) return null;

  const selected = repositories.find((r) => r.slug === value);

  return (
    <div
      className={cn(
        "flex items-center gap-2.5 border-b border-hairline bg-surface px-4 py-2",
        className,
      )}
    >
      <label
        htmlFor="repo-switcher"
        className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-text-muted"
      >
        <GitFork className="h-3.5 w-3.5" aria-hidden="true" />
        Repo
      </label>

      <div className="relative">
        <select
          id="repo-switcher"
          aria-label="Select repository"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={cn(
            "appearance-none rounded-[7px] border border-hairline-strong bg-inset",
            "py-1 pl-2.5 pr-7 text-[13px] text-text-primary",
            "transition-colors hover:bg-raised",
            "focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
          )}
        >
          {repositories.map((repo) => (
            <option key={repo.slug} value={repo.slug}>
              {repoLabel(repo)}
            </option>
          ))}
        </select>
        {/* Custom chevron (appearance-none hides the native arrow) */}
        <span
          className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-text-muted"
          aria-hidden="true"
        >
          ▾
        </span>
      </div>

      {/* Primary badge for the currently-selected repo, parity with HEAD chip. */}
      {selected?.is_primary && (
        <span className="rounded border border-hairline-cyan bg-accent-soft px-[5px] py-px font-mono text-[9px] font-semibold text-accent">
          PRIMARY
        </span>
      )}
    </div>
  );
}
