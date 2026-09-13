import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles/base.css";

const container = document.getElementById("root");

// Throwing beats a silent no-op. If the served `index.html` ever stops carrying
// the mount node -- a template edit, a wrong catch-all route returning the
// marketing shell -- a blank page with a clean console is the worst possible
// symptom, because it looks like a slow load.
if (!container) {
  throw new Error(
    'PulseSoc web: #root is missing from the document. The served index.html ' +
      "is not the built SPA shell."
  );
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>
);
