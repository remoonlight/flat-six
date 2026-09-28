import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ReadOnlySessionPage } from "./pages/ReadOnlySessionPage";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ReadOnlySessionPage />
  </StrictMode>,
);
