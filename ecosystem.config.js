const path = require("path");
const fs = require("fs");

// Load .env file to pass environment to pm2 subprocess
const envPath = path.join(__dirname, ".env");
const envConfig = {};
const logDir = path.join(__dirname, "log");

// PM2 does not create parent directories for custom log files.
fs.mkdirSync(logDir, { recursive: true, mode: 0o700 });

function loadEnvFile(filePath, target) {
    if (!fs.existsSync(filePath)) return;
    const envContent = fs.readFileSync(filePath, "utf-8");
    envContent.split("\n").forEach((line) => {
        const [key, ...valueParts] = line.split("=");
        if (key && key.trim() && !key.trim().startsWith("#")) {
            const rawValue = valueParts.join("=").trim();
            const quote = rawValue[0];
            target[key.trim()] =
                (quote === '"' || quote === "'") && rawValue.at(-1) === quote
                    ? rawValue.slice(1, -1)
                    : rawValue;
        }
    });
}

loadEnvFile(envPath, envConfig);
const instanceName = envConfig.CURIE_INSTANCE || "curie";
loadEnvFile(path.join(__dirname, "instances", `${instanceName}.env`), envConfig);
envConfig.CURIE_INSTANCE = instanceName;

// Keep the API opt-in because port 8000 may already be owned by another
// service. Telegram remains the default connector for this installation.
const runApi = envConfig.RUN_API || "false";
const runTelegram = envConfig.RUN_TELEGRAM || "true";

module.exports = {
    apps: [
        {
            name: "curie-main",
            cwd: __dirname,
            script: path.join(__dirname, "main.py"),
            interpreter: path.join(__dirname, ".venv", "bin", "python"),
            args: [],
            instances: 1,
            exec_mode: "fork",
            autorestart: true,
            watch: false,
            max_memory_restart: "12G",
            env: {
                ...envConfig,
                RUN_API: runApi,
                RUN_TELEGRAM: runTelegram,
            },
            error_file: path.join(logDir, "pm2-error.log"),
            out_file: path.join(logDir, "pm2-out.log"),
            log_date_format: "YYYY-MM-DD HH:mm:ss Z",
            kill_timeout: 15000,
            listen_timeout: 10000,
        },
    ],
};
