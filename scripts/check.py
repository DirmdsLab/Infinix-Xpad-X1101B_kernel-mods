#!/usr/bin/env python3
"""Independent boot/configuration/vendor-module checks for one local build."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import re
import subprocess
import tarfile
import tempfile
import zlib


class XpadError(RuntimeError):
    """A failed input or compatibility check."""


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(args, *, capture=False):
    process = subprocess.run(args, text=True, capture_output=True)
    if process.returncode:
        raise XpadError(f"Command failed: {args}: {process.stderr.strip()}")
    return process.stdout


def norm(name: str) -> str:
    return Path(name).name.removesuffix(".ko").replace("-", "_")


def symbols(option: str, path: Path) -> list[tuple[str, int]]:
    result = []
    for line in run(["modprobe", option, str(path)], capture=True).splitlines():
        crc, name = line.split()
        result.append((name, int(crc, 16)))
    return result


def analyze(symvers: Path, loaded_file: Path, roots: list[Path], project: Path) -> dict:
    loaded = {
        norm(line.split()[0]) for line in loaded_file.read_text().splitlines() if line.strip()
    }
    if not loaded:
        raise XpadError("The loaded-module snapshot is empty")
    kernel, excluded = {}, 0
    for line in symvers.read_text().splitlines():
        fields = line.split()
        if len(fields) < 4:
            raise XpadError(f"Invalid Module.symvers entry: {line}")
        crc, symbol, provider = int(fields[0], 16), fields[1], fields[2]
        if provider == "vmlinux":
            if symbol in kernel and kernel[symbol] != crc:
                raise XpadError(f"Conflicting vmlinux CRC values: {symbol}")
            kernel[symbol] = crc
        else:
            excluded += 1
    if not kernel:
        raise XpadError("Module.symvers contains no vmlinux exports")
    builtin_file = symvers.parent / "modules.builtin"
    builtins = (
        {norm(x) for x in builtin_file.read_text().splitlines() if x.strip()}
        if builtin_file.exists()
        else set()
    )
    modules, imports = [], []
    exports, aliases, dependencies = defaultdict(list), defaultdict(set), defaultdict(set)
    without_exports = 0
    for root in roots:
        paths = sorted(root.rglob("*.ko"))
        if not paths:
            raise XpadError(f"No .ko files were found: {root}")
        for path in paths:
            name = norm(run(["modinfo", "-F", "name", str(path)], capture=True).strip())
            if not name:
                raise XpadError(f"Empty module name: {path}")
            versions = symbols("--dump-modversions", path)
            if not versions:
                raise XpadError(f"Missing import CRC records: {path}")
            deps = {
                norm(x.strip())
                for x in run(["modinfo", "-F", "depends", str(path)], capture=True)
                .strip()
                .split(",")
                if x.strip()
            }
            dependencies[name].update(deps)
            aliases[norm(path.name)].add(name)
            item = dict(
                name=name,
                path=str(path.relative_to(project)),
                sha256=sha256(path),
                depends=sorted(deps),
                vermagic=run(["modinfo", "-F", "vermagic", str(path)], capture=True).strip(),
            )
            modules.append(item)
            imports.append((item, versions))
            sections = run(["readelf", "-SW", str(path)], capture=True)
            if not re.search(r"\]\s+__ksymtab_strings\s", sections):
                if re.search(r"\]\s+__ksymtab(?:_gpl)?\s", sections):
                    raise XpadError(f"Export table without export strings: {path}")
                without_exports += 1
                continue
            for symbol, crc in symbols("--show-exports", path):
                exports[symbol].append(dict(module=name, path=item["path"], crc=crc))
    seen = {m["name"] for m in modules}
    load_lists, unresolved = {}, []
    for root in roots:
        for path in sorted(root.rglob("modules.load*")):
            entries = set()
            for line in path.read_text().splitlines():
                for token in line.partition("#")[0].split():
                    key = norm(token)
                    names = aliases.get(key, {key} if key in seen else set())
                    if len(names) != 1:
                        unresolved.append(
                            dict(
                                file=str(path.relative_to(project)),
                                entry=token,
                                candidates=sorted(names),
                            )
                        )
                    entries.update(names)
            load_lists[str(path.relative_to(project))] = sorted(entries)
    if not load_lists:
        raise XpadError("No modules.load lists were found")
    required = loaded | {n for names in load_lists.values() for n in names}
    pending = list(required)
    while pending:
        for dep in dependencies[pending.pop()]:
            if dep not in required:
                required.add(dep)
                pending.append(dep)
    findings, counts = [], Counter()
    for item, versions in imports:
        name = item["name"]
        for symbol, crc in versions:
            candidates = (
                [dict(module="vmlinux", crc=kernel[symbol])]
                if symbol in kernel
                else [p for p in exports.get(symbol, []) if p["module"] != name]
            )
            matching = [p for p in candidates if crc != 0 and p["crc"] == crc]
            if not candidates:
                result = "MISSING"
            elif matching:
                result = "KERNEL_MATCH" if symbol in kernel else "VENDOR_MATCH"
            elif crc == 0 or any(p["crc"] == 0 for p in candidates):
                result = "UNVERIFIED_CRC"
            else:
                result = "CRC_MISMATCH"
            counts[result] += 1
            reasons = [] if result in ("KERNEL_MATCH", "VENDOR_MATCH") else [result]
            if result == "VENDOR_MATCH":
                if name in loaded and not any(p["module"] in loaded for p in matching):
                    reasons.append("PROVIDER_NOT_ACTIVE")
                if len({(p["module"], p["crc"]) for p in candidates}) > 1:
                    reasons.append("AMBIGUOUS_VENDOR_PROVIDER")
            if reasons:
                findings.append(
                    dict(
                        module=name,
                        path=item["path"],
                        symbol=symbol,
                        import_crc=crc,
                        active=name in loaded,
                        required=name in required,
                        reasons=reasons,
                        candidates=candidates,
                    )
                )
    variants = defaultdict(set)
    for item in modules:
        variants[item["name"]].add(item["sha256"])
    conflicts = sorted(name for name, hashes in variants.items() if len(hashes) > 1)
    missing = sorted((loaded - seen) | (required - seen - builtins))
    blocked = bool(
        missing or unresolved or set(conflicts) & required or any(f["required"] for f in findings)
    )
    status = "BLOCKED" if blocked else "REVIEW" if findings or conflicts else "CLEAR"
    return dict(
        status=status,
        files=len(modules),
        unique=len(seen),
        active_total=len(loaded),
        active_covered=len(loaded & seen),
        missing_required=missing,
        unresolved_load_entries=unresolved,
        conflicting_module_names=conflicts,
        without_exports=without_exports,
        builtin_dependencies=sorted((required - seen) & builtins),
        excluded_non_vmlinux_exports=excluded,
        counts=dict(counts),
        load_lists=load_lists,
        modules=modules,
        findings=findings,
        symvers_sha256=sha256(symvers),
        loaded_sha256=sha256(loaded_file),
    )


def config_values(path):
    result = {}
    for line in path.read_text().splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            result[line[2:-11]] = "n"
    return result


def embedded_config(path):
    data = path.read_bytes()
    position = data.find(b"IKCFG_ST\x1f\x8b\x08")
    if position < 0:
        raise XpadError("Embedded IKCONFIG was not found in the kernel")
    stream = zlib.decompressobj(16 + zlib.MAX_WBITS)
    result = stream.decompress(data[position + 8 :]) + stream.flush()
    if not stream.eof or b"CONFIG_" not in result:
        raise XpadError("Embedded IKCONFIG is incomplete or invalid")
    return result


def unpack(tool, image, directory, *, vendor=False):
    directory.mkdir()
    process = subprocess.run(
        [str(tool), "unpack", str(image)],
        cwd=directory,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if process.returncode not in ((0, 3) if vendor else (0,)):
        raise XpadError(f"Cannot unpack {image.name}: {process.stdout}")
    if vendor and "VENDOR_BOOT_HDR" not in process.stdout:
        raise XpadError("device/vendor_boot.img is not a vendor boot image")
    return dict(re.findall(r"^([A-Z0-9_]+)\s+\[(.*)\]$", process.stdout, re.MULTILINE))


def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise XpadError(f"Unsafe archive path: {name}")


def extract_modules(root, temporary, tool):
    device = root / "device"
    vendor = temporary / "vendor"
    vendor.mkdir()
    archive = device / "modules.tar"
    with tarfile.open(archive) as handle:
        for entry in handle.getmembers():
            safe_name(entry.name)
            if entry.issym() or entry.islnk():
                safe_name(entry.linkname)
    run(["tar", "--no-same-owner", "-xf", str(archive), "-C", str(vendor)])
    boot = temporary / "vendor-boot"
    unpack(tool, device / "vendor_boot.img", boot, vendor=True)
    ramdisk = boot / "vendor_ramdisk/ramdisk.cpio"
    if not ramdisk.is_file():
        raise XpadError(
            "Expected one platform vendor_ramdisk/ramdisk.cpio; inspect other layouts manually"
        )
    with ramdisk.open("rb") as handle:
        listing = subprocess.run(
            ["cpio", "-it", "--quiet"],
            stdin=handle,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout
    for name in listing.splitlines():
        safe_name(name)
    extracted = boot / "modules"
    extracted.mkdir()
    with ramdisk.open("rb") as handle:
        subprocess.run(
            ["cpio", "-id", "--no-absolute-filenames", "--no-preserve-owner", "*.ko", "*modules.*"],
            cwd=extracted,
            stdin=handle,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
        )
    return [vendor / "modules", extracted]


def accept_module_report(report, known):
    if report["status"] == "CLEAR":
        return True
    if report["status"] != "REVIEW" or report["conflicting_module_names"]:
        return False
    grouped = defaultdict(Counter)
    for finding in report["findings"]:
        if finding["active"] or finding["required"]:
            return False
        grouped[finding["module"]].update(finding["reasons"])
    return (
        {name: dict(counts) for name, counts in grouped.items()} == known
        and not report["missing_required"]
        and not report["unresolved_load_entries"]
        and report["active_covered"] == report["active_total"]
    )


def inspect_build(root, image, expected_release, boot_size, requirements):
    build, device, tool = root / "build", root / "device", root / "tools/magiskboot"
    required_files = [
        image,
        build / "Image",
        build / "Module.symvers",
        build / "kernel.config",
        build / "kernel.release",
        tool,
        device / "boot.img",
        device / "magisk_boot.img",
        device / "vendor_boot.img",
        device / "modules.tar",
        device / "loaded_modules.txt",
    ]
    for path in required_files:
        if not path.is_file() or path.stat().st_size == 0:
            raise XpadError(f"Required file is missing or empty: {path}")
    if sha256(tool) != "21be0ef297957c74184f0632dcf7d54777f064e64c8418c7d6ee107b9ddb3ffe":
        raise XpadError("magiskboot differs from the pinned tested binary")
    release = (build / "kernel.release").read_text().strip()
    if expected_release and release != expected_release:
        raise XpadError(f"Kernel release mismatch: {release}; expected {expected_release}")
    kernel = (build / "Image").read_bytes()
    if len(kernel) < 64 or kernel[56:60] != b"ARM\x64":
        raise XpadError("build/Image is not a recognized ARM64 Image")
    if b"Linux version " + release.encode() + b" " not in kernel:
        raise XpadError("The Image banner differs from kernel.release")
    if embedded_config(build / "Image") != (build / "kernel.config").read_bytes():
        raise XpadError("The embedded configuration differs from build/kernel.config")
    values = config_values(build / "kernel.config")
    for assignment in ["CONFIG_MODVERSIONS=y", *requirements]:
        key, expected = assignment.split("=", 1)
        if values.get(key, "n") != expected:
            raise XpadError(
                f"Configuration mismatch: {key}; expected {expected}, got {values.get(key, 'n')}"
            )
    for path in (image, device / "boot.img", device / "magisk_boot.img"):
        if path.stat().st_size != boot_size:
            raise XpadError(f"Unexpected boot partition size: {path}")

    with tempfile.TemporaryDirectory(prefix="check-", dir=build) as directory:
        temporary = Path(directory)
        stock, template, result = temporary / "stock", temporary / "template", temporary / "result"
        stock_header = unpack(tool, device / "boot.img", stock)
        template_header = unpack(tool, device / "magisk_boot.img", template)
        result_header = unpack(tool, image, result)
        fields = (
            "HEADER_VER",
            "OS_VERSION",
            "OS_PATCH_LEVEL",
            "PAGESIZE",
            "CMDLINE",
            "KERNEL_FMT",
            "RAMDISK_FMT",
        )
        for field in fields:
            if field not in template_header or template_header[field] != result_header.get(field):
                raise XpadError(f"Repacked boot metadata changed: {field}")
            if stock_header.get(field) != template_header[field]:
                raise XpadError(f"Original and Magisk templates differ in metadata: {field}")
        if sha256(stock / "kernel") != sha256(template / "kernel"):
            raise XpadError(
                "Original and Magisk template kernels differ; provide a matched input pair"
            )
        status = subprocess.run(
            [str(tool), "cpio", str(template / "ramdisk.cpio"), "test"], capture_output=True
        ).returncode
        if status != 1:
            raise XpadError(
                f"The template ramdisk is not recognized as Magisk-patched: status {status}"
            )
        if sha256(result / "kernel") != sha256(build / "Image"):
            raise XpadError("The boot image contains a different kernel")
        for name in (
            "ramdisk.cpio",
            "kernel_dtb",
            "second",
            "dtb",
            "extra",
            "recovery_dtbo",
            "bootconfig",
        ):
            before, after = template / name, result / name
            if before.exists() or after.exists():
                if not before.exists() or not after.exists() or sha256(before) != sha256(after):
                    raise XpadError(f"Non-kernel component changed: {name}")
        if embedded_config(result / "kernel") != (build / "kernel.config").read_bytes():
            raise XpadError("Boot image embedded configuration mismatch")
        roots = extract_modules(root, temporary, tool)
        # analyze reads modules.builtin beside Module.symvers when available.
        module_report = analyze(
            build / "Module.symvers", device / "loaded_modules.txt", roots, root
        )
        known = json.loads((root / "patch/known-module-findings.json").read_text())
        accepted = accept_module_report(module_report, known)
        result_data = dict(
            status=(
                ("CHECK_PASS" if module_report["status"] == "CLEAR" else "CHECK_REVIEW")
                if accepted
                else "CHECK_FAIL"
            ),
            accepted=accepted,
            kernel_release=release,
            image_sha256=sha256(build / "Image"),
            symvers_sha256=sha256(build / "Module.symvers"),
            boot_sha256=sha256(image),
            boot_size=boot_size,
            boot_header=result_header,
            configuration_matches=True,
            kernel_matches=True,
            magisk_ramdisk_preserved=True,
            avb_signature_verified=False,
            device_boot_tested=False,
            modules=module_report,
            note="Known inactive-module findings remain REVIEW; a static pass is not a device boot test.",
        )
        return result_data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--expected-release", default="")
    parser.add_argument("--boot-size", type=int, default=67108864)
    parser.add_argument("--require", action="append", default=[])
    args = parser.parse_args(argv)
    root = args.root.resolve()
    (root / "build").mkdir(exist_ok=True)
    report_path = root / "build/check-report.json"
    try:
        report = inspect_build(
            root, args.image.resolve(), args.expected_release, args.boot_size, args.require
        )
    except (
        XpadError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
        tarfile.TarError,
        zlib.error,
    ) as error:
        report = dict(status="CHECK_FAIL", accepted=False, error=str(error))
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("STATUS:", report["status"])
    if not report["accepted"]:
        print("ERROR:", report.get("error", "Unreviewed module compatibility findings"))
    if "modules" in report:
        mod = report["modules"]
        print("Module files / unique names:", mod["files"], "/", mod["unique"])
        print("Active coverage:", mod["active_covered"], "/", mod["active_total"])
        print("CRC references:", mod["counts"])
        print("Missing required modules:", mod["missing_required"])
        print("Unresolved load entries:", mod["unresolved_load_entries"])
    if report["accepted"]:
        print("Kernel:", report["kernel_release"])
        print("Image SHA-256:", report["image_sha256"])
        print("Boot SHA-256:", report["boot_sha256"])
    print("Full report:", report_path)
    return 0 if report["status"] == "CHECK_PASS" else 2 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
