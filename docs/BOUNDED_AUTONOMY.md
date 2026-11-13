# Bounded autonomy

Curie's bounded-autonomy service is an opt-in maintenance loop for Curie's own
repository. It increases initiative without granting unattended production
authority.

## Authority boundary

The automatic loop may:

- inspect the configured Curie checkout and its current Git state;
- create a detached temporary Git worktree from the current local commit;
- run a fixed, credential-free regression lane in that worktree;
- ask the configured coding model for a small repair when that lane fails;
- apply the candidate only inside the temporary worktree and rerun the lane;
- save a tested immutable `project_change` preview for the owner;
- deliver one actionable result through the existing proactive-message policy.

It cannot automatically apply a patch to the live checkout. It also has no path
to commit, push, merge, restart services, deploy, change credentials or
dependencies, or edit approval, security, deployment, or autonomy-governance
files. Applying a saved patch uses the existing owner-bound, expiring,
single-use `/approve action …` flow and revalidates every source hash first.

Temporary worktrees are always removed after a cycle. Uncommitted live changes
are observed but never copied into the worktree, overwritten, stashed, reset, or
committed.

## Owner controls

- `/autonomy status`
- `/autonomy on`
- `/autonomy off`
- `/autonomy mode observe`
- `/autonomy mode prepare`
- `/autonomy interval 24` (6–168 hours)
- `/autonomy run`
- `/autonomy report`
- `/autonomy review PREVIEW-ID`

`observe` stops after checks. `prepare` may generate and verify a repair but
still stops at an immutable preview. Proactive delivery remains subject to the
owner's existing quiet hours, daily and weekly caps, topic cooldown, snooze, and
disable controls.

## Defaults and configuration

The owner profile controls opt-in and cadence:

- `bounded_autonomy_enabled` (default `false`)
- `bounded_autonomy_auto_prepare` (default `true` after opt-in)
- `bounded_autonomy_interval_hours` (default `24`, minimum `6`)
- `bounded_autonomy_last_run_at`
- `bounded_autonomy_last_status`
- `bounded_autonomy_pending_report_id`

Optional environment settings:

- `CURIE_AUTONOMY_TEST_TARGETS`: comma-separated relative `tests/*.py` files;
- `CURIE_AUTONOMY_TEST_TIMEOUT_SECONDS`: per-lane timeout, default 240;
- `CURIE_AUTONOMY_MAX_CHANGED_LINES`: maximum candidate size, default 300;
- `CURIE_AUTONOMY_PREVIEW_TTL_MINUTES`: preview lifetime, default 1440.

The default regression lane covers action previews, learned-skill policy, and
proactive/memory controls. Test subprocesses receive a minimal environment, an
isolated home directory, and an isolated SQLite database. Reports are stored as
owner-scoped `bounded_autonomy_report` personal items.

## Operational rule

Do not enable the legacy standalone coding service or `self_updater` as a
substitute for this workflow. Those older paths are not the bounded-autonomy
control plane and are not required by this service.
