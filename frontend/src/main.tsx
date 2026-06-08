import { QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";

import { App } from "@/App";
// PH-232: the QueryClient singleton lives in @/lib/queryClient so non-React code
// (the Zustand auth store) can import the SAME instance and clear() it at an
// identity boundary without forming a main.tsx ↔ auth.ts import cycle.
import { queryClient } from "@/lib/queryClient";

import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={queryClient}>
    <App />
  </QueryClientProvider>,
);
