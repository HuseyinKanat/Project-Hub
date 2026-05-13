import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";

import { api } from "@/api/client";

export function BoardsPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["boards"],
    queryFn: () => api.listBoards(),
  });

  return (
    <section className="space-y-4">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Boards</h1>
      </header>

      {isLoading && <div className="text-sm text-slate-500">Yükleniyor…</div>}
      {error && (
        <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {(error as Error).message}
        </div>
      )}

      {data && data.boards.length === 0 && (
        <div className="card p-6 text-sm text-slate-500">
          Henüz board yok. Backend <code>projecthub bootstrap</code> komutunu çalıştırdın mı?
        </div>
      )}

      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {data?.boards.map((board) => (
          <li key={board.id}>
            <Link
              to={`/boards/${board.key}`}
              className="card group flex h-full flex-col gap-2 p-4 transition-colors hover:border-slate-300"
            >
              <div className="flex items-center justify-between">
                <span className="rounded bg-slate-900 px-2 py-0.5 text-xs font-medium text-white">
                  {board.key}
                </span>
                <ArrowRight className="h-4 w-4 text-slate-400 transition-transform group-hover:translate-x-0.5" />
              </div>
              <div className="space-y-1">
                <h2 className="text-base font-semibold">{board.name}</h2>
                {board.description && (
                  <p className="line-clamp-2 text-sm text-slate-500">{board.description}</p>
                )}
              </div>
              <div className="mt-auto flex flex-wrap gap-1 pt-2">
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
                  {board.project_type}
                </span>
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
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
