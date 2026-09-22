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
    X_AUTOPOST_ENABLED: 'false'
  }
}] };
