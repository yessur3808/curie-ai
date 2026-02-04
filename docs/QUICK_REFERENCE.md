# Quick Reference: Curie AI Optimized Architecture

## For Users: New Commands

### Remember a Fact
```
/remember hobby reading
/remember favorite_food sushi
/remember birth_month March
```
Bot will store these facts and use them in future conversations.

### Clear Your Memory
```
/clear_memory
```
Clears your conversation history.

### Say You're Busy
```
/busy
```
Bot will reduce proactive messages until you:

### Resume Chatting
```
/resume
```

---

## For Developers: Using ChatWorkflow

### Initialize
```python
from agent.chat_workflow import ChatWorkflow
from utils.persona import load_persona

# Load persona
persona = load_persona(filename="curie.json")

# Create workflow
workflow = ChatWorkflow(
    persona=persona,
    max_history=5,
    enable_small_talk=False
)
```

### Process Messages
```python
import asyncio

# Normalize incoming message
normalized_input = {
    'platform': 'my_platform',           # e.g., 'telegram', 'api', 'voice'
    'external_user_id': 'user_123',      # Platform-specific user ID
    'external_chat_id': 'chat_456',      # Platform-specific chat ID
    'message_id': 'msg_789',             # Platform-specific message ID
    'text': 'Hello, how are you?',       # User message
    'timestamp': datetime.utcnow()       # Message timestamp
}

# Process through workflow
result = await workflow.process_message(normalized_input)

# Use result
print(result['text'])                    # Response text
print(result['processing_time_ms'])      # How long it took
print(result['model_used'])              # Which model generated it
```

### Get Cache Statistics
```python
stats = workflow.get_cache_stats()
print(stats)
# {
#   'prompt_cache': {'hits': 42, 'misses': 58, 'hit_rate_percent': 42.0, 'size': 35},
#   'dedupe_cache_size': 128,
#   'current_persona': 'Curie'
# }
```

### Change Persona
```python
workflow.change_persona('andreja')  # Load different persona
# Returns True if successful, False if persona not found
```

---

## For Connector Developers: Adding New Connectors

### 1. Receive Event from Platform
```python
# Your platform (e.g., Discord, WhatsApp, Voice)
def on_message_received(event):
    platform = 'discord'
    user_id = event.author.id
    chat_id = event.channel.id
    message_id = event.id
    text = event.content
```

### 2. Normalize to Standard Format
```python
from datetime import datetime
from agent.chat_workflow import ChatWorkflow

normalized_input = {
    'platform': platform,
    'external_user_id': str(user_id),
    'external_chat_id': str(chat_id),
    'message_id': str(message_id),
    'text': text,
    'timestamp': datetime.utcnow()
}
```

### 3. Process Through Workflow
```python
# Workflow is passed from main.py or initialized here
workflow = ChatWorkflow(persona=persona)

result = await workflow.process_message(normalized_input)
```

### 4. Send Response
```python
response_text = result['text']
# Send back to user via your platform
channel.send(response_text)  # Discord example
```

### 5. Register in main.py
```python
# In main.py, add connector startup
def run_discord(workflow):
    from connectors.discord_connector import start_discord_bot
    start_discord_bot(workflow)

# In main():
if run_discord_flag:
    t = threading.Thread(target=run_discord, args=(workflow,), daemon=True)
    threads.append(t)
    t.start()
```

---

## LLM Response Caching

### What Gets Cached?
- Same prompt + temperature + max_tokens → cached response

### Cache Statistics
```python
from llm.manager import ResponseCache

stats = ResponseCache.stats()
print(stats)
# {
#   'hits': 156,
#   'misses': 244,
#   'hit_rate_percent': 39.0,
#   'size': 47
# }
```

### Disable Cache (if needed)
```python
# In llm/manager.py, comment out cache check:
# cached_response = ResponseCache.get(prompt, temperature, max_tokens)
```

---

## Database Queries

### Batch Loading (Async)
```python
# ChatWorkflow automatically batch-loads:
# 1. User profile (from MongoDB)
# 2. Conversation history (from PostgreSQL)
# Both run in parallel, reducing latency by ~50%
```

### Explicit Profile Query
```python
from memory import UserManager

profile = UserManager.get_user_profile(internal_id)
# Returns: {'hobby': 'reading', 'favorite_food': 'sushi', ...}
```

### Explicit History Query
```python
from memory import ConversationManager

history = ConversationManager.load_recent_conversation(
    internal_id, 
    limit=10
)
# Returns: [('user', 'msg1'), ('assistant', 'response1'), ...]
```

---

## Performance Tuning

### Reduce LLM Call Count
- ✅ Already at 1 call per message (was 2-3)
- ✅ No automatic small talk
- ✅ No automatic fact extraction

### Reduce DB Query Latency
- ✅ Batch queries in parallel (async)
- ✅ Consider connection pooling for high concurrency
- Consider read replicas for PostgreSQL if needed

### Improve Cache Hit Rate
- Monitor `ResponseCache.stats()`
- If hit rate < 20%, consider increasing `_response_cache_ttl`
- If memory is tight, decrease `_response_cache_max_size`

### Monitor Response Time
```bash
# Check recent logs
pm2 logs curie-assistant --lines 20

# Look for: "processing_time_ms" in responses
```

---

## Troubleshooting

### Bot Not Responding
1. Check process is running: `pm2 status`
2. Check logs: `pm2 logs curie-assistant`
3. Verify model loaded: Look for "Successfully loaded model" in logs
4. Test API health: `curl http://localhost:8000/health`

### Slow Responses
1. Check cache hit rate: `curl http://localhost:8000/health`
2. If hit rate < 30%, responses are likely uncached (expected on first request)
3. If consistently slow, check model resource usage: `nvidia-smi` or `top`
4. Consider enabling response caching if disabled

### Memory Issues
1. Check available RAM: `free -h`
2. Monitor cache size: `ResponseCache.stats()`
3. Reduce `_response_cache_max_size` in `llm/manager.py`
4. Check model size: `ls -lh models/`

### Duplicate Responses
1. Deduplication should prevent this
2. Check dedupe cache size: `workflow.get_cache_stats()['dedupe_cache_size']`
3. If > 4000, TTL might be too long - verify TTL setting

---

## Configuration Reference

### Environment Variables (in .env)

```dotenv
# LLM Configuration
LLM_MODELS=Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf,qwen2.5-3b-instruct-Q4_K_M.gguf,codellama-34b-instruct.Q4_K_M.gguf
LLM_PROVIDER=llama.cpp
LLM_THREADS=18
LLM_CONTEXT_SIZE=2048
LLM_TEMPERATURE=0.7
LLM_MAX_NEW_TOKENS_DEFAULT=200

# Connector Flags
RUN_TELEGRAM=true
RUN_API=true
RUN_CODER=false

# Persona Selection
ASSISTANT_NAME=curie
PERSONA_FILE=curie.json

# Database
POSTGRES_HOST=192.168.50.183
POSTGRES_PORT=5432
MONGO_URI=mongodb://192.168.50.183:27017/

# Telegram
TELEGRAM_BOT_TOKEN=YOUR_TOKEN_HERE
```

### Persona JSON Fields

```json
{
  "name": "Curie",
  "description": "Short description",
  "system_prompt": "Core behavior instructions",
  "greeting": "Initial greeting message",
  "french_phrases": ["Oui!", "Mon ami", ...],
  "response_style": {
    "brevity": "concise|normal|verbose",
    "tone": "warm|professional|playful",
    "humor": "playful|subtle|none",
    "formality": "casual|neutral|formal",
    "clarity": "prioritized|balanced"
  },
  "small_talk_topics": ["hobbies", "interests", ...],
  "constraints": ["never make up facts", ...],
  "personality": {
    "traits": ["trait1", "trait2", ...]
  }
}
```

---

## Monitoring Dashboard Ideas

```python
# Create simple monitoring endpoint
@app.get("/metrics")
async def metrics():
    return {
        "workflow": {
            "prompt_cache": ResponseCache.stats(),
            "dedupe_cache": _workflow.dedupe_cache.stats() if hasattr(_workflow, 'dedupe_cache') else {},
            "current_persona": _workflow.persona.get('name')
        },
        "system": {
            "uptime": get_uptime(),
            "requests_total": request_count,
            "avg_response_time_ms": avg_time
        }
    }
```

---

## Next Steps

1. **Monitor performance**: Track response times, cache hit rates
2. **Gather feedback**: Ask users if responses feel faster/better
3. **Adjust parameters**: Fine-tune cache sizes, LLM parameters
4. **Add connectors**: Voice, Discord, WhatsApp using standard interface
5. **Scale up**: Consider load balancing for multiple instances

---

**Version**: 2.0.0 (Optimized)
**Last Updated**: February 5, 2026
**Status**: Production Ready ✅
