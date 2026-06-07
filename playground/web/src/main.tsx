import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { AgentView } from "./pages/AgentView";
import "./style.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AgentView />
  </StrictMode>,
);
