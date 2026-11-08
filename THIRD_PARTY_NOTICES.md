# Third-party notices

Original project scripts and documentation use the root MIT license. This does not relicense upstream kernel code or third-party patches.

`patch/files/001.GKI-below-6.12-fix_sysvipc_kabi_6_7_8.patch` and `002.5.10_or_lower_use_android_abi_padding_for_posix_mqueue.patch` are unchanged from Droidspaces-OSS commit `6659b5b5f96d7d8dcd86033b5a42a3fcc364bbf9`. Their original author/sign-off metadata is retained. The repository license at that revision is included in `LICENSES/Droidspaces-LICENSE.txt`.

The kernel source context in patches 003/004 and fixtures in `tests/fixtures/kernel-base` comes from AOSP common commit `7c6bbcca33e1734642601acf07a14140116fa956`. Preserve the original per-file notices. The GPL-2.0 text is included in `LICENSES/GPL-2.0.txt`.

The magiskboot prebuilt, AOSP toolchains/source, Repo launcher, Ubuntu packages, firmware, and device readbacks are not bundled. They retain their respective upstream terms. Kernel binary/source distribution requires following the relevant upstream license requirements; the MIT license on orchestration is not a substitute.
