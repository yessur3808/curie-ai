#!/usr/bin/env bash
# Operator-only promotion after actual local inference has also been verified.
# --check is read-only; neither mode reboots the machine.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
source_root=/home/curlycoffee3808/Desktop/server/bots/curie-ai
[[ $(hostname) == yi-server ]] || { echo "Wrong host."; exit 1; }
[[ $# -eq 0 || ( $# -eq 1 && $1 == --check ) ]] || exit 2
if [[ ${1:-} != --check ]]; then
    [[ $EUID -eq 0 ]] || { echo "Administrator authentication is required."; exit 1; }
    # Refresh evidence with the installed, root-owned verifier before promotion.
    systemctl start curie-gpu-check.service
fi
/usr/bin/python3 -I "$source_root/scripts/curie_gpu_readiness.py"
[[ $(findmnt -no UUID /boot) == 9462745e-b74f-4f1f-85a6-4ed8acde6ecf ]]
for kernel in 7.0.0-42-generic 7.0.0-43-generic; do
    test -s "/boot/vmlinuz-$kernel"
    test -s "/boot/initrd.img-$kernel"
    test -d "/lib/modules/$kernel"
done
grub-script-check "$source_root/deploy/43_curie_gpu_test"
if [[ ${1:-} == --check ]]; then
    echo "Promotion preflight passed. No changes made."
    exit 0
fi
backup_dir=$(mktemp -d /var/backups/curie-gpu-promotion-XXXXXXXX)
targets=(/etc/grub.d/43_curie_gpu_test /etc/default/grub.d/99-curie-safe-boot.cfg /boot/grub/grub.cfg /boot/grub/grubenv)
for target in "${targets[@]}"; do
    test -f "$target"
    cp -a --parents "$target" "$backup_dir"
done
rollback() {
    local result=$?
    trap - EXIT
    if [[ $result -ne 0 ]]; then
        for target in "${targets[@]}"; do
            cp -a "$backup_dir$target" "$target"
        done
        echo "Promotion failed; previous boot files restored from $backup_dir" >&2
    fi
    exit "$result"
}
trap rollback EXIT
install -o root -g root -m 755 "$source_root/deploy/43_curie_gpu_test" /etc/grub.d/43_curie_gpu_test
# Replace the existing override, avoiding competing GRUB_DEFAULT snippets.
install -o root -g root -m 644 "$source_root/deploy/99-curie-gpu-boot.cfg" /etc/default/grub.d/99-curie-safe-boot.cfg
update-grub
grub-script-check /boot/grub/grub.cfg
grep -Fq 'set default="curie-gpu-boot"' /boot/grub/grub.cfg
grub-editenv /boot/grub/grubenv unset next_entry
echo "GPU boot is now the normal default; the safe framebuffer entry is retained."
echo "Backup directory: $backup_dir"
echo "No reboot performed. Recovery reboot escalation stays disabled until a normal boot."
