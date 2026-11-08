#!/usr/bin/env bash
# Apply the exact four source/configuration changes that produced the uwu build.
set -euo pipefail
: "${REPO_ROOT:?Run ./main.sh patch patch/droidspaces.sh}"
: "${KERNEL_SRC:?}"
expected=7c6bbcca33e1734642601acf07a14140116fa956
[[ $(git -C "$KERNEL_SRC" rev-parse HEAD) == "$expected" ]] \
  || { echo "This baseline patch targets commit $expected only." >&2; exit 1; }
files=(
  "$REPO_ROOT/patch/files/001.GKI-below-6.12-fix_sysvipc_kabi_6_7_8.patch"
  "$REPO_ROOT/patch/files/002.5.10_or_lower_use_android_abi_padding_for_posix_mqueue.patch"
  "$REPO_ROOT/patch/files/003.Enable_Droidspaces_GKI_defconfig.patch"
  "$REPO_ROOT/patch/files/004.Use_uwu_Git_suffix.patch"
)
(cd "$REPO_ROOT/patch/files"; sha256sum -c SHA256SUMS)
if git -C "$KERNEL_SRC" apply --reverse --check "${files[@]}" >/dev/null 2>&1; then
  echo "Baseline patches are already applied."
  exit 0
fi
git -C "$KERNEL_SRC" apply --check "${files[@]}"
git -C "$KERNEL_SRC" apply "${files[@]}"
echo "DROIDSPACES_BASELINE_APPLIED"
