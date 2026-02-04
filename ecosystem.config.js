const path = require("path");
const fs = require("fs");

// Load .env file to pass environment to pm2 subprocess
const envPath = path.join(__dirname, ".env");
const envConfig = {};

if (fs.existsSync(envPath)) {
    const envContent = fs.readFileSync(envPath, "utf-8");
    envContent.split("\n").forEach((line) => {
        const [key, ...valueParts] = line.split("=");
        if (key && key.trim() && !key.trim().startsWith("#")) {
            envConfig[key.trim()] = valueParts.join("=").trim().replace(/^"|"$/g, "");
        }
    });
}

const runApi = envConfig.RUN_API || "true";
const runTelegram = envConfig.RUN_TELEGRAM || "true";

module.exports = {
    apps: [
        {
            name: "curie-main",
            cwd: __dirname,
            script: path.join(__dirname, "main.py"),
            interpreter: path.join(__dirname, "ai_venv", "bin", "python"),
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
            error_file: path.join(__dirname, "log", "pm2-error.log"),
            out_file: path.join(__dirname, "log", "pm2-out.log"),
            log_date_format: "YYYY-MM-DD HH:mm:ss Z",
            kill_timeout: 15000,
            listen_timeout: 10000,
        },
    ],
};
