# Named assistant instances

Create one ignored `NAME.env` file per bot. Each file overrides the shared
`.env` without duplicating model settings or other common configuration.

```dotenv
PERSONA_FILE=curie.json
TELEGRAM_BOT_TOKEN=replace_me
CURIE_LOCAL_MEMORY_DB=.curie_NAME.sqlite3
```

Run up to three named processes with isolated PID, state, log, token, persona,
and SQLite memory files:

```bash
./curie start --instance curie --telegram
./curie start --instance andreja --telegram
./curie start --instance third --telegram

./curie status --instance curie
./curie logs --instance curie
./curie stop --instance curie
```

Do not commit instance environment files. A separate Telegram bot token is
required for every simultaneously running instance.
