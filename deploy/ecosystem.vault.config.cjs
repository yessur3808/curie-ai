// Start only Curie's existing apps through the private vault launcher.
// Credential values stay in child-process memory, outside PM2's saved config.
const path = require("path");
const root = path.resolve(__dirname, "..");
const original = require(path.join(root, "ecosystem.config.js"));
const voice = require(path.join(__dirname, "ecosystem.trained-voice.config.cjs"));
const secretNames = [
  "CURIE_CREDENTIAL_KEY", "CURIE_SETUP_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN",
  "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI",
  "GOVEE_API_KEY", "X_OAUTH_CLIENT_ID", "X_OAUTH_CLIENT_SECRET", "X_OAUTH_REDIRECT_URI",
];
const wanted = new Set([
  "curie-main", "curie-dashboard-api", "curie-dashboard-api-recovery",
]);
const privateConfig = path.join(process.env.HOME, ".config/agent-secrets/runtime/curie.json");
const python = path.join(root, ".venv/bin/python");
module.exports = {
  apps: [...original.apps, ...voice.apps].filter(app => wanted.has(app.name)).map(app => {
    const command = app.name === "curie-main"
      ? [python, path.join(root, "main.py")]
      : [python, "-m", "services.trained_voice.api"];
    return {
      ...app,
      cwd: root,
      script: path.join(root, "scripts/vault_exec.py"),
      interpreter: python,
      args: ["--", ...command],
      env: {
        ...app.env,
        ...Object.fromEntries(secretNames.map(key => [key, ""])),
        CURIE_INSTANCE: "curie",
        CURIE_VAULT_CONFIG: privateConfig,
      },
    };
  }),
};
