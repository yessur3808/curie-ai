# Curie lifecycle diagnostics

Direct startup through `main.py` records metadata-only events in
`log/lifecycle.jsonl`: UTC time, PID, parent PID, monotonic uptime, startup,
process exit, and received SIGTERM/SIGINT. No environment, credentials,
message text, or command arguments are recorded. Files are owner-only and
rotate at 1 MB with three backups. Tests use isolated subprocesses.

Correlate these events with `~/.pm2/pm2.log`, PM2 status, and the service
stdout/stderr logs. A signal event identifies the signal, not its sender.
Default termination behavior and existing Python signal handlers are retained.
SIGKILL, power loss, and native crashes cannot reliably write an exit event;
a startup without a later exit/signal event is not proof of any specific cause.
Check host journal/OOM evidence separately in that case.

On 2026-11-05 the bot and both dashboards were absent from PM2. The PM2 log
recorded a daemon shutdown by signal at 2026-11-04 10:16:18 UTC and a new
daemon with no apps at 10:16:32. The initiator was not established. Restoring
Curie runtime services does not enable T3's separately paused reliability
loop or daily memory audit.

During the Nov5 restoration, a synthetic model request reproduced a shutdown:
the kernel reported a memory-cgroup OOM killing the inference PID under
`curie-dashboard.service`, whose MemoryMax is 384 MB. That Observatory unit's
OOMPolicy=stop then shut down the shared PM2 process tree. The bot lifecycle
log captured SIGTERM at 04:18:18 UTC. This establishes the new incident's
mechanism, not necessarily the initiator of the Nov4 incident.

The existing `curie-pm2.service` was active/exited but owned no processes.
Restore via `systemctl --user restart curie-pm2.service` after verifying no
unrelated PM2 apps would be affected. Its existing profile-sync drop-in runs
`scripts/reload_curie_pm2.sh`; its OOMPolicy is continue and it has no service
MemoryMax. Verify each Curie PID's `/proc/PID/cgroup` ends in
`curie-pm2.service`, not `curie-dashboard.service`. Avoid starting the PM2
daemon from the Observatory's constrained service. No unit files were changed.
