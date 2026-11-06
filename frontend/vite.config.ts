import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { host: "0.0.0.0", port: 5173 },
  // `vite preview` runs in production (Railway). Allow the Railway-assigned
  // public host instead of rejecting it as an unknown host.
  preview: { host: "0.0.0.0", port: 5173, allowedHosts: true },
});
