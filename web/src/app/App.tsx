import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import { PublicOnly, RequireAuth } from "../auth/guards";
import { DashboardPage } from "../pages/DashboardPage";
import { ImportsPage } from "../pages/ImportsPage";
import { LoginPage } from "../pages/LoginPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { RegisterPage } from "../pages/RegisterPage";
import { AppShell } from "./AppShell";
import { createQueryClient } from "./queryClient";

/**
 * The route table.
 *
 *   /login, /register        only when signed out (PublicOnly)
 *   /dashboard, /imports     only when signed in with an organisation (RequireAuth), inside the shell
 *   /                        → /dashboard
 *   anything else            not found
 *
 * The guards decide what to *show*. What a person may *do* is decided by the API, which refuses
 * every customer route without a valid session whatever the frontend renders.
 */
export function App() {
  // Created once per app instance rather than at module level, so each test gets a fresh cache.
  const [queryClient] = useState(createQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<PublicOnly />}>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
          </Route>

          <Route element={<RequireAuth />}>
            <Route element={<AppShell />}>
              <Route index element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/imports" element={<ImportsPage />} />
            </Route>
          </Route>

          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
