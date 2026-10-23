// One private local model cache for Telegram, dashboard and background jobs.
const fs = require('fs');
const path = require('path');
const root = process.env.CURIE_ROOT || path.resolve(__dirname, '..');
const values = {};
const file = path.join(root, '.env');
if (fs.existsSync(file)) {
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    const match = line.match(/^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$/);
    if (!match) continue;
    let value = match[2];
    if (/^['"]/.test(value)) value = value.slice(1, value.lastIndexOf(value[0]));
    else value = value.replace(/\s+#.*$/, '').trim();
    values[match[1]] = value;
  }
}
// Deliberately do not copy connector credentials or remote-provider API keys.
const modelKeys = [
  'LLM_MODELS', 'LLM_GENERAL_MODEL', 'LLM_FAST_MODEL', 'LLM_REASONING_MODEL',
  'LLM_DEEP_REASONING_MODEL',
  'LLM_CODING_MODEL', 'LLM_CRITIC_MODEL', 'LLM_CONTEXT_SIZE', 'LLM_CONTEXT_BUFFER',
  'LLM_MAX_LOADED_MODELS', 'LLM_THREADS', 'LLM_GPU_LAYERS', 'LLM_ACCELERATOR_MODE',
  'LLM_GPU_LAYERS_BY_MODEL',
  'LLM_DISABLE_THINKING', 'LLM_USE_CHAT_TEMPLATE', 'CURIE_RUNTIME_KERNEL',
];
const env = Object.fromEntries(modelKeys.filter(k => values[k]).map(k => [k, values[k]]));
const logDir = path.join(root, 'log');
fs.mkdirSync(logDir, { recursive: true, mode: 0o700 });
const socketPath = values.CURIE_INFERENCE_SOCKET || path.join(logDir, 'inference', 'worker.sock');
module.exports = { apps: [{
  name: 'curie-inference', cwd: root,
  script: path.join(root, '.venv/bin/python'), interpreter: 'none',
  args: ['-m', 'llm.local_gateway', '--socket', socketPath],
  instances: 1, exec_mode: 'fork', autorestart: true, watch: false,
  restart_delay: 5000, min_uptime: 30000, max_restarts: 3,
  max_memory_restart: '46G', kill_timeout: 15000,
  env: {
    ...env, PYTHON_DOTENV_DISABLED: '1', LLM_PROVIDER_PRIORITY: 'llama.cpp',
    LLM_NPU_ROUTING: 'off', CURIE_INFERENCE_WORKER: '1',
    CURIE_INFERENCE_SOCKET: socketPath, CURIE_MODEL_SUPERVISOR: 'off',
  },
  out_file: path.join(logDir, 'inference-out.log'),
  error_file: path.join(logDir, 'inference-error.log'),
  log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
}] };
