const path = require('path');
const root = process.env.CURIE_ROOT || path.resolve(__dirname, '..');

module.exports = { apps: [{
  name: 'curie-dashboard-api',
  cwd: root,
  script: path.join(root, '.venv/bin/python'),
  interpreter: 'none',
  args: ['-m', 'services.trained_voice.api'],
  autorestart: true,
  max_restarts: 3,
  min_uptime: 30000,
  restart_delay: 10000,
  env: {
    CURIE_ROOT: root,
    CURIE_API_HOST: '127.0.0.1',
    CURIE_API_PORT: '8010',
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
    // Dashboard conversation favors the compact model for normal chat, while
    // analysis and code still route to Curie's stronger specialist models.
    LLM_MODELS: process.env.CURIE_DASHBOARD_LLM_MODELS || 'Qwen3-4B-Q4_K_M.gguf,Qwen3.5-9B-Q4_K_M.gguf,Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf',
    LLM_GENERAL_MODEL: process.env.CURIE_DASHBOARD_GENERAL_MODEL || 'Qwen3-4B-Q4_K_M.gguf',
    LLM_FAST_MODEL: process.env.CURIE_DASHBOARD_FAST_MODEL || 'Qwen3-4B-Q4_K_M.gguf',
    LLM_REASONING_MODEL: process.env.CURIE_DASHBOARD_REASONING_MODEL || 'Qwen3.5-9B-Q4_K_M.gguf',
    LLM_CODING_MODEL: process.env.CURIE_DASHBOARD_CODING_MODEL || 'Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf',
    LLM_MAX_LOADED_MODELS: process.env.CURIE_DASHBOARD_MAX_LOADED_MODELS || '2'
  }
}] };
