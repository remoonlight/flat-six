"""List or restore a verified historical OBD runtime into a fresh .local folder."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--list', action='store_true')
    group.add_argument('--package')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    with zipfile.ZipFile(args.archive) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('history-duplicate-entry')
        index = json.loads(archive.read('index.json'))
        if index.get('kind') != 'verified-obd-runtime-history':
            raise ValueError('history-kind-unreviewed')
        if args.list:
            if args.output:
                raise ValueError('list-does-not-create-output')
            print(json.dumps([r['path'] for r in index['packages']], ensure_ascii=False, indent=2))
            return
        matches = [r for r in index['packages'] if r['path'] == args.package]
        if len(matches) != 1 or not args.output:
            raise ValueError('exact-historical-package-and-fresh-output-required')
        destination = args.output.absolute()
        local = Path(__file__).resolve().parents[1] / '.local'
        if destination.exists() or not destination.resolve().is_relative_to(local) or destination == local:
            raise ValueError('fresh-local-output-required')
        row = matches[0]
        original = archive.read(row['manifestEntry'])
        if hashlib.sha256(original).hexdigest() != row['manifestSha256']:
            raise ValueError('history-manifest-integrity-failed')
        manifest = json.loads(original)
        files = manifest['files'] + row.get('extraFiles', [])
        seen = set()
        for file in files:
            path = Path(file['path'])
            if (path.is_absolute() or '..' in path.parts or '\\' in file['path'] or file['path'] in seen
                    or file['path'] == 'delivery-manifest.json'
                    or not re.fullmatch(r'[0-9a-f]{64}', file['sha256'])
                    or type(file['bytes']) is not int or file['bytes'] < 0):
                raise ValueError('history-file-path-invalid')
            seen.add(file['path'])
            info = archive.getinfo('objects/' + file['sha256'])
            if info.file_size != file['bytes']:
                raise ValueError('history-file-size-different')
        destination.mkdir()
        try:
            for file in files:
                target = destination / file['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open('objects/' + file['sha256']) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                with target.open('rb') as source:
                    if hashlib.file_digest(source, 'sha256').hexdigest() != file['sha256']:
                        raise ValueError('history-file-integrity-failed')
            (destination / 'delivery-manifest.json').write_bytes(original)
        except Exception:
            # This function created the exclusive destination; no existing files are removed.
            shutil.rmtree(destination)
            raise
        print(json.dumps(dict(ok=True, files=len(files), output=str(destination), runtimeStarted=False)))


if __name__ == '__main__':
    main()
