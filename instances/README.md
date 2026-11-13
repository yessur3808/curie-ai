# Independent personalities

Every new personality is a new installation. Do not create an `instances/NAME.env`
overlay in another assistant's installation or switch PERSONA_FILE to reuse its memory.

From Curie (or an updated standalone assistant):

```sh
./curie personality add jarvis --persona assets/personality/jarvis.json
```

This creates a sibling `jarvis-ai` directory with independent source, a single persona,
private configuration, empty state, private socket/log paths and separate PM2 process
names. It refuses to overwrite an existing installation. It does **not** copy any
credentials, databases, browser sessions, voice training data, adapters or model assets.
It does not start services. Follow the new installation's SETUP.md to install its own
runtime/model assets and configure its own bot credentials before starting it.

Provisioned assistants reject changes to their bound personality, memory/state paths
and inference socket. Their launcher discards inherited application environment values
before loading their own configuration. Onboarding keeps the current personality;
adding another personality uses the command above.

Curie remains in `curie-ai`; Andreja is already live in sibling `andreja-ai` with her
own runtime and trained voice. The retired `instances/andreja.env` must not be launched.
Existing Curie state is preserved; this change does not reset its memory. Each assistant
has separate application state, but still uses the same host and OS account. Resource
budgets must be checked before enabling additional model processes.
