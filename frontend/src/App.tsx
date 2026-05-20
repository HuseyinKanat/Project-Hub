import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { RequireAuth } from "@/components/RequireAuth";
import { ThemeProvider } from "@/components/ThemeProvider";
import { BoardDetailPage } from "@/pages/BoardDetail";
import { BoardSettingsPage } from "@/pages/BoardSettings";
import { BoardsPage } from "@/pages/Boards";
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
