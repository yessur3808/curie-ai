# C.U.R.I.E. - Clever Understanding and Reasoning Intelligent Entity

Curie is an AI assistant that runs **locally** and interacts with users via Telegram.  
It is inspired by conversational assistants like Jarvis from Iron Man, but runs fully on your hardware using state-of-the-art open local language models (no OpenAI account required).

## 🌟 Features

- **Conversational AI** via Telegram
- **Local LLMs**: Runs Meta Llama 3/3.1 or other GGUF models (no cloud needed)
- **Configurable Persona**: Customizable assistant personality via JSON
- **Memory Management**: Stores conversation history and context
- **Database Integration**: PostgreSQL & MongoDB for data persistence
- **Migration System**: Organized database versioning
- **Utility Scripts**: Helper scripts for common operations
- **Docker Support**: Containerized deployment ready

## 🏗️ High-Level Architecture

```
┌────────────┐      ┌────────────┐      ┌─────────────┐
│ Messaging  │─────▶│ Assistant  │─────▶│ Local LLM   │
│ Platforms  │◀──── │ Back-End   │◀──── │ (.gguf, etc)│
└─────┬──────┘      │ (Python)   │      └─────┬───────┘
      │             │             │            │
      │             └────┬────────┘            │
      │                  │                     │
      ▼                  ▼                     ▼
Slack, Telegram,      Memory DB           Local actions
WhatsApp, Voice       (e.g. SQLite,       (filesystem, web,
API                   ChromaDB,           etc)
                      LanceDB)
```



---

## 📁 Project Structure

[Current Directory Structure](./directory_structure.md)


## 🚀 Getting Started

### Prerequisites

- Python 3.8 or higher
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

```env
TELEGRAM_BOT_TOKEN=your_telegram_token
LLM_MODELS=Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf
POSTGRES_URL=postgresql://user:pass@localhost:5432/curie
MONGO_URL=mongodb://localhost:27017/curie
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
- [ ] Voice interface integration
- [ ] WhatsApp connector
- [ ] Discord connector
- [ ] Web dashboard
- [ ] Advanced memory management
- [ ] Multi-user support
- [ ] Plugin system



## 🤝 Contributing
Contributions welcome! Please read our contributing guidelines.

## 📄 License
This project is licensed under the MIT License - see the LICENSE file for details.


---

**Curie: Your Personal AI Assistant, Running Locally!**



