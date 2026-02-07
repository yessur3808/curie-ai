# C.U.R.I.E. - Clever Understanding and Reasoning Intelligent Entity

Curie is an AI assistant that runs **locally** and interacts with users via **multiple platforms**.  
It is inspired by conversational assistants like Jarvis from Iron Man, but runs fully on your hardware using state-of-the-art open local language models (no OpenAI account required).

## 🌟 Features

- **Multi-Platform Support**: Telegram, Discord, WhatsApp, and RESTful/WebSocket API
- **Voice Interface**: Accent-aware speech recognition and persona-based text-to-speech
- **Conversational AI** with context and memory
- **Local LLMs**: Runs Meta Llama 3/3.1 or other GGUF models (no cloud needed)
- **Configurable Persona**: Customizable assistant personality via JSON with voice settings
- **Memory Management**: Stores conversation history and context
- **Database Integration**: PostgreSQL & MongoDB for data persistence
- **Migration System**: Organized database versioning
- **Utility Scripts**: Helper scripts for common operations
- **Docker Support**: Containerized deployment ready

## 📱 Supported Platforms

| Platform | Text | Voice | Status |
|----------|------|-------|--------|
| **Telegram** | ✅ | ✅ | Stable |
| **Discord** | ✅ | ✅ | Stable |
| **WhatsApp** | ✅ | ✅ | Beta |
| **REST API** | ✅ | ✅ | Stable |
| **WebSocket** | ✅ | 🔜 | Stable |

## 🎙️ Voice Features

- **Speech-to-Text**: Powered by OpenAI Whisper with automatic language detection
- **Text-to-Speech**: Google TTS with multi-accent support
- **Accent Recognition**: Adapts to American, British, Indian, Australian, and more
- **Persona-Based Voice**: Configure accent, language, and speaking style per persona

See [Multi-Platform Guide](docs/MULTI_PLATFORM_GUIDE.md) for detailed voice configuration.

## 🏗️ High-Level Architecture

```
┌────────────────┐      ┌────────────┐      ┌─────────────┐
│ Messaging      │─────▶│ Assistant  │─────▶│ Local LLM   │
│ Platforms      │◀──── │ Back-End   │◀──── │ (.gguf, etc)│
│ • Telegram     │      │ (Python)   │      └─────┬───────┘
│ • Discord      │      │             │            │
│ • WhatsApp     │      └────┬────────┘            │
│ • API/WS       │           │                     │
└───────┬────────┘           │                     │
        │                    ▼                     ▼
        │              ┌──────────┐          ┌──────────┐
        │              │ Memory   │          │  Voice   │
        │              │ (Conv +  │          │Processing│
        └──────────────│  User    │          │(STT/TTS) │
                       │ Profile) │          └──────────┘
                       └──────────┘
```



---

## 📁 Project Structure

[Current Directory Structure](./directory_structure.md)


## 🚀 Getting Started

### Prerequisites

- Python 3.10 or higher
- PostgreSQL
- MongoDB
- Docker (optional but preferred)
- `python-telegram-bot` (v20+)
- `llama-cpp-python`
- `python-dotenv`
- At least one GGUF language model (see below)

---

## Setup

### 1. **Clone the repository**
```sh
git clone https://github.com/yessur3808/curie-ai.git
cd curie-ai
```


### 2. **Install dependencies**

```sh
pip install -r requirements.txt
```


### 3. **Download a GGUF LLM model**

- Recommended: Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf
- Place the .gguf file in the models/ directory.


### 4. **Create a `.env` file in the project root**

See the [Environment Variables](#-environment-variables) section below for a complete list of available configuration options.

Minimal example:

```env
TELEGRAM_BOT_TOKEN=your_telegram_token
LLM_MODELS=Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf
MASTER_USER_ID=123456789
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=assistant_db
POSTGRES_USER=your_pg_user
POSTGRES_PASSWORD=your_pg_password
MONGODB_URI=mongodb://localhost:27017/
MONGODB_DB=assistant_db
```
You can list multiple GGUF model files (comma-separated) if you want to support switching later.

### 5. Set up databases

```sh
# Apply database migrations
python scripts/apply_migrations.py

# Generate master ID
python scripts/gen_master_id.py

# Insert master user
python scripts/insert_master.py
```

### 6. **Set up your persona (optional)**

Edit assets/persona.json to customize the assistant’s name, greeting, and style.


## Running the Bot

```sh
python main.py
```

or

```sh
python3 main.py
```


Using Docker:

```sh

docker-compose up
```

## 🔧 Environment Variables

Curie uses environment variables for configuration. Copy `.env.example` to `.env` and configure the following variables:

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token from BotFather | `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11` |
| `DISCORD_BOT_TOKEN` | Your Discord bot token from Developer Portal | `your_discord_bot_token_here` |
| `WHATSAPP_SESSION_PATH` | Path to store WhatsApp session data | `./whatsapp_session` |
| `LLM_MODELS` | Comma-separated list of GGUF model filenames | `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` |
| `MASTER_USER_ID` | User ID with admin privileges | `123456789` |
| `POSTGRES_HOST` | PostgreSQL database host | `localhost` |
| `POSTGRES_PORT` | PostgreSQL database port | `5432` |
| `POSTGRES_DB` | PostgreSQL database name | `assistant_db` |
| `POSTGRES_USER` | PostgreSQL username | `your_pg_user` |
| `POSTGRES_PASSWORD` | PostgreSQL password | `your_pg_password` |
| `MONGODB_URI` | MongoDB connection URI | `mongodb://localhost:27017/` |
| `MONGODB_DB` | MongoDB database name | `assistant_db` |

### Connector Flags

| Variable | Description | Default |
|----------|-------------|---------|
| `RUN_TELEGRAM` | Enable Telegram connector | `true` |
| `RUN_DISCORD` | Enable Discord connector | `false` |
| `RUN_WHATSAPP` | Enable WhatsApp connector | `false` |
| `RUN_API` | Enable REST API connector | `true` |

### Voice Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `WHISPER_MODEL` | Whisper model size (tiny, base, small, medium, large) | `base` |

### Optional Variables

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `CODING_MODEL_NAME` | GGUF model for coding tasks | None | `codellama-34b-instruct.Q4_K_M.gguf` |
| `ASSISTANT_NAME` | Display name of your AI assistant (used for speaker tag removal) | None | `jarvis` |
| `PERSONA_FILE` | Persona configuration filename (set to "all" for multi-persona mode) | None | `personality.json` |
| `GITHUB_TOKEN` | GitHub personal access token for code operations | None | `ghp_***` |
| `MAIN_REPO` | Main repository URL for code operations | None | `https://github.com/user/repo` |
| `MAIN_REVIEWER` | Default code reviewer username | None | `MainCoder` |
| `RUN_TELEGRAM` | Enable/disable Telegram bot | `true` | `true` or `false` |
| `RUN_API` | Enable/disable API server | `true` | `true` or `false` |
| `RUN_CODER` | Enable/disable coding agent | `false` | `true` or `false` |
| `PROJECTS_ROOT` | Root directory for projects | None | `/var/projects` |

### LLM Context Window Configuration (Advanced)

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_CONTEXT_SIZE` | Total context window size | `2048` |
| `LLM_CONTEXT_BUFFER` | Buffer reserved for system tokens | `16` |
| `LLM_MIN_TOKENS` | Minimum tokens required for a response | `64` |
| `LLM_FALLBACK_MAX_TOKENS` | Fallback max_tokens if tokenization fails | `512` |
| `LLM_DEFAULT_MAX_TOKENS` | Default max_tokens for ask_llm() | `128` |

### Info Search Task Configuration (Advanced)

| Variable | Description | Default |
|----------|-------------|---------|
| `INFO_SEARCH_TEMPERATURE` | Temperature for info search LLM calls (lower = more deterministic) | `0.2` |
| `INFO_SEARCH_MAX_TOKENS` | Maximum tokens for info search responses | `512` |
| `INFO_SEARCH_MAX_SOURCES` | Maximum number of sources to process (prevents context overflow) | `3` |
| `INFO_SEARCH_MAX_SNIPPET_CHARS` | Maximum characters per snippet (~100 tokens) | `400` |

## 🛠️ Development Phases
### Phase 1: Core Functionality ✅
- [x] Telegram integration
- [x] Local LLM support
- [x] Basic conversation handling


### Phase 2: Memory & Storage ✅
- [x] PostgreSQL integration
- [x] MongoDB for conversation history
- [x] Migration system


### Phase 3: Enhanced Features 🚧
- [ ] Multi-platform support
- [ ] Advanced context management
- [ ] Web interface



## 📝 Notes
- All LLM inference runs locally
- Recommended: 8GB+ RAM for optimal performance
- Supports multiple GGUF models
- Database backups recommended


## 🗺️ Roadmap

### Completed Features
- [x] Voice interface integration (✅ Completed)
- [x] WhatsApp connector (✅ Completed)
- [x] Discord connector (✅ Completed)
- [x] Multi-platform support (✅ Completed)
- [x] Real-time date/time access (✅ Completed)
- [x] Proactive messaging service (✅ Completed)

### Core Platform Improvements
- [ ] Web dashboard / UI
- [ ] Advanced memory management
- [ ] Enhanced multi-user support
- [ ] Plugin system

### Feature Enhancements (See [FEATURE_ROADMAP.md](docs/FEATURE_ROADMAP.md))

#### Priority 1: High Impact, Low Complexity
- [ ] **Currency & Unit Conversions**
  - Live currency exchange rates
  - Comprehensive unit conversions (length, mass, volume, temperature, speed, area, energy, power, pressure, force, angles, time, fuel)
  - Cooking and measurement conversions

- [ ] **Enhanced News Analysis**
  - News aggregation from multiple sources
  - Sentiment analysis
  - Topic extraction and trending topics
  - Category-specific news (business, tech, science, health, sports)

- [ ] **Basic Financial Data** (View-only)
  - Cryptocurrency prices and market data
  - Stock market quotes and indices
  - Forex exchange rates
  - Market status and trends

#### Priority 2: Medium Complexity
- [ ] **Navigation & Traffic**
  - Real-time traffic information
  - Route planning and directions
  - Multi-modal routing (walk, bike, transit, drive)
  - Travel time estimates with traffic
  - Alternative route suggestions

- [ ] **Email Integration**
  - Send emails via SMTP or API (SendGrid, Mailgun)
  - Template management
  - Scheduled email sending
  - Attachment support
  - Contact management

- [ ] **Nutrition & Wellness Information**
  - Nutrition database lookup
  - Calorie and macro tracking
  - BMI and health calculators
  - General wellness tips
  - Exercise recommendations

#### Priority 3: Complex Features
- [ ] **Legal & Tax Information** (Educational only)
  - US tax code reference
  - Tax brackets and deduction information
  - Basic legal definitions
  - ⚠️ With strong disclaimers - not professional advice

- [ ] **Advanced Financial Analysis** (Educational only)
  - Technical indicators (RSI, MACD, Moving Averages)
  - Portfolio tracking (paper/virtual)
  - Strategy backtesting (educational)
  - Market trend analysis

#### Future Considerations (Require Legal Framework)
- ⚠️ Real trading execution (requires broker partnership & licensing)
- ⚠️ Medical diagnosis/treatment advice (liability concerns - not planned)
- ⚠️ Specific legal advice (unauthorized practice - not planned)
- ⚠️ Tax filing services (requires professional review)

**Note**: For detailed implementation plans, API requirements, and enhancement ideas, see [docs/FEATURE_ROADMAP.md](docs/FEATURE_ROADMAP.md)



## 🤝 Contributing
Contributions welcome! Please read our contributing guidelines.

## 📄 License
This project is licensed under the MIT License - see the LICENSE file for details.


---

**Curie: Your Personal AI Assistant, Running Locally!**



