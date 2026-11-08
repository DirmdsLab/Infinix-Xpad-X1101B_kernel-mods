#!/usr/bin/env bash
# One-folder kernel experiment workflow. Run inside the Ubuntu build container.
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SRC_DIR="$ROOT/src"
BUILD_DIR="$ROOT/build"
BACKUP_DIR="$ROOT/build.bak"
DEVICE_DIR="$ROOT/device"
TOOLS_DIR="$ROOT/tools"
LOG_DIR="$ROOT/log"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage:
  ./main.sh setup                 Install dependencies and prepare clean source
  ./main.sh reset                 Reset kernel source and rotate build to build.bak
  ./main.sh patch patch/file.sh   Run an experiment patch script
  ./main.sh build                 Compile, repack build/build.img, and check it
  ./main.sh check                 Recheck the current build independently

Edit kernel.conf to select a kernel commit and build settings.
setup also resets matching source and rotates the previous build.
EOF
}

command=${1:-help}
case "$command" in
  setup|reset|patch|build|check) ;;
  help|-h|--help) usage; exit 0 ;;
  *) usage >&2; exit 1 ;;
esac

# The outer process records the true command and tee statuses separately.
if [[ ${XPAD_LOGGED:-0} != 1 ]]; then
  mkdir -p "$LOG_DIR"
  set +e
  XPAD_LOGGED=1 bash "$ROOT/main.sh" "$@" 2>&1 | tee "$LOG_DIR/$command.log"
  statuses=("${PIPESTATUS[@]}")
  printf 'COMMAND_EXIT=%s LOG_EXIT=%s\n' "${statuses[0]}" "${statuses[1]}"
  [[ ${statuses[0]} == 0 || ${statuses[0]} == 2 ]] || exit "${statuses[0]}"
  [[ ${statuses[1]} == 0 ]] || exit "${statuses[1]}"
  exit "${statuses[0]}"
fi

cd "$ROOT"
source "$ROOT/kernel.conf"
[[ $KERNEL_COMMIT =~ ^[0-9a-f]{40}$ ]] || die "KERNEL_COMMIT must be a full 40-character commit hash"
[[ $JOBS =~ ^[1-9][0-9]*$ ]] || die "JOBS must be positive"
[[ $BOOT_IMAGE_SIZE =~ ^[1-9][0-9]*$ ]] || die "BOOT_IMAGE_SIZE must be positive"
[[ $KERNEL_SUBDIR != /* && $KERNEL_SUBDIR != . && $KERNEL_SUBDIR != .. && $KERNEL_SUBDIR != ../* && $KERNEL_SUBDIR != */../* && $KERNEL_SUBDIR != */.. ]] \
  || die "KERNEL_SUBDIR must name a directory inside src"
KERNEL_SRC="$SRC_DIR/$KERNEL_SUBDIR"
export REPO_ROOT="$ROOT" SRC_DIR KERNEL_SRC
mkdir -p "$LOG_DIR"

assert_kernel_repo() {
  [[ -e "$KERNEL_SRC/.git" ]] || die "Kernel Git repository missing: $KERNEL_SRC; run setup"
  local expected actual source_root
  expected=$(readlink -f "$KERNEL_SRC")
  source_root=$(readlink -f "$SRC_DIR")
  [[ $expected == "$source_root/"* ]] || die "Kernel source escapes src/"
  actual=$(git -C "$KERNEL_SRC" rev-parse --show-toplevel)
  [[ $(readlink -f "$actual") == "$expected" ]] \
    || die "Refusing to operate on a parent Git repository"
}

clean_kernel() {
  local revision=$1
  assert_kernel_repo
  git -C "$KERNEL_SRC" cat-file -e "$revision^{commit}" \
    || die "Commit is not available locally; run setup"
  git -C "$KERNEL_SRC" reset --hard "$revision"
  git -C "$KERNEL_SRC" clean -ffdx
}

assert_build_dirs() {
  [[ ! -L "$BUILD_DIR" && ! -L "$BACKUP_DIR" ]] \
    || die "build and build.bak must be ordinary directories"
  [[ ! -e "$BUILD_DIR" || -d "$BUILD_DIR" ]] || die "build is not a directory"
  [[ ! -e "$BACKUP_DIR" || -d "$BACKUP_DIR" ]] || die "build.bak is not a directory"
}

rotate_build() {
  assert_build_dirs
  if [[ -d "$BUILD_DIR" ]]; then
    rm -rf -- "$BACKUP_DIR"
    mv -- "$BUILD_DIR" "$BACKUP_DIR"
  fi
  mkdir -p "$BUILD_DIR"
  touch "$BUILD_DIR/.gitkeep"
}

reset_workspace() {
  # Validate both scopes before discarding any local kernel changes.
  assert_kernel_repo
  assert_build_dirs
  git -C "$KERNEL_SRC" cat-file -e "$KERNEL_COMMIT^{commit}" \
    || die "Configured commit is not local; run setup first"
  clean_kernel "$KERNEL_COMMIT"
  rotate_build
  echo "RESET_OK: kernel clean; previous build is in build.bak"
}

check_inputs() {
  local missing=0 name
  for name in boot.img magisk_boot.img vendor_boot.img modules.tar loaded_modules.txt; do
    if [[ ! -s "$DEVICE_DIR/$name" ]]; then
      printf 'Missing input: device/%s\n' "$name" >&2
      missing=1
    fi
  done
  [[ $missing == 0 ]] || die "Prepare the slot-neutral device files described in README.md"
}

install_packages() {
  [[ $(id -u) == 0 ]] || die "Run setup as root inside the container"
  [[ $(uname -m) == x86_64 ]] || die "The supplied toolchain requires x86_64 Linux"
  source /etc/os-release
  [[ $ID == ubuntu && $VERSION_ID == 22.04 ]] || die "Dependency setup targets Ubuntu 22.04"
  local packages=() missing=() package
  mapfile -t packages < "$ROOT/scripts/packages.txt"
  for package in "${packages[@]}"; do
    if [[ $(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true) != installed ]]; then
      missing+=("$package")
    fi
  done
  if (( ${#missing[@]} )); then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
  else
    echo "PACKAGES_READY: no installation needed"
  fi
  dpkg-query -W > "$LOG_DIR/packages.txt"
  if ! command -v repo >/dev/null 2>&1; then
    local launcher
    launcher=$(mktemp)
    curl -fL https://storage.googleapis.com/git-repo-downloads/repo -o "$launcher"
    install -m 0755 "$launcher" /usr/local/bin/repo
    rm -f -- "$launcher"
  fi
  sha256sum "$(command -v repo)" > "$LOG_DIR/repo-launcher.sha256"
}

prepare_tool() {
  local expected=21be0ef297957c74184f0632dcf7d54777f064e64c8418c7d6ee107b9ddb3ffe
  mkdir -p "$TOOLS_DIR"
  if [[ ! -e "$TOOLS_DIR/magiskboot" ]]; then
    curl -fL \
      https://raw.githubusercontent.com/ravindu644/Android-Kernel-Tutorials/c0b59297dd0a32cb7b0678ab75e1f99f6b7470a7/tools/magiskboot \
      -o "$TOOLS_DIR/magiskboot.download"
    printf '%s  %s\n' "$expected" "$TOOLS_DIR/magiskboot.download" | sha256sum -c -
    mv "$TOOLS_DIR/magiskboot.download" "$TOOLS_DIR/magiskboot"
  fi
  printf '%s  %s\n' "$expected" "$TOOLS_DIR/magiskboot" | sha256sum -c -
  chmod 0755 "$TOOLS_DIR/magiskboot"
}

source_complete() {
  [[ -d "$SRC_DIR/.repo" && -e "$KERNEL_SRC/.git" && \
     -f "$SRC_DIR/build/build.sh" && -x "$SRC_DIR/$CLANG_BIN/clang" ]]
}

sync_source() {
  mkdir -p "$SRC_DIR"
  if [[ ! -d "$SRC_DIR/.repo" ]]; then
    [[ -z $(find "$SRC_DIR" -mindepth 1 -maxdepth 1 ! -name .gitkeep -print -quit) ]] \
      || die "src must be empty or an AOSP checkout root containing .repo and $KERNEL_SUBDIR"
  elif [[ -e "$KERNEL_SRC/.git" ]]; then
    clean_kernel HEAD
  fi
  # Supply a local-only identity if the container has no Git identity yet.
  # Existing global Git settings are never overwritten.
  local identity_env=()
  if ! git var GIT_COMMITTER_IDENT >/dev/null 2>&1; then
    identity_env=(GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=user.name \
      "GIT_CONFIG_VALUE_0=Kernel Builder" GIT_CONFIG_KEY_1=user.email \
      GIT_CONFIG_VALUE_1=kernel-builder@example.invalid)
  fi
  (
    cd "$SRC_DIR"
    env "${identity_env[@]}" repo init --depth=1 \
      -u "$MANIFEST_URL" -b "$MANIFEST_BRANCH" --no-use-superproject
    mkdir -p .repo/local_manifests
    local extra
    extra=$(find .repo/local_manifests -maxdepth 1 -name '*.xml' ! -name xpad.xml -print -quit)
    [[ -z $extra ]] || die "Review additional local manifests before changing source: $extra"
    python3 - "$KERNEL_PROJECT" "$KERNEL_SUBDIR" "$KERNEL_COMMIT" <<'PY'
import sys
import xml.etree.ElementTree as ET
name, path, commit = sys.argv[1:]
root = ET.Element("manifest")
ET.SubElement(root, "remove-project", name=name)
ET.SubElement(root, "project", name=name, path=path, remote="aosp",
              revision=commit, **{"clone-depth": "1"})
ET.ElementTree(root).write(".repo/local_manifests/xpad.xml",
                           encoding="utf-8", xml_declaration=True)
PY
    repo sync -d -c -j4 --no-tags --fail-fast
  )
}

setup() {
  install_packages
  check_inputs
  prepare_tool
  if source_complete && [[ $(git -C "$KERNEL_SRC" rev-parse HEAD) == "$KERNEL_COMMIT" ]]; then
    echo "SOURCE_REUSED: matching checkout and toolchain; skipping source download"
  else
    sync_source
  fi
  source_complete || die "Source or toolchain is incomplete"
  reset_workspace
  "$SRC_DIR/$CLANG_BIN/clang" --version
  (cd "$SRC_DIR"; repo manifest -r -o "$LOG_DIR/source-manifest.xml")
  echo "SETUP_OK: dependencies ready, source clean; apply a patch before building"
}

assert_ready_source() {
  assert_kernel_repo
  [[ $(git -C "$KERNEL_SRC" rev-parse HEAD) == "$KERNEL_COMMIT" ]] \
    || die "The source commit differs from kernel.conf; run setup"
  [[ -f "$SRC_DIR/build/build.sh" && -x "$SRC_DIR/$CLANG_BIN/clang" ]] \
    || die "Build driver or compiler missing; run setup"
}

apply_experiment() {
  [[ $# == 1 ]] || die "Usage: ./main.sh patch patch/file.sh"
  assert_ready_source
  local script
  script=$(readlink -f "$1")
  [[ $script == "$ROOT/patch/"* && $script == *.sh && -f $script ]] \
    || die "Choose a shell script inside patch/"
  bash "$script"
  git -C "$KERNEL_SRC" diff --check
  git -C "$KERNEL_SRC" diff --stat
  echo "PATCH_OK"
}

run_check() {
  local image=${1:-$BUILD_DIR/build.img} required=() entry
  for entry in "${REQUIRED_CONFIG[@]}"; do required+=(--require "$entry"); done
  python3 "$ROOT/scripts/check.py" \
    --root "$ROOT" --image "$image" --expected-release "$EXPECTED_RELEASE" \
    --boot-size "$BOOT_IMAGE_SIZE" "${required[@]}"
}

build_kernel() {
  assert_ready_source
  check_inputs
  prepare_tool
  [[ ! -L "$BUILD_DIR" ]] || die "build must be an ordinary directory"
  mkdir -p "$BUILD_DIR"
  rm -f -- "$BUILD_DIR/build.img" "$BUILD_DIR/build.img.sha256" \
    "$BUILD_DIR/build.img.partial" "$BUILD_DIR/check-report.json"
  export PATH="$SRC_DIR/$CLANG_BIN:$PATH"
  bash "$ROOT/scripts/normalize-config.sh"
  (
    cd "$SRC_DIR"
    unset OUT_DIR DIST_DIR OUT_DIR_SUFFIX SKIP_MRPROPER SKIP_DEFCONFIG
    unset SKIP_IF_VERSION_MATCHES KCONFIG_CONFIG LOCALVERSION KERNEL_DIR GKI_DEFCONFIG_FRAGMENT
    TZ=UTC LTO="$LTO" BUILD_CONFIG="$BUILD_CONFIG" \
      OUT_DIR="$BUILD_DIR/out" DIST_DIR="$BUILD_DIR/dist" \
      bash build/build.sh -j"$JOBS"
  )
  local output="$BUILD_DIR/out/$KERNEL_SUBDIR"
  cp "$BUILD_DIR/dist/Image" "$BUILD_DIR/Image"
  cp "$output/Module.symvers" "$BUILD_DIR/Module.symvers"
  cp "$output/.config" "$BUILD_DIR/kernel.config"
  cp "$output/include/config/kernel.release" "$BUILD_DIR/kernel.release"
  cp "$output/modules.builtin" "$BUILD_DIR/modules.builtin"
  git -C "$KERNEL_SRC" diff --binary HEAD > "$BUILD_DIR/kernel-changes.patch"
  gzip -n -c -9 "$BUILD_DIR/Image" > "$BUILD_DIR/Image.gz"
  local repack status
  repack=$(mktemp -d "$BUILD_DIR/repack.XXXXXX")
  (
    cd "$repack"
    unset PATCHVBMETAFLAG
    "$TOOLS_DIR/magiskboot" unpack "$DEVICE_DIR/magisk_boot.img"
    cp "$BUILD_DIR/Image.gz" kernel
    "$TOOLS_DIR/magiskboot" repack "$DEVICE_DIR/magisk_boot.img" "$BUILD_DIR/build.img.partial"
  )
  set +e
  run_check "$BUILD_DIR/build.img.partial"
  status=$?
  set -e
  [[ $status == 0 || $status == 2 ]] || die "Automatic checks failed; inspect build/check-report.json"
  mv "$BUILD_DIR/build.img.partial" "$BUILD_DIR/build.img"
  (cd "$BUILD_DIR"; sha256sum Image Module.symvers build.img > SHA256SUMS)
  (cd "$BUILD_DIR"; sha256sum build.img > build.img.sha256)
  echo "BUILD_OK: build/Image and build/build.img"
  return "$status"
}

shift
case "$command" in
  setup) [[ $# == 0 ]] || die "setup takes no arguments"; setup ;;
  reset) [[ $# == 0 ]] || die "reset takes no arguments"; reset_workspace ;;
  patch) apply_experiment "$@" ;;
  build) [[ $# == 0 ]] || die "build takes no arguments"; build_kernel ;;
  check) [[ $# == 0 ]] || die "check takes no arguments"; run_check ;;
esac
