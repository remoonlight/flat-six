"""Portable local definitions. No raw captures, backups, firmware or executables.

Derived replay values and source metadata may be private; export is explicit.
Hash integrity never promotes an offline definition to a live qualification.
"""
from __future__ import annotations
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import zipfile

ROOT = Path(os.environ.get('PORSCHE981_DEFINITION_ROOT') or Path(__file__).resolve().parents[2])
SEEDS = ("data/seed/diagnostics", "data/seed/obd", "data/seed/x431/plaintext-archive", "data/seed/piwis",
    "data/seed/coding-guide", "data/seed/diagnostic-reference-981")
LOCAL = (".local/diagnostics/realtime-preparation", ".local/diagnostics/piwis-workshop",
         ".local/diagnostics/x431-source-references")
VARIANTS = ".local/x431-re/2026-09-27-981982/expansion/variants.jsonl"
LANGUAGE = ".local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP"
EXTENSIONS = {".json", ".jsonl", ".xml", ".ini", ".txt", ".md", ".csv", ".htm", ".html"}
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_FILE = 512 * 1024 * 1024
MAX_FILES = 25000

def allowed(name):
    p = PurePosixPath(name)
    return (isinstance(name, str) and len(name) <= 300 and "\\" not in name and ":" not in name
        and not p.is_absolute() and ".." not in p.parts and str(p) == name
        and (name in (VARIANTS, LANGUAGE) or any(name.startswith(prefix + "/") for prefix in SEEDS + LOCAL))
        and (name == LANGUAGE or p.suffix.lower() in EXTENSIONS))

def digest(file):
    with Path(file).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()

def files(root):
    selected = []
    for prefix in SEEDS + LOCAL:
        directory = root / prefix
        if not directory.exists():
            continue
        for file in sorted(directory.rglob("*")):
            name = file.relative_to(root).as_posix()
            if file.is_file() and allowed(name):
                if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
                    raise ValueError("definition-source-outside-root")
                selected.append((name, file))
    for name in (VARIANTS, LANGUAGE):
        if (root / name).is_file():
            if (root / name).is_symlink() or not (root / name).resolve().is_relative_to(root.resolve()):
                raise ValueError("definition-source-outside-root")
            selected.append((name, root / name))
    return selected

def export_bundle(destination, root=ROOT):
    root = Path(root); selected = files(root)
    if not selected or len(selected) > MAX_FILES:
        raise ValueError("definition-count-invalid")
    total = sum(file.stat().st_size for _, file in selected)
    if total > MAX_TOTAL or any(file.stat().st_size > MAX_FILE for _, file in selected):
        raise ValueError("definition-size-limit")
    manifest = {"schemaVersion": 1, "kind": "local-diagnostic-definitions", "liveVerified": False,
        "containsDerivedSamples": True, "files": [{"name": name, "bytes": file.stat().st_size, "sha256": digest(file)} for name, file in selected]}
    # Exclusive destination; a failed export removes only the file opened here.
    created = False
    try:
        with open(destination, "xb") as target:
            created = True
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
                for row, (_, file) in zip(manifest["files"], selected, strict=True):
                    archive.write(file, row["name"])
                    if file.stat().st_size != row["bytes"] or digest(file) != row["sha256"]:
                        raise ValueError("definition-source-changed")
        return {"ok": True, "files": len(selected), "bytes": total, "liveVerified": False}
    except Exception:
        if created:
            Path(destination).unlink(missing_ok=True)
        raise

class BundleSpaceError(ValueError):
    def __init__(self, available, required):
        super().__init__("bundle-insufficient-space")
        self.available_bytes = available
        self.required_payload_bytes = required


def import_bundle(source, root=ROOT):
    root = Path(root)
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if len(names) > MAX_FILES + 1 or len(set(names)) != len(names) or "manifest.json" not in names:
            raise ValueError("bundle-entries-invalid")
        if archive.getinfo("manifest.json").file_size > 8 * 1024 * 1024:
            raise ValueError("bundle-manifest-limit")
        manifest = json.loads(archive.read("manifest.json"))
        rows = manifest.get("files")
        if manifest.get("schemaVersion") != 1 or manifest.get("kind") != "local-diagnostic-definitions" or not isinstance(rows, list) or not rows:
            raise ValueError("bundle-manifest-invalid")
        expected = [row.get("name") for row in rows]
        if len(set(expected)) != len(expected) or set(names) != {"manifest.json", *expected} or not all(allowed(name) for name in expected):
            raise ValueError("bundle-path-invalid")
        if any(type(row.get("bytes")) is not int or not 0 <= row["bytes"] <= MAX_FILE for row in rows) or sum(row["bytes"] for row in rows) > MAX_TOTAL:
            raise ValueError("bundle-size-limit")
        entries = []
        for row in rows:
            name = row["name"]; target = root / name
            if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
                raise ValueError("bundle-target-outside-root")
            info = archive.getinfo(name)
            if info.file_size != row["bytes"] or info.flag_bits & 1 or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("bundle-entry-invalid")
            existing = target.exists()
            if existing and (not target.is_file() or digest(target) != row["sha256"]):
                raise ValueError("bundle-existing-file-conflict:" + name)
            entries.append((row, info, target, existing))
        required = sum(row["bytes"] for row, _, _, existing in entries if not existing)
        new_count = sum(not existing for _, _, _, existing in entries)
        if new_count:
            probe = root
            while not probe.exists():
                probe = probe.parent
            free = shutil.disk_usage(probe).free
            if free < required:
                raise BundleSpaceError(free, required)
        # Identical installed entries are still read and hashed from the ZIP.
        # They need no staging file; never skip archive integrity verification.
        with ExitStack() as stack:
            temporary = None
            if new_count:
                staging_parent = root / ".local" / "diagnostics" / "bundle-staging"
                staging_parent.mkdir(parents=True, exist_ok=True)
                temporary = Path(stack.enter_context(tempfile.TemporaryDirectory(dir=staging_parent)))
            new = []
            for row, info, target, existing in entries:
                staged = None if existing else temporary / row["name"]
                if staged is not None:
                    staged.parent.mkdir(parents=True, exist_ok=True)
                count = 0; hasher = hashlib.sha256()
                with archive.open(info) as incoming, ExitStack() as entry_stack:
                    outgoing = entry_stack.enter_context(staged.open("xb")) if staged is not None else None
                    while chunk := incoming.read(1024 * 1024):
                        count += len(chunk)
                        if count > row["bytes"]:
                            raise ValueError("bundle-entry-size-invalid")
                        hasher.update(chunk)
                        if outgoing is not None:
                            outgoing.write(chunk)
                if count != row["bytes"] or hasher.hexdigest() != row.get("sha256"):
                    raise ValueError("bundle-integrity-failed")
                if existing:
                    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
                        raise ValueError("bundle-target-outside-root")
                    if not target.is_file() or digest(target) != row["sha256"]:
                        raise ValueError("bundle-existing-file-conflict:" + row["name"])
                else:
                    # Re-read staged bytes so successful incoming hashing cannot
                    # hide a changed/damaged staging file before publication.
                    if digest(staged) != row["sha256"]:
                        raise ValueError("bundle-integrity-failed")
                    new.append((staged, target))
            published = []
            try:
                for staged, target in new:
                    if not target.resolve().is_relative_to(root.resolve()):
                        raise ValueError("bundle-target-outside-root")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.link(staged, target)
                    published.append(target)
            except Exception:
                for target in published:
                    target.unlink()
                raise
            return {"ok": True, "files": len(rows), "imported": len(new), "unchanged": len(rows) - len(new),
                    "stagedPayloadBytes": required, "liveVerified": False}

def main():
    request = json.loads(sys.stdin.read(8192))
    try:
        if set(request) != {"action", "file"} or request["action"] not in ("export", "import"):
            raise ValueError("bundle-request-invalid")
        result = export_bundle(request["file"]) if request["action"] == "export" else import_bundle(request["file"])
    except Exception as error:
        code = {28: "ENOSPC", 13: "EACCES", 1: "EPERM"}.get(error.errno) if isinstance(error, OSError) else None
        result = {"ok": False, "error": code or str(error), "liveVerified": False}
        if isinstance(error, BundleSpaceError):
            result.update(availableBytes=error.available_bytes, requiredPayloadBytes=error.required_payload_bytes)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 2

if __name__ == "__main__":
    raise SystemExit(main())
