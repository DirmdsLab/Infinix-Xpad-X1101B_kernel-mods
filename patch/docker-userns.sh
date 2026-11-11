#!/usr/bin/env bash

# Enable User Namespace for Docker/runc support.

set -euo pipefail

: "${KERNEL_SRC:?Run this file through ./main.sh patch}"

echo "Kernel source: $KERNEL_SRC"

CONFIG_FILE="$KERNEL_SRC/arch/arm64/configs/gki_defconfig"

if grep -q '^CONFIG_USER_NS=y$' "$CONFIG_FILE"; then
    echo "CONFIG_USER_NS is already enabled."
elif grep -q '^# CONFIG_USER_NS is not set$' "$CONFIG_FILE"; then
    sed -i 's/^# CONFIG_USER_NS is not set$/CONFIG_USER_NS=y/' "$CONFIG_FILE"
elif grep -q '^CONFIG_USER_NS=' "$CONFIG_FILE"; then
    sed -i 's/^CONFIG_USER_NS=.*/CONFIG_USER_NS=y/' "$CONFIG_FILE"
else
    printf '\nCONFIG_USER_NS=y\n' >> "$CONFIG_FILE"
fi

echo "CONFIG_USER_NS=y enabled successfully."