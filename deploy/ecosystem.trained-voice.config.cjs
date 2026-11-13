const fs = require('fs');
const path = require('path');

const root = process.env.CURIE_ROOT || path.resolve(__dirname, '..');
const values = {};
const envPath = path.join(root, '.env');
if (fs.existsSync(envPath)) {
  for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const match = line.match(/^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$/);
    if (!match) continue;
    let value = match[2];
    if (/^['"]/.test(value)) value = value.slice(1, value.lastIndexOf(value[0]));
    else value = value.replace(/\s+#.*$/, '').trim();
    values[match[1]] = value;
  }
}

const configured = (dashboardKey, sharedKey, fallback) =>
  process.env[dashboardKey] || values[dashboardKey] || values[sharedKey] || fallback;

const socketPath = configured(
  'CURIE_DASHBOARD_INFERENCE_SOCKET',
  'CURIE_INFERENCE_SOCKET',
  path.join(root, 'log', 'inference', 'worker.sock'),
);
const baseEnv = {
  LLM_ALTERNATE_ENABLED: values.LLM_ALTERNATE_ENABLED || 'false',
  LLM_ALTERNATE_MODEL: values.LLM_ALTERNATE_MODEL || 'huihui_ai/qwen3.6-abliterated:35b',
  CURIE_ROOT: root,
  CURIE_API_HOST: '127.0.0.1',
  CURIE_INFERENCE_SOCKET: socketPath,
  RUN_API: 'true',
  RUN_TELEGRAM: 'false',
  RUN_DISCORD: 'false',
  RUN_WHATSAPP: 'false',
  RUN_SLACK: 'false',
  RUN_SIGNAL: 'false',
  RUN_CODER: 'false',
  RUN_CODING_SERVICE: 'false',
  ENABLE_PROACTIVE_MESSAGING: 'false',
  HOME_INVENTORY_WARM_ON_STARTUP: 'false',
  X_AUTOPOST_ENABLED: 'false',
  LLM_MODELS: configured(
    'CURIE_DASHBOARD_LLM_MODELS',
    'LLM_MODELS',
    'Qwen3-4B-Q4_K_M.gguf',
  ),
  LLM_GENERAL_MODEL: configured(
    'CURIE_DASHBOARD_GENERAL_MODEL',
    'LLM_GENERAL_MODEL',
    'Qwen3-4B-Q4_K_M.gguf',
  ),
  LLM_FAST_MODEL: configured(
    'CURIE_DASHBOARD_FAST_MODEL',
    'LLM_FAST_MODEL',
    'Qwen3-4B-Q4_K_M.gguf',
  ),
  LLM_REASONING_MODEL: configured(
    'CURIE_DASHBOARD_REASONING_MODEL',
    'LLM_REASONING_MODEL',
    'Qwen3-4B-Q4_K_M.gguf',
  ),
  LLM_CODING_MODEL: configured(
    'CURIE_DASHBOARD_CODING_MODEL',
    'LLM_CODING_MODEL',
    'Qwen3-4B-Q4_K_M.gguf',
  ),
  LLM_CRITIC_MODEL: configured(
    'CURIE_DASHBOARD_CRITIC_MODEL',
    'LLM_CRITIC_MODEL',
    'Qwen3-4B-Q4_K_M.gguf',
  ),
  LLM_MAX_LOADED_MODELS: configured(
    'CURIE_DASHBOARD_MAX_LOADED_MODELS',
    'LLM_MAX_LOADED_MODELS',
    '2',
  ),
};

const app = (name, port) => ({
  name,
  cwd: root,
  script: path.join(root, '.venv/bin/python'),
  interpreter: 'none',
  args: ['-m', 'services.trained_voice.api'],
  autorestart: true,
  max_restarts: 3,
  min_uptime: 30000,
  restart_delay: 10000,
  env: {...baseEnv, CURIE_API_PORT: String(port)},
});

module.exports = {
  apps: [
    app('curie-dashboard-api', 8010),
    app('curie-dashboard-api-recovery', 8011),
  ],
};
