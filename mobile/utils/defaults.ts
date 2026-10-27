// Build-time defaults. EXPO_PUBLIC_API_BASE (repo variable in CI) overrides the server URL.
export const DEFAULT_SERVER_URL =
  process.env.EXPO_PUBLIC_API_BASE || "https://pred-agent-54kw.onrender.com";

// Matches the render.yaml default; the password is never baked into the app.
export const DEFAULT_USERNAME = "admin";
