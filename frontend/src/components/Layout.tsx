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
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6">
          <Link to="/" className="text-base font-semibold tracking-tight dark:text-slate-100 sm:text-lg">
            ProjectHub
          </Link>
          <nav className="flex items-center gap-1 text-sm sm:gap-2">
            <NavLink
              to="/"
              end
              className={({ isActive }) =>
                isActive
                  ? "btn-ghost bg-slate-100 dark:bg-slate-700"
                  : "btn-ghost"
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
              <span className="hidden sm:inline">Logout</span>
            </button>
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-4 dark:text-slate-200 sm:px-6 sm:py-6">
        <Outlet />
      </main>
    </div>
  );
}
