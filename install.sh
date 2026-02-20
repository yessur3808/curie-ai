#!/usr/bin/env bash
# =============================================================================
#  C.U.R.I.E. AI - Installer for macOS and Linux
#  https://github.com/yessur3808/curie-ai
# =============================================================================
set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Helpers ───────────────────────────────────────────────────────────────────
info()    { echo -e "${CYAN}${BOLD}[curie]${RESET} $*"; }
success() { echo -e "${GREEN}${BOLD}[✔]${RESET} $*"; }
warn()    { echo -e "${YELLOW}${BOLD}[!]${RESET} $*"; }
error()   { echo -e "${RED}${BOLD}[✘]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }

prompt() {
  # prompt <VAR_NAME> <message> [default]
  local var="$1" msg="$2" default="${3:-}"
  local display_default=""
  [[ -n "$default" ]] && display_default=" (default: ${BOLD}${default}${RESET})"
  echo -ne "${BLUE}${BOLD}?${RESET} ${msg}${display_default}: "
  read -r value
  [[ -z "$value" && -n "$default" ]] && value="$default"
  eval "$var=\"\$value\""
}

prompt_secret() {
  local var="$1" msg="$2"
  echo -ne "${BLUE}${BOLD}?${RESET} ${msg} (hidden): "
  read -rs value
  echo
  eval "$var=\"\$value\""
}

prompt_yn() {
  # prompt_yn <message> <default y|n>  → returns 0 for yes, 1 for no
  local msg="$1" default="${2:-y}"
  local opts="[Y/n]"
  [[ "$default" == "n" ]] && opts="[y/N]"
  echo -ne "${BLUE}${BOLD}?${RESET} ${msg} ${opts}: "
  read -r yn
  [[ -z "$yn" ]] && yn="$default"
  [[ "$yn" =~ ^[Yy] ]]
}

section() {
  echo
  echo -e "${BOLD}${BLUE}━━━ $* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
}

command_exists() { command -v "$1" &>/dev/null; }

# ── Banner ────────────────────────────────────────────────────────────────────
echo
echo -e "${CYAN}${BOLD}"
cat <<'EOF'
   ____  __  __  ____  ___  ____
  / __/ / / / / / _ \ /  _// __/
 / /__ / /_/ / / , _/_/  _// _/  
 \___/ \____/ /_/|_|/____//___/  
                                  
  Clever Understanding and Reasoning Intelligent Entity
EOF
echo -e "${RESET}"
echo -e "  ${BOLD}Installer for macOS & Linux${RESET}"
echo -e "  https://github.com/yessur3808/curie-ai"
echo

# ── Parse flags ───────────────────────────────────────────────────────────────
SKIP_ONBOARD=0
INSTALL_DIR="${CURIE_INSTALL_DIR:-$HOME/.curie-ai}"
NON_INTERACTIVE=0

for arg in "$@"; do
  case "$arg" in
    --no-onboard)       SKIP_ONBOARD=1 ;;
    --non-interactive)  NON_INTERACTIVE=1 ;;
    --install-dir=*)    INSTALL_DIR="${arg#*=}" ;;
    --help|-h)
      echo "Usage: install.sh [options]"
      echo
      echo "Options:"
      echo "  --no-onboard          Skip interactive setup, just install files"
      echo "  --non-interactive     Assume defaults for all prompts (CI mode)"
      echo "  --install-dir=PATH    Install to a custom directory (default: ~/.curie-ai)"
      echo "  --help                Show this help"
      exit 0
      ;;
  esac
done

[[ "$NON_INTERACTIVE" == "1" ]] && SKIP_ONBOARD=1

# ── OS detection ──────────────────────────────────────────────────────────────
section "Checking your system"

OS=""
case "$(uname -s)" in
  Darwin) OS="macos" ;;
  Linux)  OS="linux" ;;
  *)      die "Unsupported OS: $(uname -s). Only macOS and Linux are supported." ;;
esac
info "Detected OS: ${BOLD}${OS}${RESET}"

# ── Dependency: git ───────────────────────────────────────────────────────────
if ! command_exists git; then
  warn "git is not installed."
  if [[ "$OS" == "macos" ]]; then
    info "Installing git via Xcode Command Line Tools..."
    xcode-select --install 2>/dev/null || true
    die "Please re-run this installer after the Xcode CLT installation completes."
  else
    die "Please install git first: sudo apt install git  OR  sudo dnf install git"
  fi
fi
success "git found: $(git --version)"

# ── Dependency: Python 3.10+ ──────────────────────────────────────────────────
section "Checking Python"

PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command_exists "$candidate"; then
    version=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)
    if [[ "$major" -ge 3 && "$minor" -ge 10 ]]; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  warn "Python 3.10+ not found."
  if [[ "$OS" == "macos" ]]; then
    if command_exists brew; then
      info "Installing Python 3.12 via Homebrew..."
      brew install python@3.12
      PYTHON="python3.12"
    else
      die "Homebrew not found. Install it from https://brew.sh then re-run this installer."
    fi
  else
    # Linux - try common package managers
    if command_exists apt-get; then
      info "Installing Python 3.11 via apt..."
      sudo apt-get update -qq
      sudo apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip
      PYTHON="python3.11"
    elif command_exists dnf; then
      info "Installing Python 3.11 via dnf..."
      sudo dnf install -y python3.11 python3.11-devel
      PYTHON="python3.11"
    elif command_exists pacman; then
      info "Installing Python via pacman..."
      sudo pacman -S --noconfirm python python-pip
      PYTHON="python3"
    else
      die "Cannot install Python automatically. Please install Python 3.10+ manually and re-run."
    fi
  fi
fi

success "Python found: ${BOLD}$PYTHON${RESET} ($(${PYTHON} --version))"

# ── Dependency: pip ───────────────────────────────────────────────────────────
if ! "$PYTHON" -m pip --version &>/dev/null; then
  info "Installing pip..."
  curl -fsSL https://bootstrap.pypa.io/get-pip.py | "$PYTHON"
fi
success "pip found"

# ── Clone or update the repo ──────────────────────────────────────────────────
section "Installing Curie AI"

REPO_URL="https://github.com/yessur3808/curie-ai.git"

if [[ -d "$INSTALL_DIR/.git" ]]; then
  info "Existing installation found at ${BOLD}${INSTALL_DIR}${RESET}"
  info "Pulling latest changes..."
  git -C "$INSTALL_DIR" pull --ff-only
  UPGRADE=1
else
  info "Cloning into ${BOLD}${INSTALL_DIR}${RESET}..."
  git clone "$REPO_URL" "$INSTALL_DIR"
  UPGRADE=0
fi
success "Curie AI source is ready"

cd "$INSTALL_DIR"

# ── Virtual environment ───────────────────────────────────────────────────────
section "Setting up Python environment"

VENV_DIR="$INSTALL_DIR/venv"

if [[ ! -d "$VENV_DIR" ]]; then
  info "Creating virtual environment..."
  "$PYTHON" -m venv "$VENV_DIR"
fi

# Activate venv
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
success "Virtual environment activated"

# Upgrade pip inside venv silently
pip install --upgrade pip --quiet

info "Installing Python dependencies (this may take a few minutes)..."
pip install -r requirements.txt --quiet
success "Core dependencies installed"

# Optional deps
if [[ "$NON_INTERACTIVE" == "0" ]]; then
  echo
  if prompt_yn "Install optional dependencies? (voice/WhatsApp features — may fail on Python 3.13+)" "n"; then
    warn "Note: openai-whisper may fail to build on Python 3.13+. Skipping if it errors."
    pip install -r requirements-optional.txt --quiet || warn "Some optional dependencies failed to install — core features will still work."
    success "Optional dependencies installed"
  fi
fi

# ── Models directory ──────────────────────────────────────────────────────────
section "LLM Model Setup"

MODELS_DIR="$INSTALL_DIR/models"
mkdir -p "$MODELS_DIR"

info "Curie runs on local GGUF models (e.g. Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf)"
info "Models directory: ${BOLD}${MODELS_DIR}${RESET}"

if [[ "$NON_INTERACTIVE" == "0" ]]; then
  echo
  warn "You need at least one GGUF model file to use Curie."
  echo "  Download recommended model (~4.9GB):"
  echo "  https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/resolve/main/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
  echo
  if command_exists huggingface-cli; then
    if prompt_yn "Download the recommended Llama 3.1 8B Q4 model now via huggingface-cli?" "n"; then
      info "Downloading model... (this is ~4.9GB, go make a coffee ☕)"
      huggingface-cli download bartowski/Meta-Llama-3.1-8B-Instruct-GGUF \
        Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf \
        --local-dir "$MODELS_DIR"
      success "Model downloaded"
    fi
  else
    echo -e "  ${YELLOW}Tip:${RESET} pip install huggingface-hub  then re-run to auto-download models."
  fi

  # Let user specify existing model path
  echo
  EXISTING_MODELS=()
  while IFS= read -r -d '' f; do
    EXISTING_MODELS+=("$(basename "$f")")
  done < <(find "$MODELS_DIR" -name "*.gguf" -print0 2>/dev/null)

  if [[ ${#EXISTING_MODELS[@]} -gt 0 ]]; then
    success "Found model(s) in ${MODELS_DIR}:"
    for m in "${EXISTING_MODELS[@]}"; do echo "    • $m"; done
    LLM_MODELS_DEFAULT="${EXISTING_MODELS[0]}"
  else
    warn "No .gguf files found in ${MODELS_DIR}"
    warn "You must manually place a .gguf file there before running Curie."
    LLM_MODELS_DEFAULT="Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
  fi
fi

# ── Interactive .env setup ────────────────────────────────────────────────────
ENV_FILE="$INSTALL_DIR/.env"

if [[ "$SKIP_ONBOARD" == "0" ]]; then
  section "Configuration Setup"

  echo -e "  Let's configure Curie. Press ${BOLD}Enter${RESET} to accept defaults."
  echo

  # ── Connectors ──
  echo -e "${BOLD}Which connectors do you want to enable?${RESET}"
  RUN_TELEGRAM="false"; RUN_DISCORD="false"; RUN_WHATSAPP="false"; RUN_API="true"

  prompt_yn "  Enable Telegram bot?" "y" && RUN_TELEGRAM="true" || RUN_TELEGRAM="false"
  prompt_yn "  Enable Discord bot?" "n" && RUN_DISCORD="true" || RUN_DISCORD="false"
  prompt_yn "  Enable WhatsApp connector (beta)?" "n" && RUN_WHATSAPP="true" || RUN_WHATSAPP="false"
  prompt_yn "  Enable REST API?" "y" && RUN_API="true" || RUN_API="false"
  echo

  # ── Telegram ──
  TELEGRAM_BOT_TOKEN=""
  if [[ "$RUN_TELEGRAM" == "true" ]]; then
    echo -e "${BOLD}Telegram Setup${RESET}"
    echo "  Get your bot token from @BotFather on Telegram."
    prompt_secret TELEGRAM_BOT_TOKEN "Telegram bot token"
    echo
  fi

  # ── Discord ──
  DISCORD_BOT_TOKEN=""
  if [[ "$RUN_DISCORD" == "true" ]]; then
    echo -e "${BOLD}Discord Setup${RESET}"
    echo "  Get your token from https://discord.com/developers/applications"
    prompt_secret DISCORD_BOT_TOKEN "Discord bot token"
    echo
  fi

  # ── WhatsApp ──
  WHATSAPP_SESSION_PATH="./whatsapp_session"
  if [[ "$RUN_WHATSAPP" == "true" ]]; then
    echo -e "${BOLD}WhatsApp Setup${RESET}"
    prompt WHATSAPP_SESSION_PATH "WhatsApp session path" "./whatsapp_session"
    echo
  fi

  # ── Master user ──
  echo -e "${BOLD}Admin User${RESET}"
  echo "  This is the Telegram/Discord user ID that gets admin privileges."
  prompt MASTER_USER_ID "Master user ID" ""
  echo

  # ── LLM Model ──
  echo -e "${BOLD}LLM Model${RESET}"
  prompt LLM_MODELS "GGUF model filename (just the filename, not the full path)" "${LLM_MODELS_DEFAULT:-Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf}"
  echo

  # ── Databases ──
  echo -e "${BOLD}PostgreSQL Setup${RESET}"
  prompt POSTGRES_HOST     "PostgreSQL host"     "localhost"
  prompt POSTGRES_PORT     "PostgreSQL port"     "5432"
  prompt POSTGRES_DB       "PostgreSQL database" "assistant_db"
  prompt POSTGRES_USER     "PostgreSQL username" "postgres"
  prompt_secret POSTGRES_PASSWORD "PostgreSQL password"
  echo

  echo -e "${BOLD}MongoDB Setup${RESET}"
  prompt MONGODB_URI "MongoDB URI"      "mongodb://localhost:27017/"
  prompt MONGODB_DB  "MongoDB database" "assistant_db"
  echo

  # ── Optional extras ──
  echo -e "${BOLD}Optional: GitHub / GitLab / Bitbucket (for code review features)${RESET}"
  GITHUB_TOKEN=""; GITLAB_TOKEN=""; GITLAB_URL="https://gitlab.com"
  BITBUCKET_USERNAME=""; BITBUCKET_APP_PASSWORD=""

  if prompt_yn "  Configure code platform tokens?" "n"; then
    prompt_secret GITHUB_TOKEN "GitHub personal access token (leave blank to skip)"
    prompt_secret GITLAB_TOKEN "GitLab personal access token (leave blank to skip)"
    [[ -n "$GITLAB_TOKEN" ]] && prompt GITLAB_URL "GitLab URL" "https://gitlab.com"
    prompt BITBUCKET_USERNAME     "Bitbucket username (leave blank to skip)" ""
    [[ -n "$BITBUCKET_USERNAME" ]] && prompt_secret BITBUCKET_APP_PASSWORD "Bitbucket app password"
  fi
  echo

  # ── Voice / Whisper ──
  WHISPER_MODEL="base"
  if prompt_yn "  Configure Whisper model for voice? (tiny/base/small/medium/large)" "n"; then
    prompt WHISPER_MODEL "Whisper model size" "base"
  fi

  # ── Write .env ──
  section "Writing configuration"

  cat > "$ENV_FILE" <<EOF
# ── Generated by Curie installer on $(date) ──

# ── Connectors ──────────────────────────────────────────────────────────────
RUN_TELEGRAM=${RUN_TELEGRAM}
RUN_DISCORD=${RUN_DISCORD}
RUN_WHATSAPP=${RUN_WHATSAPP}
RUN_API=${RUN_API}
RUN_CODER=false
RUN_CODING_SERVICE=false

# ── Bot Tokens ───────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
DISCORD_BOT_TOKEN=${DISCORD_BOT_TOKEN}
WHATSAPP_SESSION_PATH=${WHATSAPP_SESSION_PATH}

# ── Admin ────────────────────────────────────────────────────────────────────
MASTER_USER_ID=${MASTER_USER_ID}

# ── LLM ─────────────────────────────────────────────────────────────────────
LLM_MODELS=${LLM_MODELS}

# ── PostgreSQL ───────────────────────────────────────────────────────────────
POSTGRES_HOST=${POSTGRES_HOST}
POSTGRES_PORT=${POSTGRES_PORT}
POSTGRES_DB=${POSTGRES_DB}
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}

# ── MongoDB ──────────────────────────────────────────────────────────────────
MONGODB_URI=${MONGODB_URI}
MONGODB_DB=${MONGODB_DB}

# ── Voice ────────────────────────────────────────────────────────────────────
WHISPER_MODEL=${WHISPER_MODEL}

# ── Code Platforms ───────────────────────────────────────────────────────────
GITHUB_TOKEN=${GITHUB_TOKEN}
GITLAB_TOKEN=${GITLAB_TOKEN}
GITLAB_URL=${GITLAB_URL}
BITBUCKET_USERNAME=${BITBUCKET_USERNAME}
BITBUCKET_APP_PASSWORD=${BITBUCKET_APP_PASSWORD}

# ── LLM Context (advanced) ───────────────────────────────────────────────────
LLM_CONTEXT_SIZE=2048
LLM_CONTEXT_BUFFER=16
LLM_MIN_TOKENS=64
LLM_FALLBACK_MAX_TOKENS=512
LLM_DEFAULT_MAX_TOKENS=128
EOF

  chmod 600 "$ENV_FILE"
  success ".env written to ${BOLD}${ENV_FILE}${RESET} (permissions set to 600)"

  # ── Database setup ────────────────────────────────────────────────────────
  section "Database Setup"

  if prompt_yn "Run database migrations now? (requires PostgreSQL/MongoDB to be running)" "y"; then
    info "Running migrations..."
    "$VENV_DIR/bin/python" scripts/apply_migrations.py && success "Migrations applied" || warn "Migrations failed — you can run them later with: python scripts/apply_migrations.py"

    info "Generating master ID..."
    "$VENV_DIR/bin/python" scripts/gen_master_id.py && success "Master ID generated" || warn "gen_master_id.py failed — run it manually if needed"

    info "Inserting master user..."
    "$VENV_DIR/bin/python" scripts/insert_master.py && success "Master user inserted" || warn "insert_master.py failed — run it manually if needed"
  else
    warn "Skipping database setup. Run these later:"
    echo "    python scripts/apply_migrations.py"
    echo "    python scripts/gen_master_id.py"
    echo "    python scripts/insert_master.py"
  fi

else
  # Non-interactive / --no-onboard: just copy .env.example if no .env exists
  if [[ ! -f "$ENV_FILE" ]]; then
    cp "$INSTALL_DIR/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    warn "Copied .env.example → .env — please edit it before running Curie."
  fi
fi

# ── Verify installation ───────────────────────────────────────────────────────
section "Verifying Installation"

info "Running verify_setup.py..."
"$VENV_DIR/bin/python" scripts/verify_setup.py && success "Verification passed" || warn "Some checks failed — see output above. Curie may still work for the enabled connectors."

# ── Create launcher script ────────────────────────────────────────────────────
section "Creating launcher"

LAUNCHER="$HOME/.local/bin/curie"
mkdir -p "$(dirname "$LAUNCHER")"

cat > "$LAUNCHER" <<LAUNCHER
#!/usr/bin/env bash
# Curie AI launcher — generated by installer
source "${VENV_DIR}/bin/activate"
cd "${INSTALL_DIR}"
exec python main.py "\$@"
LAUNCHER

chmod +x "$LAUNCHER"

# Add ~/.local/bin to PATH hint
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
  warn "~/.local/bin is not in your PATH."
  echo "  Add this to your ~/.zshrc or ~/.bashrc:"
  echo
  echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo
fi

success "Launcher created at ${BOLD}${LAUNCHER}${RESET}"

# ── Optional: systemd / launchd daemon ───────────────────────────────────────
if [[ "$SKIP_ONBOARD" == "0" ]]; then
  section "Background Service (optional)"

  if [[ "$OS" == "linux" ]]; then
    if prompt_yn "Install as a systemd user service (starts on login)?" "n"; then
      SERVICE_NAME="curie-ai"
      SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
      mkdir -p "$(dirname "$SERVICE_FILE")"

      cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=Curie AI Assistant
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${VENV_DIR}/bin/python ${INSTALL_DIR}/main.py --all
Restart=on-failure
RestartSec=5
EnvironmentFile=${ENV_FILE}

[Install]
WantedBy=default.target
SERVICE

      systemctl --user daemon-reload
      systemctl --user enable "$SERVICE_NAME"
      systemctl --user start "$SERVICE_NAME"
      success "systemd user service installed and started"
      info "Manage with: systemctl --user [start|stop|status|restart] curie-ai"
    fi

  elif [[ "$OS" == "macos" ]]; then
    if prompt_yn "Install as a launchd agent (starts on login)?" "n"; then
      PLIST_DIR="$HOME/Library/LaunchAgents"
      PLIST_FILE="${PLIST_DIR}/ai.curie.assistant.plist"
      mkdir -p "$PLIST_DIR"

      cat > "$PLIST_FILE" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.curie.assistant</string>
  <key>ProgramArguments</key>
  <array>
    <string>${VENV_DIR}/bin/python</string>
    <string>${INSTALL_DIR}/main.py</string>
    <string>--all</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${INSTALL_DIR}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${INSTALL_DIR}/logs/curie.log</string>
  <key>StandardErrorPath</key>
  <string>${INSTALL_DIR}/logs/curie.err</string>
</dict>
</plist>
PLIST

      mkdir -p "$INSTALL_DIR/logs"
      launchctl load "$PLIST_FILE"
      success "launchd agent installed and loaded"
      info "Manage with:"
      info "  launchctl unload ~/Library/LaunchAgents/ai.curie.assistant.plist  # stop"
      info "  launchctl load   ~/Library/LaunchAgents/ai.curie.assistant.plist  # start"
    fi
  fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
section "All done! 🦠"

echo
echo -e "  ${BOLD}Curie is installed at:${RESET} ${INSTALL_DIR}"
echo -e "  ${BOLD}Config file:${RESET}          ${ENV_FILE}"
echo -e "  ${BOLD}Models directory:${RESET}      ${INSTALL_DIR}/models/"
echo
echo -e "  ${BOLD}Run Curie:${RESET}"
echo -e "    curie --telegram    # Telegram only"
echo -e "    curie --discord     # Discord only"
echo -e "    curie --api         # REST API only"
echo -e "    curie --all         # All enabled connectors"
echo
echo -e "  ${BOLD}Or use Make (from ${INSTALL_DIR}):${RESET}"
echo -e "    cd ${INSTALL_DIR} && make run-telegram"
echo
if [[ "$SKIP_ONBOARD" == "1" ]]; then
  warn "Don't forget to edit your .env before running:"
  echo "    ${ENV_FILE}"
fi
echo -e "  ${CYAN}Docs:${RESET} https://github.com/yessur3808/curie-ai/tree/main/docs"
echo
