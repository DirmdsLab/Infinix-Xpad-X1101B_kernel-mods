# Infinix Xpad X1101B kernel experiments

One repository folder, one source checkout, and five commands:

```sh
./main.sh setup
./main.sh reset
./main.sh patch patch/droidspaces.sh
./main.sh build
./main.sh check
```

**Use matching device files from the same tablet, firmware, and active slot.** The local filenames deliberately contain no `_a` or `_b`. For example, a slot-B `boot_b.img` is stored as `device/boot.img`; `magisk_patched_b.img` becomes `device/magisk_boot.img`. Check the active slot before taking readbacks and rename the files yourself. Slot-neutral filenames do not make files from different slots interchangeable.

The default source and four baseline changes reproduce the configuration that booted successfully as `5.10.209-android12-9-g7c6bbcca33e1-uwu`, with Magisk 31.1 and Droidspaces 7.1.0. Source, image inputs, output, tools, and logs all live inside this repository directory.

## Device and tested software

| Item | Tested device / software |
| --- | --- |
| Device | Infinix Xpad X1101B |
| RAM / storage | 4 GB / 256 GB, reported by the owner |
| SoC | MediaTek Helio G99 / MT8781 |
| Stock ROM base | Android 14 |
| Installed GSI | Android 16, LineageOS GSI by MisterZtr |
| Selected GSI release | `v2026.05.24-lineage23.2`; exact image flavor was not specified |
| Root | Magisk v31.1 |
| Target runtime | Droidspaces v7.1.0 |
| Kernel base | Linux 5.10.209, Android 12 GKI, KMI generation 9 |
| Compiler | Android Clang 12.0.5 / r416183b / build 7284624 |

- [GSMArena device reference](https://www.gsmarena.com/infinix_xpad-13263.php)
- [Manufacturer X1101B specifications](https://wap.rw.infinixmobility.com/specs/xpad) — regional memory variants differ from the owner's 4 GB unit.
- [Magisk v31.1 APK](https://github.com/topjohnwu/Magisk/releases/download/v31.1/Magisk-v31.1.apk) · [Magisk repository](https://github.com/topjohnwu/Magisk)
- [LineageOS GSI release](https://github.com/MisterZtr/LineageOS_gsi/releases/tag/v2026.05.24-lineage23.2) · [GSI repository](https://github.com/MisterZtr/LineageOS_gsi)
- [Droidspaces repository](https://github.com/ravindu644/Droidspaces-OSS)

The Android 16 GSI does not change the kernel baseline: the inspected working boot contained an Android 12 / Linux 5.10 kernel.

## Folder layout

| Path | Contents |
| --- | --- |
| `main.sh` | Container command entry point |
| `kernel.conf` | Commit, manifest, compiler, job count, expected release, and config checks |
| `device/` | Your original device inputs; contents ignored by Git |
| `src/` | AOSP checkout root: `.repo/`, `common/`, `build/`, prebuilts, and related projects |
| `patch/` | Experiment scripts, baseline patch files, and known static exceptions |
| `build/` | Current build, raw Image, repacked `build.img`, and check report |
| `build.bak/` | One previous build, replaced on the next reset |
| `log/` | Setup, reset, patch, build, and independent-check logs |
| `tools/` | Downloaded, checksummed magiskboot |
| `scripts/` | Config normalization and independent checks |

Empty `device`, `src`, `build`, `log`, and `tools` folders are kept with `.gitkeep`. Their generated/private contents are ignored. The workflow is controlled by `main.sh` and `kernel.conf`.

## 1. Prepare device inputs — HOST

The owner obtained the original boot image using **UnlockTool readback**. Another tool is suitable if it produces the correct raw partition image for the device and selected slot. Use partition names from your own tablet rather than physical block addresses copied from another device.

Place the following files inside `device/`:

| Filename | Meaning | Required |
| --- | --- | --- |
| `boot.img` | Original boot readback for this firmware/slot | Yes |
| `magisk_boot.img` | Corresponding Magisk-patched boot template | Yes |
| `vendor_boot.img` | Raw **vendor_boot** partition image, containing early-boot modules | Yes |
| `modules.tar` | Archive containing the `modules/` directory from vendor_dlkm | Yes |
| `loaded_modules.txt` | `/proc/modules` from the working device | Yes |
| `vendor.img` | Raw **vendor** partition readback | Optional archive |
| `vendor_dlkm.img` | Raw vendor_dlkm partition readback | Optional archive |
| `working_boot.img` | Current working boot backup for manual recovery | Recommended |
| `dmesg.txt` | Kernel log from the working device | Optional record |

**`vendor.img` and `vendor_boot.img` are different partitions.** The checker uses `vendor_boot.img` and `modules.tar`; it does not extract modules from a raw vendor image. Keep `vendor.img` when available, but do not rename it to `vendor_boot.img`.

For the default device baseline, the original boot and Magisk boot are each 67,108,864 bytes. They contain the same original kernel; the Magisk ramdisk differs. Use the same matched pair used during the successful build.

Check the active slot from a host terminal:

```sh
adb shell getprop ro.boot.slot_suffix
adb shell "su -c 'id'"
```

For a working rooted tablet whose active slot is **b**, these host commands collect the additional files. Run them from the repository root:

```sh
adb exec-out "su -c 'cat /dev/block/by-name/vendor_boot_b'" > device/vendor_boot.img
adb exec-out "su -c 'tar -C /vendor_dlkm/lib -cf - modules'" > device/modules.tar
adb exec-out "su -c 'cat /proc/modules'" > device/loaded_modules.txt
adb exec-out "su -c 'cat /dev/block/by-name/boot_b'" > device/working_boot.img
adb exec-out "su -c 'dmesg'" > device/dmesg.txt
```

For active slot **a**, replace the partition suffixes in the read commands with `_a`. Keep the destination filenames unchanged. `boot.img` remains the original firmware readback, not an automatically renamed dump of a currently modified boot. These commands only read the tablet; they run on the host, not inside the build container.

## 2. Create the container — HOST

Open a terminal **inside the root of `Infinix-Xpad-X1101B_kernel-mods`**, then run:

```sh
podman run -dit \
  --name Infinix-Xpad-X1101B_kernel-mods_Workspaces \
  -v "$(pwd):/Workspaces/Infinix-Xpad-X1101B_kernel-mods:Z" \
  -w /Workspaces \
  docker.io/library/ubuntu:22.04 \
  bash
```

Enter it:

```sh
podman exec -it Infinix-Xpad-X1101B_kernel-mods_Workspaces bash
```

Inside the container:

```sh
cd /Workspaces/Infinix-Xpad-X1101B_kernel-mods
chmod +x main.sh
```

The host files edited in Codium are the same files visible through this mount. Use the existing container if it already mounts this repository at the stated path. All `main.sh` commands run **inside the Ubuntu container**.

## 3. Setup — CONTAINER

```sh
./main.sh setup
```

Setup:

1. Installs missing packages from `scripts/packages.txt` on Ubuntu 22.04 x86_64. Installed packages are reused.
2. Checks that the five required device input files are present and nonempty.
3. Installs the official Repo launcher when missing and obtains the pinned x86_64 magiskboot when missing.
4. Reuses a complete `src/` checkout when its kernel HEAD matches `KERNEL_COMMIT` and the build driver/compiler are present. It skips source download in that case.
5. Otherwise initializes/synchronizes the configured AOSP manifest and exact kernel commit, using existing local Git objects where possible.
6. Resets the selected kernel repository to the configured commit and removes its untracked/ignored files.
7. Rotates the old `build/` to `build.bak/` and creates a fresh `build/`.
8. Records the resolved source manifest and compiler version in `log/`.

**Setup leaves the kernel source clean and applies no feature patches. Running setup again also resets the kernel and replaces the build backup.** Save experiment changes as patch scripts before doing so.

If the container has no Git identity, only Repo initialization receives a local placeholder identity. Global Git settings are not overwritten and no commits are published.

For an existing checkout supplied manually, `src/` itself must contain `.repo/`, `common/`, `build/`, and the prebuilts. An extra `src/android-kernel/` level is not the layout used here. Setup does not move or import folders. Additional foreign local manifests are reported for manual review when a source sync is necessary.

The original PC had 12 logical CPUs, around 16 GB RAM and 8 GB swap. The default remains two build jobs. Allow roughly 35–45 GiB of free space for source, build, one backup, and headroom; actual usage varies.

## 4. Apply an experiment

For the exact tested Droidspaces baseline:

```sh
./main.sh patch patch/droidspaces.sh
```

This applies four checked-in patches: SYSVIPC padding 6/7/8, POSIX mqueue padding, the normalized GKI defconfig changes, and the Git suffix change from `-dirty` to `-uwu`. The upstream patches and the final configuration/version-script contents are unchanged from the successful session.

The effective options are:

```text
CONFIG_SYSVIPC=y
CONFIG_POSIX_MQUEUE=y
CONFIG_IPC_NS=y
CONFIG_PID_NS=y
CONFIG_DEVTMPFS=y
CONFIG_NETFILTER_XT_MATCH_ADDRTYPE=y
CONFIG_MODVERSIONS=y
```

CFS bandwidth and the cgroup PID controller remain disabled in the baseline. The build uses ThinLTO. The minimal defconfig need not explicitly retain PID_NS/IPC_NS lines when the resulting `.config` enables them by default.

For another experiment, copy `patch/example.sh` to a new file and edit it in Codium. Patch scripts are ordinary trusted Bash programs. `REPO_ROOT`, `SRC_DIR`, and `KERNEL_SRC` are provided; write your changes to `KERNEL_SRC`. Do not commit those changes to a different kernel HEAD: keep them as working-tree edits so reset restores the configured base.

```sh
./main.sh patch patch/my-feature.sh
```

Reset is limited to kernel source and build rotation; it does not undo arbitrary actions performed by a custom shell script outside those locations.

## 5. Build and automatic checks

```sh
./main.sh build
```

The script normalizes the current GKI defconfig, verifies that normalization preserves its effective configuration, and runs the legacy AOSP build driver with the selected compiler, ThinLTO, and job count. Intermediates are under `build/out/`; final files are easy to find:

| File | Meaning |
| --- | --- |
| `build/Image` | Uncompressed ARM64 kernel |
| `build/Image.gz` | Deterministically gzip-compressed kernel used for repack |
| `build/Module.symvers` | Exported symbols and CRCs |
| `build/kernel.config` | Effective configuration |
| `build/kernel.release` | Actual release string |
| `build/kernel-changes.patch` | Current source changes |
| `build/build.img` | Verified repack from `device/magisk_boot.img` |
| `build/build.img.sha256` | Final boot-image checksum |
| `build/SHA256SUMS` | Image, Module.symvers, and boot-image checksums |
| `build/check-report.json` | Complete automatic check results |

A build replaces the current outputs. Reset is the action that keeps one previous build. A failed compile/check does not publish a new `build.img`; a repack that failed checks can remain as `build.img.partial` for inspection.

Checks run automatically after compilation and repack. They compare the ARM64 image/banner, embedded configuration, required config values, image size, template headers, kernel bytes, preserved Magisk ramdisk and other non-kernel components, vendor module coverage, CRCs, and candidate providers. The same checks can be run later without compiling:

```sh
./main.sh check
```

| Result | Exit | Meaning |
| --- | ---: | --- |
| `CHECK_PASS` | 0 | Covered static checks passed |
| `CHECK_REVIEW` | 2 | Only the recorded inactive-module findings remain; report retains REVIEW |
| `CHECK_FAIL` | 1 | Failed input/config/image check or unreviewed module findings |

Exit 2 is expected for the recorded baseline. Read the report instead of chaining the command with `&&`. The check validates static properties, not AVB signatures, init-script ordering, every dynamic module request, or real hardware boot. It does not flash the tablet.

## 6. Reset for the next experiment

```sh
./main.sh reset
```

Reset does exactly two things:

1. `git reset --hard` to `KERNEL_COMMIT` and `git clean -ffdx` **inside the kernel Git repository only**, including nested untracked experiment repositories.
2. Replace the previous `build.bak/` with the current `build/`, then create an empty `build/`.

The manifest checkout, compiler/prebuilts, `device/`, `patch/`, project code, and logs remain in place. The previous backup is overwritten on every reset, including a reset of an empty current build. An unavailable configured commit requires setup first; reset does not download anything.

Then apply your next patch and build again:

```sh
./main.sh patch patch/my-feature.sh
./main.sh build
```

## 7. Select another kernel version

Edit `kernel.conf` in Codium. Set the exact commit, matching manifest branch, compiler path, build config, expected release, and required config values. Then run:

```sh
./main.sh setup
```

A changed/missing kernel is synchronized to the requested commit; a complete matching kernel is reused and reset. No second source directory is created. The baseline patch script intentionally accepts only its original 5.10 commit; provide another patch script for other source versions.

This driver supports the legacy AOSP `build/build.sh` workflow and ARM64 boot format used here. A Kleaf-only kernel needs another build driver; changing a commit alone does not add that support or establish compatibility with the tablet's vendor modules. Never disable MODVERSIONS or force module loading to hide ABI failures.

## Recorded result and limitations

The owner's successful build booted on slot `_b`, retained Magisk 31.1, passed all Droidspaces 7.1.0 mandatory/recommended checks, and ran a container with systemd as PID 1. Runtime configuration matched the build, all 355 previously active module names remained Live, and no additional selected kernel-error patterns were found.

Static analysis checked 412 module files / 375 unique names. It found 19,260 kernel CRC matches, 2,896 vendor-candidate matches, and these retained exceptions:

| Inactive module | CRC mismatches | Missing symbols |
| --- | ---: | ---: |
| defrag_gc_ko | 15 | 5 |
| dev_gc | 21 | 9 |
| read_ahead | 2 | 2 |

They were absent from the active set and checked boot/recovery lists/hard dependencies. Only these known findings are accepted as REVIEW; other findings fail the check. They are not removed or force-loaded.

Macvlan, IPv6 NAT, CPU/process limits, and user-namespace sandboxing were not enabled. Individual hardware smoke-test results and long-term stability of the new uwu build were not fully recorded.

The old successful boot SHA-256 was `c7115492b3fba5c7592ce59d81a4d3335ed9316ff3658203b42689ef6ad688e6`. The new folder/output paths can affect binary reproducibility, so that is historical evidence, not a required checksum for this reorganized workflow. The source patches and baseline options remain the same.

## Sources and tests

- [Pinned AOSP source](https://android.googlesource.com/kernel/common/+/7c6bbcca33e1734642601acf07a14140116fa956)
- [Pinned Droidspaces configuration guide](https://github.com/ravindu644/Droidspaces-OSS/blob/6659b5b5f96d7d8dcd86033b5a42a3fcc364bbf9/Documentation/Kernel-Configuration.md)
- [Pinned Droidspaces patches](https://github.com/ravindu644/Droidspaces-OSS/tree/6659b5b5f96d7d8dcd86033b5a42a3fcc364bbf9/Documentation/resources/kernel-patches/GKI/below-kernel-6.12)
- [AOSP kernel builds](https://source.android.com/docs/setup/build/building-kernels)
- [Magisk boot tools](https://github.com/topjohnwu/Magisk/blob/master/docs/tools.md)
- [Linux Module.symvers / module versioning](https://docs.kernel.org/kbuild/modules.html)
- [AOSP vendor ramdisk modules](https://source.android.com/docs/core/architecture/kernel/kernel-module-support)
- [Linux reproducible builds](https://docs.kernel.org/kbuild/reproducible-builds.html)

The tool download is pinned to tutorial commit `c0b59297dd0a32cb7b0678ab75e1f99f6b7470a7` with SHA-256 `21be0ef297957c74184f0632dcf7d54777f064e64c8418c7d6ee107b9ddb3ffe`.

Run offline regression tests with:

```sh
python3 -m unittest discover -s tests -v
```

Packaging tests use local fixtures. The historical device boot is not a claim that this reorganized shell workflow has already been compiled/flashed again on the owner's PC. A fresh full source sync, real-device inputs, complete kernel compile, and device boot still need to be exercised there.

During packaging, 23 offline regression tests passed. The standalone checker also passed an isolated test using the real pinned magiskboot with synthetic 64 MiB header-v4 images, gzip kernels, and an lz4_legacy Magisk ramdisk. Vendor extraction/CRC inputs were mocked for that isolated tool test; CRC analysis has separate regression tests. The synthetic payload was not bootable and no device was flashed.

Original orchestration is MIT-licensed. Third-party patches and kernel fixtures retain their original notices; see `THIRD_PARTY_NOTICES.md` and `LICENSES/`.
