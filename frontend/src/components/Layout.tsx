import { LogOut } from "lucide-react";
import { Link, NavLink, Outlet } from "react-router-dom";

import { NotificationBell } from "@/components/NotificationBell";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useAuth } from "@/stores/auth";

export function Layout() {
  const logout = useAuth((s) => s.logout);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-800">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-6">
          <Link to="/" className="text-lg font-semibold tracking-tight dark:text-slate-100">
            ProjectHub
          </Link>
          <nav className="flex items-center gap-2 text-sm">
            <NavLink
              to="/"
              end
              className={({ isActive }) =>
                isActive ? "btn-ghost bg-slate-100" : "btn-ghost"
              }
            >
              Boards
            </NavLink>
            <NotificationBell />
            <ThemeToggle />
            <button
              type="button"
              onClick={logout}
              className="btn-ghost gap-1.5 text-slate-600 dark:text-slate-300"
              aria-label="Logout"
            >
              <LogOut className="h-4 w-4" />
              Logout
            </button>
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 px-6 py-6 dark:text-slate-200">
        <Outlet />
      </main>
    </div>
  );
}
