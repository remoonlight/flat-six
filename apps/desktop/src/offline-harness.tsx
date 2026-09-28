import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { OfflineDiagnosticsPage } from "./pages/OfflineDiagnosticsPage";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <OfflineDiagnosticsPage />
  </StrictMode>,
);
