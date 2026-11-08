#!/usr/bin/env bash
# Copy this file, then add your own edits to the kernel repository.
set -euo pipefail
: "${KERNEL_SRC:?Run this file through ./main.sh patch}"
echo "Kernel source: $KERNEL_SRC"
# Example editing target:
# "$KERNEL_SRC/arch/arm64/configs/gki_defconfig"
# Write or apply only the intended source/configuration changes here.
