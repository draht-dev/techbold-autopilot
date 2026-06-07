/// <reference path="./.sst/platform/config.d.ts" />

export default $config({
  app(input) {
    return {
      name: "techbold",
      removal: input?.stage === "production" ? "retain" : "remove",
      protect: ["production"].includes(input?.stage),
      home: "aws",
      // providers: { aws: { region: "eu-central-1" } },
    };
  },
  async run() {
    // --- Config & secrets ----------------------------------------------------
    // Set before deploying:  npx sst secret set <NAME> <value>
    // Optional ones can stay at their "" default (feature just stays disabled).
    const phoenixBaseUrl = new sst.Secret("PhoenixApiBaseUrl", "");
    const phoenixToken = new sst.Secret("PhoenixApiToken", "");
    const openrouterKey = new sst.Secret("OpenrouterApiKey", "");
    const elevenLabsKey = new sst.Secret("ElevenLabsApiKey", "");
    const elevenLabsAgentId = new sst.Secret("ElevenLabsAgentId", "");

    // --- Networking + cluster for the containerized backend ------------------
    const vpc = new sst.aws.Vpc("Vpc");
    const cluster = new sst.aws.Cluster("Cluster", { vpc });

    // --- Single HTTPS entry point (CloudFront) -------------------------------
    // Frontend is served at "/", the API/WebSocket are proxied to the backend.
    // Same origin => no CORS and no http/https mixed-content issues, and no
    // custom domain is required (you get an https://*.cloudfront.net URL).
    const router = new sst.aws.Router("Router", {
      domain: "techbold-autopilot.draht.dev",
    });

    // --- Backend: FastAPI on Fargate behind an ALB ---------------------------
    const backend = new sst.aws.Service("Backend", {
      cluster,
      // Builds backend/Dockerfile and pushes to ECR. Docker must be running.
      image: { context: "backend" },
      cpu: "0.5 vCPU",
      memory: "1 GB",
      loadBalancer: {
        rules: [{ listen: "80/http", forward: "8000/http" }],
        // The app's "/" returns 404; point the ALB health check at /health.
        health: {
          "8000/http": {
            path: "/health",
            interval: "10 seconds",
            timeout: "5 seconds",
            healthyThreshold: 2,
          },
        },
      },
      environment: {
        PHOENIX_API_BASE_URL: phoenixBaseUrl.value,
        PHOENIX_API_TOKEN: phoenixToken.value,
        OPENROUTER_API_KEY: openrouterKey.value,
        ELEVENLABS_API_KEY: elevenLabsKey.value,
        ELEVENLABS_AGENT_ID: elevenLabsAgentId.value,
      },
    });

    // Proxy the REST API (/api/*) and WebSockets (/ws/*) to the backend.
    router.route("/api", backend.url);
    router.route("/ws", backend.url);

    // --- Frontend: Vite static site served at the router root ----------------
    const web = new sst.aws.StaticSite("Web", {
      path: "frontend",
      build: { command: "npm run build", output: "dist" },
      router: { instance: router },
      environment: {
        // Same host as the site, so REST hits /api/* and WS hits /ws/* here.
        VITE_API_BASE: router.url,
      },
    });

    return {
      url: web.url,
      router: router.url,
      api: backend.url,
    };
  },
});
