import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Plus } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "@/api/client";
import { NewBoardDialog } from "@/components/NewBoardDialog";
import { useCanCreateBoard } from "@/hooks/useMe";

export function BoardsPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["boards"],
    queryFn: () => api.listBoards(),
  });
  // PH-332: HIDE-when-ineligible (admin-of-some-board); the POST 403 is only the
  // submit backstop. While `useMe()` is loading this is false → no flash.
  const canCreate = useCanCreateBoard();
  const [dialogOpen, setDialogOpen] = useState(false);

  return (
    <section className="space-y-4">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Boards</h1>
        {canCreate && (
          <button
            type="button"
            className="btn-primary inline-flex items-center gap-1.5 text-sm"
            onClick={() => setDialogOpen(true)}
          >
            <Plus aria-hidden className="h-4 w-4" />
            Yeni board
          </button>
        )}
      </header>

      <NewBoardDialog open={dialogOpen} onClose={() => setDialogOpen(false)} />

      {isLoading && (
        <output className="block text-sm text-text-muted" aria-live="polite">
          Yükleniyor…
        </output>
      )}
      {error && (
        <div
          className="rounded-md px-3 py-2 text-sm text-danger"
          style={{ background: "var(--danger-soft)" }}
          role="alert"
          aria-live="polite"
        >
          {error.message}
        </div>
      )}

      {data && data.boards.length === 0 && (
        <div className="card p-6 text-sm text-text-muted">
          Henüz board yok. Backend{" "}
          <code className="mono rounded bg-inset px-1 text-text-secondary">projecthub bootstrap</code>{" "}
          komutunu çalıştırdın mı?
        </div>
      )}

      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {data?.boards.map((board) => (
          <li key={board.id}>
            <Link
              to={`/boards/${board.key}`}
              className="card group relative flex h-full flex-col gap-2.5 overflow-hidden p-4 transition-all duration-base ease-out hover:border-hairline-cyan hover:shadow-md hover:[transform:translateY(-2px)] motion-reduce:transition-none motion-reduce:hover:[transform:none]"
            >
              <span
                aria-hidden
                className="pointer-events-none absolute opacity-0 transition-opacity duration-slow group-hover:opacity-100 motion-reduce:hidden"
                style={{
                  inset: "-1px",
                  borderRadius: "14px",
                  padding: "1px",
                  background:
                    "linear-gradient(120deg, transparent 30%, var(--accent) 50%, transparent 70%)",
                  WebkitMask:
                    "linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0)",
                  WebkitMaskComposite: "xor",
                  maskComposite: "exclude",
                }}
              />
              <div className="flex items-start justify-between">
                <span className="mono rounded-sm border border-hairline-cyan bg-accent-soft px-2 py-0.5 text-2xs font-semibold text-accent">
                  {board.key}
                </span>
                <ArrowRight
                  aria-hidden
                  className="h-4 w-4 text-text-muted transition-all group-hover:text-accent group-hover:[transform:translateX(2px)] motion-reduce:group-hover:[transform:none]"
                />
              </div>
              <div className="space-y-1">
                <h2 className="text-base font-semibold text-text-primary">{board.name}</h2>
                {board.description && (
                  <p className="line-clamp-2 text-sm text-text-secondary">{board.description}</p>
                )}
              </div>
              <div className="mt-auto flex flex-wrap gap-1.5 pt-2">
                <span className="mono whitespace-nowrap rounded-pill border border-hairline bg-raised px-2 py-0.5 text-xs text-text-secondary">
                  {board.project_type}
                </span>
                <span className="mono whitespace-nowrap rounded-pill border border-hairline bg-raised px-2 py-0.5 text-xs text-text-secondary">
                  {board.workflow.states.length} states
                </span>
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
