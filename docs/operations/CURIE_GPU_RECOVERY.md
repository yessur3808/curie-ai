# Curie GPU boot and recovery (yi-server)

This host-specific procedure pins known kernels, boot UUID and root volume. Do
not run it on another host. No cloud inference is involved.

1. `sudo bash scripts/install_curie_recovery.sh --prepare-gpu` installs the
   bounded watchdog and arms a one-time kernel 7.0.0-43 GPU boot. It does not
   reboot. The normal default remains kernel 7.0.0-42 with `nomodeset`.
2. Reboot explicitly. Read `/var/lib/curie-recovery/gpu-validation.json` and
   confirm the boot ID matches `/proc/sys/kernel/random/boot_id`. The checks
   require an AMD render device, amdgpu driver, hardware Vulkan and private T3
   access. A failed test triggers a safe reboot after five minutes; an early
   hard hang cannot be recovered by this software timer.
3. Independently exercise Curie's local model worker and verify real GPU use,
   not just a requested offload setting. Check the worker's AMD DRM compute
   accounting under `/proc/<worker-pid>/fdinfo/` and new kernel errors.
4. `bash scripts/promote_curie_gpu.sh --check` is read-only. After model
   verification, `sudo bash scripts/promote_curie_gpu.sh` changes the normal
   default to `curie-gpu-boot`, retaining the safe menu entry. It requires
   fresh, root-owned, successful evidence from the current test boot. Changed
   boot files are backed up under `/var/backups/curie-gpu-promotion-*` and
   restored automatically if configuration generation fails. It does not reboot.
5. A subsequent explicitly authorized reboot starts the normal GPU entry
   without the test flag. Verify Curie, T3, AMD hardware and watchdog status
   again. Host-reboot escalation is deliberately disabled during test boots;
   it becomes eligible on a normal boot only under the bounded recovery policy.

The watchdog restarts only persistently errored Curie main/inference processes,
never deliberately stopped instances. Reboots require persistent inference
failure, failed restart recovery and a recent kernel GPU recovery failure;
they are limited to one per day. No unrestricted sudo or arbitrary code-editing
authority is exposed to the model. A successful boot or GPU test is not proof
of general model intelligence or comprehensive behavioral correctness.

Keep both pinned kernels installed while these entries are in use. The older
independent `yi_gpu_test` setup is not managed by these scripts. Do not rerun
`--prepare-gpu` after promotion unless intentionally starting another test; it
restores the safe default and arms a new test entry.
