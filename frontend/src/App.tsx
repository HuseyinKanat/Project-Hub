import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { RequireAuth } from "@/components/RequireAuth";
import { ThemeProvider } from "@/components/ThemeProvider";
import { BoardDetailPage } from "@/pages/BoardDetail";
import { BoardSettingsPage } from "@/pages/BoardSettings";
import { BoardsPage } from "@/pages/Boards";
import { DiffDemoPage } from "@/pages/DiffDemo";
import { LoginPage } from "@/pages/Login";
import { TicketDetailPage } from "@/pages/TicketDetail";

export function App() {
  return (
    <ThemeProvider>
      <BrowserRouter
        future={{
          v7_startTransition: true,
          v7_relativeSplatPath: true,
        }}
      >
        <Routes>
        <Route path="/login" element={<LoginPage />} />
        {/* G9 — DiffViewer dev demo: public route so QA/Reviewer can verify without login.
            Hardcoded samples only; live fetch form requires a valid board + sha.
            May be gated behind import.meta.env.DEV or removed in G14. */}
        <Route path="dev/diff-demo" element={<DiffDemoPage />} />
        <Route
          element={
            <RequireAuth>
              <Layout />
            </RequireAuth>
          }
        >
          <Route index element={<BoardsPage />} />
          <Route path="boards/:boardKey" element={<BoardDetailPage />} />
          <Route path="boards/:boardKey/settings" element={<BoardSettingsPage />} />
          <Route
            path="boards/:boardKey/tickets/:ticketKey"
            element={<TicketDetailPage />}
          />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
    </ThemeProvider>
  );
}
