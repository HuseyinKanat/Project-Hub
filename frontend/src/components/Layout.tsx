/**
 * Layout.tsx — PH-171 (F2): app shell restyled to the Cyan-on-Black token
 * system. Legacy color utilities + theme-variant twins replaced with F1
 * semantic token utilities (bg-surface, border-hairline, text-accent,
 * bg-accent-soft). IA/routing/behavior unchanged (PH-167 nav set preserved).
 */
import { LogOut, User } from "lucide-react";
import { Link, NavLink, Outlet } from "react-router-dom";

import { NotificationBell } from "@/components/NotificationBell";
import { GlobalSearch } from "@/components/search/GlobalSearch";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useAuth } from "@/stores/auth";

export function Layout() {
  const logout = useAuth((s) => s.logout);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 border-b border-hairline bg-surface">
        <div className="mx-auto flex h-14 max-w-screen-2xl items-center justify-between px-4 sm:px-6">
          <Link
            to="/"
            className="text-base font-semibold tracking-tight sm:text-lg"
            aria-label="ProjectHub — go to Boards"
          >
            <span className="text-text-primary">Project</span>
            <span className="text-accent">Hub</span>
          </Link>
          <nav className="flex items-center gap-1 text-sm sm:gap-2">
            <GlobalSearch />
            <NavLink
              to="/"
              end
              className={({ isActive }) =>
                isActive ? "btn-ghost bg-accent-soft text-accent" : "btn-ghost"
              }
            >
              Boards
            </NavLink>
            <NavLink
              to="/space"
              className={({ isActive }) =>
                isActive ? "btn-ghost bg-accent-soft text-accent" : "btn-ghost"
              }
            >
              Space
            </NavLink>
            <NavLink
              to="/profile"
              className={({ isActive }) =>
                isActive
                  ? "btn-ghost gap-1.5 bg-accent-soft text-accent"
                  : "btn-ghost gap-1.5"
              }
              aria-label="Profile"
            >
              <User className="h-4 w-4" />
              <span className="hidden sm:inline">Profile</span>
            </NavLink>
            <NotificationBell />
            <ThemeToggle />
            <button
              type="button"
              onClick={logout}
              className="btn-ghost gap-1.5"
              aria-label="Logout"
            >
              <LogOut className="h-4 w-4" />
              <span className="hidden sm:inline">Logout</span>
            </button>
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-screen-2xl flex-1 px-4 py-4 sm:px-6 sm:py-6">
        <Outlet />
      </main>
    </div>
  );
}
