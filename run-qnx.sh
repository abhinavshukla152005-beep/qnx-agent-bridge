#!/usr/bin/env bash
# Boot the QNX 8.0 QSTI image headless on a Windows host.
#
# The official QSTI launch command assumes a Linux host (KVM, bridged net,
# /dev/urandom). This adaptation keeps the same virtual hardware so the same
# image boots, but uses WHPX/TCG, user-mode networking and a builtin RNG.
#
# Usage: ./run-qnx.sh [image-dir]
#   image-dir defaults to $QNX_QEMU_DIR, then ~/qnx800/images/qemu/qemu
set -euo pipefail

QEMU_BIN="${QEMU_BIN:-/c/Program Files/qemu/qemu-system-x86_64.exe}"
IMGDIR="${1:-${QNX_QEMU_DIR:-$HOME/qnx800/images/qemu/qemu}}"
SSH_PORT="${SSH_PORT:-2222}"

if [[ ! -x "$QEMU_BIN" ]]; then
    echo "qemu-system-x86_64 not found at: $QEMU_BIN" >&2
    echo "set QEMU_BIN to the full path of qemu-system-x86_64.exe" >&2
    exit 1
fi

for f in ifs.bin disk-qemu.vmdk; do
    if [[ ! -f "$IMGDIR/output/$f" ]]; then
        echo "missing $IMGDIR/output/$f" >&2
        echo "install com.qnx.qnx800.quickstart.qemu in QNX Software Center," >&2
        echo "then run unpack_qemu_image.sh and pass the unpacked qemu/ dir here." >&2
        exit 1
    fi
done

ACCEL="tcg,thread=multi"
CPU="max"
# WHPX exits immediately when the Windows Hypervisor Platform feature is off,
# and blocks when it works. 124 is the timeout exit code, i.e. it started.
rc=0
timeout 5 "$QEMU_BIN" -accel whpx -M q35 -m 128 -display none -S \
    -monitor none -serial null >/dev/null 2>&1 || rc=$?
if [[ $rc -eq 124 ]]; then
    ACCEL="whpx"
    CPU="host,host-phys-bits-limit=39"
fi

echo "booting QNX from $IMGDIR/output (accel=$ACCEL, ssh 127.0.0.1:$SSH_PORT)" >&2

cd "$IMGDIR"
exec "$QEMU_BIN" \
    -accel "$ACCEL" \
    -cpu "$CPU" \
    -smp 4 \
    -m 4G \
    -drive file=output/disk-qemu.vmdk,if=ide,id=drv0 \
    -kernel output/ifs.bin \
    -netdev user,id=net0,hostfwd=tcp::"$SSH_PORT"-:22 \
    -device virtio-net-pci,netdev=net0 \
    -object rng-builtin,id=rng0 \
    -device virtio-rng-pci,rng=rng0 \
    -vga none \
    -nographic \
    -serial mon:stdio
