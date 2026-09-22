# Rust device and entity resolution kernel

Curie's provider-neutral device resolver is an ABI3 PyO3 extension. It receives
a bounded, credential-free projection of the canonical inventory and returns
selected indices, confidence, reason, and clarification candidates.

The strict resolution order is:

1. canonical ID;
2. provider ID when the provider is explicit;
3. owner rejection tombstones;
4. exact normalized display names;
5. confirmed, unexpired owner aliases;
6. safe capability and room groups;
7. unique semantic-token matches;
8. strong fuzzy names with an ambiguity margin;
9. recent dialogue-state references;
10. clarification rather than guessing.

Generic wording includes “all lights,” “all of the lights,” “both lamps,”
“kitchen lights,” “lights in the kitchen,” “online lights,” “all switches,”
and “all controllable devices.” Group execution remains bounded to 32 devices
in Python and still verifies every provider result.

## Build and verify

```bash
make device-resolver
make device-resolver-check
```

`CURIE_DEVICE_RESOLVER` accepts `rust`, `auto`, or `python`. Production uses
`rust`; in that mode a missing or incompatible extension fails readiness rather
than silently changing resolution behavior.

Python continues to own inventory discovery, credentials, owner-scoped alias
persistence, authorization, actual device control, verification, and Curie's
natural response wording. The native kernel cannot contact devices or providers.
