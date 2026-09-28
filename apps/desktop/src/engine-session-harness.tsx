import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { EngineDataPage } from "./pages/EngineDataPage";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <main className="content">
      <EngineDataPage />
    </main>
  </StrictMode>,
);
