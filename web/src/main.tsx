import "@fontsource-variable/inter";
import "./styles/tokens.css";
import "./styles/global.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";

const container = document.getElementById("root");

if (!container) {
  throw new Error("index.html has no #root element.");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
