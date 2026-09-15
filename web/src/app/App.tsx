import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router";

import { NotFoundPage } from "../pages/NotFoundPage";
import { WelcomePage } from "../pages/WelcomePage";
import { createQueryClient } from "./queryClient";

export function App() {
  // Created once per app instance rather than at module level, so tests get a fresh cache each.
  const [queryClient] = useState(createQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<WelcomePage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
