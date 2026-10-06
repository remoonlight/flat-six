"""Offline installed-userData backup; restore only to a new directory.

Close Flat Six before use. Copies only business data, not Electron caches or
external exports. No device access; never replaces an existing user directory.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
from uuid import uuid4

from .hashutil import sha256_file

ROOTS = {'garage.db', 'diagnostics', 'diagnostic-library', 'local-assets'}


def plain_path(value):
    path = Path(value).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError('linked-path-not-allowed')
    return path.resolve()


def inventory(root):
    result = []
    for name in sorted(ROOTS):
        item = root / name
        if item.is_symlink() or item.is_junction():
            raise ValueError('linked-path-not-allowed')
        if not item.exists():
            continue
        paths = [item] if item.is_file() else sorted(item.rglob('*'))
        for path in paths:
            plain_path(path)
            if path.is_file():
                result.append({'file': path.relative_to(root).as_posix(),
                               'bytes': path.stat().st_size, 'sha256': sha256_file(path)})
    return result


def integrity(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('database-integrity-failed')


def target_stage(destination, source):
    destination = plain_path(destination)
    if destination.exists():
        raise ValueError('destination-already-exists')
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('source-destination-overlap')
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.with_name(destination.name + '.pending-' + uuid4().hex)
    stage.mkdir()
    return destination, stage


def copy_files(source, target, entries):
    for entry in entries:
        old, new = source / entry['file'], target / entry['file']
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(old, new)


def backup(user_data, output, *, app_closed=False):
    if not app_closed:
        raise ValueError('close-application-first')
    source = plain_path(user_data)
    if not source.is_dir() or not (source / 'garage.db').is_file():
        raise ValueError('installed-user-data-required')
    # Closed SQLite connections checkpoint WAL. Refuse a potentially live DB.
    for suffix in ('-wal', '-journal'):
        pending = source / ('garage.db' + suffix)
        plain_path(pending)
        if pending.exists() and pending.stat().st_size:
            raise ValueError('database-WAL-or-journal-present-close-application-first')
    before = inventory(source)
    destination, stage = target_stage(output, source)
    data = stage / 'data'; data.mkdir()
    copy_files(source, data, before)
    if inventory(data) != before or inventory(source) != before:
        raise ValueError('user-data-changed-during-backup')
    integrity(data / 'garage.db')
    manifest = {'schemaVersion': 1, 'kind': 'flat-six-user-data-backup',
        'createdUtc': datetime.now(timezone.utc).isoformat(), 'applicationClosedDeclared': True,
        'source': str(source), 'roots': sorted(ROOTS), 'files': before,
        'databaseIntegrity': 'ok', 'noDeviceIO': True, 'vehicleVerified': False,
        'externalExportsIncluded': False, 'electronCachesIncluded': False}
    (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    stage.rename(destination)
    return {'ok': True, 'output': str(destination), 'files': len(before),
            'databaseIntegrity': 'ok', 'noDeviceIO': True}


def verified_backup(source):
    manifest_path = source / 'manifest.json'
    plain_path(manifest_path)
    doc = json.loads(manifest_path.read_text(encoding='utf-8'))
    if doc.get('schemaVersion') != 1 or doc.get('kind') != 'flat-six-user-data-backup':
        raise ValueError('invalid-backup-manifest')
    entries = doc.get('files')
    if not isinstance(entries, list) or not entries:
        raise ValueError('invalid-backup-files')
    seen = set()
    for entry in entries:
        name = entry.get('file', '')
        parts = PurePosixPath(name).parts
        if (not parts or parts[0] not in ROOTS or PurePosixPath(name).is_absolute()
                or any(p in ('.', '..') or ':' in p or '\\' in p for p in parts)
                or PurePosixPath(name).as_posix() != name
                or (parts[0] == 'garage.db' and len(parts) != 1)):
            raise ValueError('invalid-backup-path')
        if name.casefold() in seen:
            raise ValueError('duplicate-backup-path')
        seen.add(name.casefold())
    data = plain_path(source / 'data')
    if inventory(data) != entries or 'garage.db' not in seen:
        raise ValueError('backup-content-mismatch')
    integrity(data / 'garage.db')
    return data, entries


def restore(backup_dir, user_data, *, app_closed=False):
    if not app_closed:
        raise ValueError('close-application-first')
    source = plain_path(backup_dir)
    data, entries = verified_backup(source)
    destination, stage = target_stage(user_data, source)
    copy_files(data, stage, entries)
    if inventory(stage) != entries:
        raise ValueError('restored-content-mismatch')
    # Check again after copying so a changing/tampered backup is not published.
    verified_backup(source)
    integrity(stage / 'garage.db')
    stage.rename(destination)
    return {'ok': True, 'output': str(destination), 'files': len(entries),
            'databaseIntegrity': 'ok', 'noDeviceIO': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    save = commands.add_parser('backup')
    save.add_argument('--user-data', type=Path, required=True)
    save.add_argument('--output-dir', type=Path, required=True)
    load = commands.add_parser('restore')
    load.add_argument('--backup-dir', type=Path, required=True)
    load.add_argument('--user-data', type=Path, required=True)
    for command in (save, load):
        command.add_argument('--app-closed', action='store_true', required=True)
    args = parser.parse_args()
    result = (backup(args.user_data, args.output_dir, app_closed=args.app_closed) if args.action == 'backup'
              else restore(args.backup_dir, args.user_data, app_closed=args.app_closed))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
