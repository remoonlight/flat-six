"""Create a fresh pinned PT3G support copy. No service, driver or device calls."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path

from .pt3g import ACTIVE_CONFIG, SUPPORT_SHA256, verify_vendor_files

ROOT = Path(__file__).resolve().parents[2]
SOURCE_INI_SHA256 = 'b895d84e9bf62f035d90d1e9c28ca3375d0ae692f43db0fe784c74d140b0e6e6'
CONFIG_FILES = ('PDU_VCI.ini', ACTIVE_CONFIG)


def _read(file):
    if file.is_symlink() or not file.is_file():
        raise ValueError('pt3g-support-source-file-invalid:' + file.name)
    # Every retained source has a fixed pin and is below 4 MiB.
    if file.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('pt3g-support-source-size-limit:' + file.name)
    return file.read_bytes()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _private_output(output):
    roots = [ROOT / '.local']
    if os.environ.get('LOCALAPPDATA'):
        roots.append(Path(os.environ['LOCALAPPDATA']) / 'FlatSixPrecar')
    if not any(output.is_relative_to(root.resolve()) and output != root.resolve() for root in roots):
        raise ValueError('pt3g-support-output-must-be-private')


def prepare_support(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    _private_output(output)
    if output.exists():
        raise ValueError('pt3g-support-output-already-exists')
    # Check all source bytes before creating output; do not copy arbitrary extras.
    selected = {}
    for name, pin in SUPPORT_SHA256.items():
        if name == ACTIVE_CONFIG and not (source / name).exists():
            continue
        raw = _read(source / name)
        allowed = {pin, SOURCE_INI_SHA256} if name in CONFIG_FILES else {pin}
        if _sha(raw) not in allowed:
            raise ValueError('pt3g-support-source-not-qualified:' + name)
        selected[name] = raw
    original = selected['PDU_VCI.ini']
    configured = original.replace(b'15765StartDetect=1', b'15765StartDetect=0')
    if _sha(configured) != SUPPORT_SHA256['PDU_VCI.ini']:
        raise ValueError('pt3g-support-disabled-config-not-qualified')
    copied = {name: raw for name, raw in selected.items() if name not in CONFIG_FILES}
    copied.update({name: configured for name in CONFIG_FILES})
    output.mkdir(parents=True, exist_ok=False)
    driver = output / 'X64'; driver.mkdir()
    # Preserve complete original configurations separately from the loaded copy.
    backups = output / 'source-config'; backups.mkdir()
    for name in CONFIG_FILES:
        if name in selected:
            (backups / name).write_bytes(selected[name])
    rows = []
    for name, raw in copied.items():
        destination = driver / name
        with destination.open('xb') as file:
            file.write(raw)
        rows.append({'file': name, 'bytes': len(raw), 'sha256': _sha(raw)})
    verify_vendor_files(driver)
    for name, raw in selected.items():
        if _sha(_read(source / name)) != _sha(raw):
            raise ValueError('pt3g-support-source-changed:' + name)
    result = {'ok': True, 'kind': 'isolated-pt3g-support', 'source': str(source), 'root': str(driver),
              'files': rows, 'activeConfig': ACTIVE_CONFIG, 'sourceConfigSha256': _sha(original),
              'automaticProtocolDetectionDisabled': True, 'sourceUnchanged': True,
              'driverLoaded': False, 'serviceChanged': False, 'deviceOpened': False,
              'nativeRuntimeVerified': False, 'vehicleRequestsSent': 0, 'vehicleTransportQualified': False}
    (output / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare_support(args.source, args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
