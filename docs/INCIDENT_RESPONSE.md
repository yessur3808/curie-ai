# Curie Incident Response

Stop affected connectors or the Curie process first, preserve the private audit
database and rotating logs, record UTC times, and avoid copying secrets or user
message contents into tickets. Audit events contain hashes and redacted
parameters; verify their `previous_hash` chain before relying on them.

## Compromised token

Disable the connector, revoke and rotate the token at its provider, replace it
in the secret store, restart only that connector, and review authentication,
approval, and policy-denial events. Never paste the old or new token into chat.

## Malicious learned skill

Disable the skill version, stop tasks that reference it, preserve its declarative
definition and audit IDs, review its allowed tools and permissions, delete it if
the owner requests deletion, and restore only a reviewed earlier version. Rotate
credentials if the skill could access a connector.

## Database exposure

Remove network access, rotate database and connector credentials, preserve a
forensic snapshot with restricted permissions, identify affected owner IDs and
retention windows, notify affected users according to applicable policy, then
restore into a newly credentialed database. Use owner-scoped export/deletion;
do not export unrelated users.

## Runaway task

Cancel the task, stop its process tree or sandbox, revoke pending approvals,
inspect changed-file evidence without replaying mutations, and restore from the
reviewed diff or backup. Lower concurrency/resource limits before resuming.

## Model-server failure

Stop new inference requests, keep deterministic safety and approval checks
active, fail closed for actions requiring model classification, restart the
managed inference service, run its readiness check, and resume traffic gradually.
Do not bypass action policy to compensate for an unavailable model.

## Recovery closeout

Document scope, UTC timeline, event IDs, mitigations, credential rotations,
restoration checks, user notifications, and follow-up controls. Do not put raw
tokens, personal messages, or unrestricted filesystem paths in the report.

## Quarterly simulation

Exercise one scenario without production secrets: inject repeated invalid
approval tokens, submit a traversal archive fixture, cancel a synthetic durable
task during a read, and run owner-scoped retention cleanup. Confirm no mutation
is replayed, unrelated owners retain their records, and exported evidence is
redacted. Record the date, failed controls, fixes, and test identifiers only.
