import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "@xterm/xterm/css/xterm.css";
import "./theme.css";

// StrictMode is intentionally omitted: its dev double-invoke would open the run
// WebSocket twice (the app runs on the Vite dev server during the demo).
ReactDOM.createRoot(document.getElementById("root")!).render(
  <BrowserRouter>
    <App />
  </BrowserRouter>
);
