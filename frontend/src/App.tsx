import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { RequireAuth } from "@/components/RequireAuth";
import { BoardDetailPage } from "@/pages/BoardDetail";
import { BoardsPage } from "@/pages/Boards";
import { LoginPage } from "@/pages/Login";
import { TicketDetailPage } from "@/pages/TicketDetail";

export function App() {
  return (
    <BrowserRouter>
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
          <Route
            path="boards/:boardKey/tickets/:ticketKey"
            element={<TicketDetailPage />}
          />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
