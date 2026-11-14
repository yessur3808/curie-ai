#!/usr/bin/env bash
# Explicit operator installation. Does not grant Curie unrestricted sudo.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Administrator authentication is required."; exit 1; }
[[ $(hostname) == yi-server ]] || { echo "Wrong host."; exit 1; }
source_root=/home/curlycoffee3808/Desktop/server/bots/curie-ai
[[ ${1:-} == "" || ${1:-} == "--prepare-gpu" ]] || exit 2
backup_dir=$(mktemp -d /var/backups/curie-recovery-XXXXXXXX)
for target in /etc/curie-recovery.json /etc/default/grub /boot/grub/grub.cfg /etc/grub.d/43_curie_gpu_test /etc/default/grub.d/99-curie-safe-boot.cfg; do
    if [[ -e "$target" ]]; then cp -a --parents "$target" "$backup_dir"; fi
done
install -d -m 755 /usr/local/libexec /var/lib/curie-recovery
install -o root -g root -m 755 "$source_root/scripts/curie_recovery.py" /usr/local/libexec/curie-recovery.py
install -o root -g root -m 644 "$source_root/deploy/curie-recovery.json" /etc/curie-recovery.json
install -o root -g root -m 644 "$source_root/deploy/curie-recovery.service" "$source_root/deploy/curie-recovery.timer" /etc/systemd/system/
/usr/bin/python3 -I -m py_compile /usr/local/libexec/curie-recovery.py
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/curie-recovery.service /etc/systemd/system/curie-recovery.timer
systemctl enable --now curie-recovery.timer
systemctl start curie-recovery.service
runuser -u curlycoffee3808 -- env XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus systemctl --user disable --now curie-self-recovery.timer
if [[ ${1:-} == "--prepare-gpu" ]]; then
    [[ $(findmnt -no UUID /boot) == 9462745e-b74f-4f1f-85a6-4ed8acde6ecf ]]
    for kernel in 7.0.0-42-generic 7.0.0-43-generic; do
        test -s "/boot/vmlinuz-$kernel"
        test -s "/boot/initrd.img-$kernel"
        test -d "/lib/modules/$kernel"
    done
    install -o root -g root -m 755 "$source_root/scripts/curie_gpu_verify.py" /usr/local/libexec/curie-gpu-verify.py
    install -o root -g root -m 755 "$source_root/deploy/43_curie_gpu_test" /etc/grub.d/43_curie_gpu_test
    install -o root -g root -m 644 "$source_root/deploy/99-curie-safe-boot.cfg" /etc/default/grub.d/99-curie-safe-boot.cfg
    for name in curie-gpu-check.service curie-gpu-check.timer curie-gpu-fallback.service curie-gpu-fallback.timer; do
        install -o root -g root -m 644 "$source_root/deploy/$name" "/etc/systemd/system/$name"
    done
    /usr/bin/python3 -I -m py_compile /usr/local/libexec/curie-gpu-verify.py
    update-grub
    grub-script-check /boot/grub/grub.cfg
    systemctl daemon-reload
    systemd-analyze verify /etc/systemd/system/curie-gpu-check.service /etc/systemd/system/curie-gpu-fallback.service
    systemctl enable curie-gpu-check.timer curie-gpu-fallback.timer
    grub-reboot curie-gpu-test
    grub-editenv /boot/grub/grubenv list
    echo "One-time GPU test prepared. Normal default: existing kernel with nomodeset."
    echo "A five-minute fallback requires the OS to reach systemd; it cannot recover a hard firmware hang."
fi
echo "Backup directory: $backup_dir"
echo "No reboot has been performed."

