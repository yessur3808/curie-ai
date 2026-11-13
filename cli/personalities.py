"""Provision clean, independent assistant installations; never clone user state."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

SOURCE_DIRS = ('agent', 'cli', 'config', 'connectors', 'contracts', 'llm', 'memory',
               'migrations', 'services', 'utils', 'native')
SOURCE_FILES = ('main.py', 'curie', 'pyproject.toml', 'requirements.txt',
                'requirements-voice-custom.txt', 'requirements-optional.txt', 'LICENSE')


def provision(name: str, persona: Path, source: Path, parent: Path) -> Path:
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', name):
        raise ValueError('Use a lowercase name with letters, numbers, hyphens or underscores.')
    source, parent = source.resolve(), parent.resolve()
    profile = json.loads(persona.read_text())
    if not isinstance(profile, dict) or not profile.get('name') or not profile.get('system_prompt'):
        raise ValueError('Persona must contain name and system_prompt.')
    # A new assistant gets its own voice identity, never a source adapter path.
    profile['voice'] = {'trained_id': name}
    profile.setdefault('description', f'{name} independent assistant')
    destination = parent / f'{name}-ai'
    if destination.exists():
        raise FileExistsError(f'Installation already exists: {destination}')
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f'.{name}-provision-', dir=parent))
    try:
        # Only application source. Never copy .env, databases, browser sessions,
        # credentials, downloaded assets, training sets, logs or another persona.
        for directory in SOURCE_DIRS:
            origin = source / directory
            if not origin.is_dir():
                continue
            for file in origin.rglob('*'):
                if file.is_symlink() or '__pycache__' in file.parts or not file.is_file():
                    continue
                if file.suffix not in {'.py', '.rs', '.c', '.h'} and file.relative_to(source).as_posix() != 'config/model_catalog.json':
                    continue
                target = staging / file.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, target)
        for filename in SOURCE_FILES:
            if (source / filename).is_file():
                shutil.copy2(source / filename, staging / filename)
        for directory in ('state', 'log/inference', 'models', 'data/trained-voices/' + name,
                          'assets/personality', 'instances', 'deploy'):
            (staging / directory).mkdir(parents=True, exist_ok=True, mode=0o700)
        (staging / 'assets/personality' / f'{name}.json').write_text(json.dumps(profile, indent=2) + '\n')
        env = {
            'CURIE_INSTANCE': name, 'ASSISTANT_NAME': profile['name'], 'PERSONA_FILE': f'{name}.json',
            'CURIE_ROOT': str(destination), 'ASSISTANT_STATE_DIR': str(destination / 'state'),
            'CURIE_LOCAL_MEMORY_DB': str(destination / 'state/memory.sqlite3'),
            'CURIE_INFERENCE_SOCKET': str(destination / 'log/inference/worker.sock'),
            'CURIE_LOG_FILE': str(destination / 'log/assistant.log'),
            'CURIE_X_BROWSER_PROFILE_ROOT': str(destination / 'state/browser-profiles'),
            'CURIE_OBSERVATORY_DATA_DIR': str(destination / 'state/observatory'),
            'XDG_CACHE_HOME': str(destination / 'state/cache'),
            'HF_HOME': str(destination / 'state/cache/huggingface'),
            'CURIE_TRAINED_VOICE_LOCK_PATH': str(destination / 'state/voice.lock'),
            'CURIE_VOICE_PYTHON': str(destination / '.trained-voice-venv/bin/python'),
            'CURIE_TRANSCRIBE_PYTHON': str(destination / '.transcription-venv/bin/python'),
            f'CURIE_VOICE_HOME_{name.upper().replace("-", "_")}': str(destination / 'data/trained-voices' / name),
            'PYTHON_DOTENV_DISABLED': '1', 'CURIE_MODEL_SUPERVISOR': 'off',
            'LLM_NPU_ROUTING': 'off', 'LLM_GPU_LAYERS': '0', 'LLM_ACCELERATOR_MODE': 'cpu',
            'LLM_MAX_LOADED_MODELS': '1', 'LLM_MAX_RESIDENT_BYTES': str(28 * 1024**3),
            'RUN_TELEGRAM': 'true', 'RUN_API': 'false', 'RUN_DISCORD': 'false',
            'ENABLE_PROACTIVE_MESSAGING': 'false', 'NOTIFY_MASTER_ON_STARTUP': 'false',
            'TELEGRAM_BOT_TOKEN': '', 'MASTER_USER_ID': '',
        }
        (staging / '.env').write_text(''.join(f'{k}={json.dumps(v)}\n' for k, v in env.items()))
        (staging / '.env').chmod(0o600)
        (staging / 'instances' / f'{name}.env').write_text(f'CURIE_INSTANCE={name}\n')
        manifest = {'schema': 1, 'name': name, 'root': str(destination),
                    'persona': f'{name}.json', 'status': 'needs-configuration',
                    'source': str(source), 'memory': 'fresh', 'voice': 'not-configured'}
        (staging / 'state/installation.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (staging / 'launch.py').write_text("""import sys
from pathlib import Path
from utils.installation import bootstrap
bootstrap(Path(__file__).resolve().parent)
import os
import runpy
if len(sys.argv) > 1 and sys.argv[1] == 'inference':
    os.environ['CURIE_INFERENCE_WORKER'] = '1'
    sys.argv = ['llm.local_gateway', '--socket', os.environ['CURIE_INFERENCE_SOCKET']]
    runpy.run_module('llm.local_gateway', run_name='__main__')
else:
    sys.argv = ['main.py']
    runpy.run_path(str(Path(__file__).resolve().parent / 'main.py'), run_name='__main__')
""")
        (staging / 'ecosystem.config.js').write_text("""const fs = require('fs');
const path = require('path');
const root = __dirname;
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'state/installation.json')));
const env = {};
for (const line of fs.readFileSync(path.join(root, '.env'), 'utf8').split('\\n')) {
 const m = line.match(/^([A-Z][A-Z0-9_]*)=(.*)$/);
 if (m) { try { env[m[1]] = JSON.parse(m[2]); } catch { env[m[1]] = m[2]; } }
}
if (env.CURIE_INSTANCE !== manifest.name || env.PERSONA_FILE !== manifest.persona)
 throw Error('This installation is bound to one personality. Use curie personality add.');
if (!env.TELEGRAM_BOT_TOKEN || !env.MASTER_USER_ID)
 throw Error('Configure this assistant own TELEGRAM_BOT_TOKEN and MASTER_USER_ID in its private .env.');
if (!fs.existsSync(path.join(root, '.venv/bin/python')))
 throw Error('Install this assistant own Python environment first.');
module.exports = {apps: [
 {name: manifest.name + '-inference', cwd: root, script: path.join(root, '.venv/bin/python'),
 interpreter: 'none', args: ['launch.py', 'inference'],
 env: {...env, CURIE_INFERENCE_WORKER: '1'}, instances: 1, autorestart: true, max_restarts: 3,
 out_file: path.join(root,'log/inference-out.log'), error_file: path.join(root,'log/inference-error.log')},
 {name: manifest.name + '-main', cwd: root, script: path.join(root, '.venv/bin/python'),
 interpreter: 'none', args: ['launch.py'], env, instances: 1, autorestart: true, max_restarts: 3,
 out_file: path.join(root,'log/main-out.log'), error_file: path.join(root,'log/main-error.log')}
]};
""")
        (staging / 'SETUP.md').write_text(f'''# {profile['name']} independent installation

This is a fresh installation, not a running bot. No credentials, conversations,
voice recordings, trained adapters or integrations were inherited.

1. Create a private `.venv` here and install `requirements.txt`.
2. Install model weights into this installation's `models/` and configure LLM_MODELS
   and LLM_GENERAL_MODEL in `.env`. Never point at another assistant's gateway.
3. Configure this bot's own Telegram token and owner ID privately in `.env`.
4. Configure its own voice environment/assets before enabling voice replies.
5. Start its own main and inference services using `pm2 start ecosystem.config.js`.
   Check available RAM before starting additional model workers.

Application state lives in `state/`; this is application isolation on the same OS,
not a container or separate OS account. Adding personalities: `./curie personality add NAME --persona FILE`.
''')
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def command(args):
    root = Path(__file__).resolve().parents[1]
    try:
        target = provision(args.name, Path(args.persona), root, Path(args.parent) if args.parent else root.parent)
    except (ValueError, OSError) as exc:
        print(f'Cannot create personality: {exc}')
        return 1
    print(f'Created isolated personality: {target}\nStatus: needs configuration; not started. Read {target / "SETUP.md"}')
    return 0


def register(subs):
    parser = subs.add_parser('personality', help='Create independent personality installations')
    actions = parser.add_subparsers(dest='personality_action', required=True)
    add = actions.add_parser('add', help='Create a fresh isolated assistant (never copies memory or credentials)')
    add.add_argument('name')
    add.add_argument('--persona', required=True, help='Persona JSON definition')
    add.add_argument('--parent', help='Parent directory for NAME-ai (default: sibling installation)')
    add.set_defaults(func=command)
