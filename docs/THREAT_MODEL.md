# Curie Threat Model

Curie treats connector events, attachments, retrieved pages, transcripts, model
output, and learned content as untrusted data. None of them grants permission or
changes policy. Owner identity, typed tool definitions, deterministic policy,
single-use approval, execution, verification, and audit remain separate steps.

## Trust boundaries

| Surface | Principal threats | Deterministic controls |
|---|---|---|
| Connectors | spoofing, replay, oversized input, cross-owner access | provider identity mapping, message deduplication, size limits, owner-scoped storage |
| Attachments | malware, parser exploits, archive bombs, path traversal, prompt injection | executable denial, ClamAV when present/required, archive limits, bounded subprocess parsers, untrusted-content envelope |
| Retrieval | SSRF, redirect abuse, malicious instructions | public-address validation on every hop, response bounds, markup removal, evidence-only prompts |
| Models | fabricated authority, prompt injection, sensitive output | models cannot grant permissions; typed routing, response boundaries, deterministic approval checks |
| Tools/processes | command injection, path escape, approval replay, partial writes | argument arrays, canonical roots, sandboxing, single-use owner tokens, idempotency, verification |
| Storage/logs | secret exposure, tampering, over-retention, cross-owner access | `0600` local database, redaction, chained audit hashes, owner filters, configurable retention |

## Media policy

Media is processed locally and deleted by connector `finally` blocks. Executable
formats are refused. ZIP-based office documents are checked for traversal,
member count, expanded size, and suspicious compression before parsing. Set
`CURIE_REQUIRE_MALWARE_SCANNER=true` to fail closed unless ClamAV is available.

## Retention and secrets

`/privacy retention` shows effective limits and `/privacy purge` removes expired
records belonging to the requesting owner. Connector secrets belong in an
OS/service secret store or private environment file, never source control.
Database backup encryption and restoration keys must be managed outside the
backup itself; losing the key makes encrypted backups unrecoverable.

Update this model whenever a connector, parser, model backend, tool, database,
or external integration is added.
