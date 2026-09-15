import { render } from "@testing-library/react";

import { App } from "../app/App";

/** Render the whole app, with its real router and a fresh query cache, at `path`. */
export function renderApp(path: string) {
  window.history.pushState({}, "", path);
  return render(<App />);
}
