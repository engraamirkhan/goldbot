import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import { takeLinkTokens } from "./lib/links";
import "./styles.css";

// Take any invite, reset or sign-in token out of the address bar now, signed in or not: a signed-in browser never
// renders the login screen, and the token must not stay in the URL or the history entry (security review L4).
takeLinkTokens();

const qc = new QueryClient({ defaultOptions: { queries: { refetchInterval: 5000, retry: 1 } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
