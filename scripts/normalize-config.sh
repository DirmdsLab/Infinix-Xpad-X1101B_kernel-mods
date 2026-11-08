#!/usr/bin/env bash
# Normalize the current defconfig without changing its effective configuration.
set -euo pipefail
: "${REPO_ROOT:?Run through main.sh}"
: "${SRC_DIR:?}"
: "${KERNEL_SRC:?}"
source "$REPO_ROOT/kernel.conf"
output="$REPO_ROOT/build/config"
export PATH="$SRC_DIR/$CLANG_BIN:$PATH"
unset KCONFIG_CONFIG KBUILD_OUTPUT LOCALVERSION
target="$KERNEL_SRC/arch/arm64/configs/gki_defconfig"
make -C "$KERNEL_SRC" O="$output" ARCH=arm64 LLVM=1 LLVM_IAS=1 \
  CROSS_COMPILE=aarch64-linux-gnu- gki_defconfig savedefconfig
cp "$output/.config" "$output/before-normalize.config"
cp "$output/defconfig" "$target"
make -C "$KERNEL_SRC" O="$output" ARCH=arm64 LLVM=1 LLVM_IAS=1 \
  CROSS_COMPILE=aarch64-linux-gnu- gki_defconfig savedefconfig
cmp "$output/before-normalize.config" "$output/.config"
cmp "$output/defconfig" "$target"
echo "CONFIG_NORMALIZED"
