"""Offline tests for the five-command container workflow."""

from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
import gzip
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kernel_check", ROOT / "scripts/check.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class ShellWorkspace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ("main.sh", "kernel.conf"):
            shutil.copyfile(ROOT / name, self.root / name)
        for name in ("patch", "scripts"):
            shutil.copytree(ROOT / name, self.root / name)
        self.kernel = self.root / "src/common"
        shutil.copytree(ROOT / "tests/fixtures/kernel-base", self.kernel)
        (self.kernel / ".gitignore").write_text("ignored-file\n")
        subprocess.run(["git", "init", "-q", str(self.kernel)], check=True)
        subprocess.run(["git", "add", "."], cwd=self.kernel, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "Base fixture",
            ],
            cwd=self.kernel,
            check=True,
        )
        self.commit = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.kernel).decode().strip()
        )
        config = (
            (self.root / "kernel.conf")
            .read_text()
            .replace("7c6bbcca33e1734642601acf07a14140116fa956", self.commit)
        )
        (self.root / "kernel.conf").write_text(config)
        for name in ("device", "build", "tools", "log"):
            (self.root / name).mkdir()
        (self.root / "src/.repo").mkdir()
        driver = self.root / "src/build/build.sh"
        driver.parent.mkdir()
        driver.write_text("#!/bin/sh\nexit 0\n")
        compiler = self.root / "src/prebuilts-master/clang/host/linux-x86/clang-r416183b/bin/clang"
        compiler.parent.mkdir(parents=True)
        compiler.write_text("#!/bin/sh\necho 'fixture clang'\n")
        compiler.chmod(0o755)
        for name in (
            "boot.img",
            "magisk_boot.img",
            "vendor_boot.img",
            "modules.tar",
            "loaded_modules.txt",
        ):
            (self.root / "device" / name).write_text("device input fixture\n")

    def tearDown(self):
        self.temp.cleanup()

    def command(self, *args):
        return subprocess.run(
            ["bash", str(self.root / "main.sh"), *args],
            cwd=self.root,
            text=True,
            capture_output=True,
        )

    def functions(self, body):
        # Exercise the real shell functions, replacing only OS/network/build
        # boundaries in individual tests. No apt or network operation is run.
        text = (self.root / "main.sh").read_text().split("\nshift\n", 1)[0]
        text = re.sub(
            r"^ROOT=.*$", "ROOT=" + shlex.quote(str(self.root)), text, count=1, flags=re.MULTILINE
        )
        script = "set -- setup\n" + text + "\n" + body
        return subprocess.run(
            ["bash", "-c", script],
            cwd=self.root,
            text=True,
            capture_output=True,
            env=dict(os.environ, XPAD_LOGGED="1"),
        )

    def test_reset_restores_source_and_rotates_exactly_one_backup(self):
        source = self.kernel / "include/linux/sched.h"
        original = source.read_bytes()
        source.write_bytes(original + b"\n/* experiment */\n")
        (self.kernel / "ignored-file").write_text("ignored")
        (self.kernel / "untracked-file").write_text("untracked")
        nested = self.kernel / "nested-experiment"
        subprocess.run(["git", "init", "-q", str(nested)], check=True)
        (self.root / "build/current").write_text("current")
        (self.root / "build.bak").mkdir()
        (self.root / "build.bak/older").write_text("older")
        result = self.command("reset")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse((self.kernel / "ignored-file").exists())
        self.assertFalse((self.kernel / "untracked-file").exists())
        self.assertFalse(nested.exists())
        self.assertEqual((self.root / "build.bak/current").read_text(), "current")
        self.assertFalse((self.root / "build.bak/older").exists())
        self.assertTrue((self.root / "build/.gitkeep").exists())
        self.assertTrue((self.root / "device/magisk_boot.img").exists())
        self.assertTrue((self.root / "src/.repo").exists())
        self.assertTrue((self.root / "log/reset.log").exists())

    def test_second_reset_replaces_backup_with_latest_build(self):
        (self.root / "build/one").write_text("one")
        self.assertEqual(self.command("reset").returncode, 0)
        (self.root / "build/two").write_text("two")
        self.assertEqual(self.command("reset").returncode, 0)
        self.assertFalse((self.root / "build.bak/one").exists())
        self.assertEqual((self.root / "build.bak/two").read_text(), "two")

    def test_reset_rejects_symlink_build_before_changing_source(self):
        source = self.kernel / "include/linux/sched.h"
        source.write_text(source.read_text() + "\n/* keep this edit */\n")
        before = source.read_bytes()
        shutil.rmtree(self.root / "build")
        (self.root / "outside").mkdir()
        (self.root / "build").symlink_to(self.root / "outside", target_is_directory=True)
        self.assertNotEqual(self.command("reset").returncode, 0)
        self.assertEqual(source.read_bytes(), before)

    def test_reset_does_not_fall_back_to_parent_git_repository(self):
        shutil.rmtree(self.kernel / ".git")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        result = self.command("reset")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Kernel Git repository missing", result.stdout)

    def test_reset_missing_commit_preserves_current_build_and_edits(self):
        path = self.root / "kernel.conf"
        path.write_text(path.read_text().replace(self.commit, "f" * 40))
        (self.root / "build/keep").write_text("keep")
        self.assertNotEqual(self.command("reset").returncode, 0)
        self.assertTrue((self.root / "build/keep").exists())

    def test_custom_patch_receives_kernel_path_and_is_logged(self):
        script = self.root / "patch/custom.sh"
        script.write_text('set -eu\nprintf "custom edit\\n" > "$KERNEL_SRC/custom.txt"\n')
        result = self.command("patch", "patch/custom.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.kernel / "custom.txt").read_text(), "custom edit\n")
        self.assertTrue((self.root / "log/patch.log").exists())

    def test_patch_script_outside_patch_folder_is_rejected(self):
        (self.root / "outside.sh").write_text('touch "should-not-exist"\n')
        self.assertNotEqual(self.command("patch", "outside.sh").returncode, 0)
        self.assertFalse((self.root / "should-not-exist").exists())

    def test_full_baseline_patches_have_recorded_source_blobs(self):
        script = self.root / "patch/droidspaces.sh"
        script.write_text(
            script.read_text().replace(
                "expected=7c6bbcca33e1734642601acf07a14140116fa956", "expected=" + self.commit
            )
        )
        result = self.command("patch", "patch/droidspaces.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for relative, expected in (
            ("arch/arm64/configs/gki_defconfig", "b44d2795d54e15f17954d8e12e38f7697a6cab77"),
            ("scripts/setlocalversion", "f5bbfb54346edc4890b0c3b3f7a5c359ab06d31a"),
        ):
            data = (self.kernel / relative).read_bytes()
            blob = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
            self.assertEqual(blob, expected)
        repeat = self.command("patch", "patch/droidspaces.sh")
        self.assertEqual(repeat.returncode, 0, repeat.stdout + repeat.stderr)
        self.assertIn("already applied", repeat.stdout)

    def test_setup_reuses_matching_source_and_resets_it(self):
        result = self.functions(
            """
install_packages() { echo MOCK_PACKAGES_READY; }
prepare_tool() { :; }
sync_source() { echo UNEXPECTED_SYNC; return 91; }
repo() { [[ $1 == manifest ]] || return 92; }
setup
"""
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SOURCE_REUSED", result.stdout)
        self.assertIn("SETUP_OK", result.stdout)
        self.assertNotIn("UNEXPECTED_SYNC", result.stdout)

    def test_setup_syncs_when_selected_commit_changes(self):
        (self.kernel / "version-two").write_text("next")
        subprocess.run(["git", "add", "."], cwd=self.kernel, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "Version two",
            ],
            cwd=self.kernel,
            check=True,
        )
        next_commit = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.kernel).decode().strip()
        )
        subprocess.run(
            ["git", "reset", "--hard", self.commit],
            cwd=self.kernel,
            check=True,
            capture_output=True,
        )
        config = self.root / "kernel.conf"
        config.write_text(config.read_text().replace(self.commit, next_commit))
        result = self.functions(
            """
install_packages() { :; }
prepare_tool() { :; }
sync_source() { echo SOURCE_SYNC_REQUESTED; git -C "$KERNEL_SRC" reset --hard "$KERNEL_COMMIT"; }
repo() { [[ $1 == manifest ]]; }
setup
"""
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SOURCE_SYNC_REQUESTED", result.stdout)

    def test_setup_reports_all_missing_device_inputs(self):
        (self.root / "device/magisk_boot.img").unlink()
        result = self.functions("install_packages() { :; }; setup")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("device/magisk_boot.img", result.stderr)

    def simulated_build(self, check_status):
        binary = self.root / "fake-bin"
        binary.mkdir()
        make = binary / "make"
        make.write_text(
            """#!/usr/bin/env bash
set -e
for value in "$@"; do
  if [[ $value == O=* ]]; then output=${value#O=}; fi
done
mkdir -p "$output"
cp "$KERNEL_SRC/arch/arm64/configs/gki_defconfig" "$output/.config"
cp "$output/.config" "$output/defconfig"
"""
        )
        make.chmod(0o755)
        driver = self.root / "src/build/build.sh"
        driver.write_text(
            """#!/usr/bin/env bash
set -e
mkdir -p "$DIST_DIR" "$OUT_DIR/common/include/config"
printf 'synthetic compiled image\n' > "$DIST_DIR/Image"
printf 'CONFIG_MODVERSIONS=y\n' > "$OUT_DIR/common/.config"
printf 'fixture release\n' > "$OUT_DIR/common/include/config/kernel.release"
printf '0x1 fixture vmlinux EXPORT_SYMBOL\n' > "$OUT_DIR/common/Module.symvers"
touch "$OUT_DIR/common/modules.builtin"
"""
        )
        tool = self.root / "tools/magiskboot"
        tool.write_text(
            """#!/usr/bin/env bash
set -e
case "$1" in
  unpack) printf 'fixture ramdisk\n' > ramdisk.cpio ;;
  repack) printf 'synthetic boot\n' > "$3" ;;
  *) exit 1 ;;
esac
"""
        )
        tool.chmod(0o755)
        body = "export PATH=" + shlex.quote(str(binary)) + ':"$PATH"\n'
        body += "prepare_tool() { :; }\n"
        body += (
            "run_check() { printf '{\"accepted\":%s}\\n' "
            + ("true" if check_status in (0, 2) else "false")
            + ' > "$BUILD_DIR/check-report.json"; return '
            + str(check_status)
            + "; }\n"
        )
        body += "build_kernel\n"
        return self.functions(body)

    def test_build_publishes_outputs_only_after_successful_checks(self):
        result = self.simulated_build(0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "build/Image").exists())
        self.assertTrue((self.root / "build/build.img").exists())
        self.assertTrue((self.root / "build/build.img.sha256").exists())
        self.assertFalse((self.root / "build/build.img.partial").exists())

    def test_known_review_is_preserved_as_exit_two_after_publication(self):
        result = self.simulated_build(2)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue((self.root / "build/build.img").exists())

    def test_failed_check_leaves_partial_without_publishing_image(self):
        result = self.simulated_build(1)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "build/build.img").exists())
        self.assertTrue((self.root / "build/build.img.partial").exists())


class ModuleChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vendor, self.early = self.root / "vendor", self.root / "early"
        self.vendor.mkdir()
        self.early.mkdir()
        self.symvers = self.root / "Module.symvers"
        self.symvers.write_text(
            "0x1 core vmlinux EXPORT_SYMBOL\n0x2 module_layout vmlinux EXPORT_SYMBOL\n"
            "0x5 module_only drivers/test EXPORT_SYMBOL\n"
        )
        self.loaded = self.root / "loaded.txt"
        self.loaded.write_text("a 1 0 - Live 0\nh 1 0 - Live 0\n")
        for parent, name in ((self.vendor, "a"), (self.vendor, "unused"), (self.early, "h")):
            (parent / f"{name}.ko").write_bytes(name.encode())
        (self.vendor / "modules.load").write_text("a.ko\n")
        self.imports = {
            "a": "0x1 core\n0x2 module_layout\n0x3 helper\n",
            "h": "0x2 module_layout\n",
            "unused": "0x9 core\n0x4 missing\n0x5 module_only\n",
        }

    def tearDown(self):
        self.temp.cleanup()

    def fake_run(self, args, **kwargs):
        name = Path(args[-1]).stem
        if args[0] == "modinfo":
            return {
                "name": name,
                "depends": "h" if name == "a" else "",
                "vermagic": "fixture modversions",
            }[args[2]]
        if args[0] == "readelf":
            return (
                "[ 2] __ksymtab_strings STRTAB\n" if name == "h" else "[ 2] __versions PROGBITS\n"
            )
        if args[0] == "modprobe":
            return "0x3 helper\n" if args[1] == "--show-exports" else self.imports[name]
        raise AssertionError(args)

    def analyze(self):
        with patch.object(checker, "run", side_effect=self.fake_run):
            return checker.analyze(self.symvers, self.loaded, [self.vendor, self.early], self.root)

    def test_inactive_issues_and_non_builtin_exports_are_recorded(self):
        report = self.analyze()
        self.assertEqual(report["status"], "REVIEW")
        self.assertEqual(report["counts"]["MISSING"], 2)
        self.assertEqual(report["counts"]["CRC_MISMATCH"], 1)
        self.assertEqual(report["active_covered"], 2)

    def test_active_problem_blocks(self):
        (self.vendor / "modules.load").write_text("a.ko\nunused.ko\n")
        self.assertEqual(self.analyze()["status"], "BLOCKED")

    def test_zero_crc_is_not_a_match(self):
        self.imports["a"] += "0x0 core\n"
        self.assertEqual(self.analyze()["counts"]["UNVERIFIED_CRC"], 1)

    def test_missing_active_module_blocks(self):
        (self.vendor / "a.ko").unlink()
        self.assertEqual(self.analyze()["status"], "BLOCKED")

    def test_known_review_is_exact_and_does_not_accept_new_findings(self):
        report = self.analyze()
        known = {"unused": {"CRC_MISMATCH": 1, "MISSING": 2}}
        self.assertTrue(checker.accept_module_report(report, known))
        self.assertFalse(checker.accept_module_report(report, {}))
        report["findings"][0]["required"] = True
        self.assertFalse(checker.accept_module_report(report, known))

    def test_clean_module_set_passes(self):
        self.imports["unused"] = "0x1 core\n"
        report = self.analyze()
        self.assertEqual(report["status"], "CLEAR")
        self.assertTrue(checker.accept_module_report(report, {}))


class CheckHelpers(unittest.TestCase):
    def test_embedded_config_accepts_trailing_bytes_and_rejects_truncation(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "Image"
            config = b"CONFIG_MODVERSIONS=y\n"
            image.write_bytes(b"headerIKCFG_ST" + gzip.compress(config) + b"IKCFG_EDtail")
            self.assertEqual(checker.embedded_config(image), config)
            image.write_bytes(b"IKCFG_ST" + gzip.compress(config)[:-5])
            with self.assertRaises(checker.XpadError):
                checker.embedded_config(image)

    def test_archive_paths_cannot_escape(self):
        for name in ("../outside", "/absolute", "modules/../../outside"):
            with self.assertRaises(checker.XpadError):
                checker.safe_name(name)
        checker.safe_name("modules/driver.ko")

    def test_missing_build_has_an_independent_failure_report(self):
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()):
            root = Path(temporary)
            code = checker.main(["--root", str(root), "--image", str(root / "build/build.img")])
            self.assertEqual(code, 1)
            report = json.loads((root / "build/check-report.json").read_text())
            self.assertFalse(report["accepted"])
            self.assertEqual(report["status"], "CHECK_FAIL")


if __name__ == "__main__":
    unittest.main()
