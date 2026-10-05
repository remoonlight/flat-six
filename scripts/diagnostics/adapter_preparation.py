"""Offline PT3G resource and internal-CAN candidate audit. Never loads a driver."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]


def source(path):
    raw = path.read_bytes()
    return {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def resources(path):
    raw = path.read_bytes()
    if len(raw) > 4 * 1024 * 1024 or b'<!ENTITY' in raw or b'<!DOCTYPE' in raw:
        raise ValueError('unsafe-or-oversized-MDF')
    document = ET.fromstring(raw)
    seen, result = set(), []
    for item in document.iter('RESOURCE'):
        name = item.findtext('SHORT_NAME')
        if not name:
            continue
        if name in seen:
            raise ValueError('duplicate-MDF-resource')
        seen.add(name)
        bus, protocol = item.find('BUSTYPE'), item.find('PROTOCOL')
        pins = [{'pin': pin.findtext('PIN_ON_MODULE'), 'typeRef': pin.find('PINTYPE').get('IDREF')}
                for pin in item.findall('PIN_ON_MODULE') if pin.find('PINTYPE') is not None]
        result.append({'name': name, 'sourceId': item.findtext('ID'),
            'busRef': bus.get('IDREF') if bus is not None else None,
            'protocolRef': protocol.get('IDREF') if protocol is not None else None, 'pins': pins,
            'sourceDeclaredOnly': True, 'executionEnabled': False, 'silentReceiveQualified': False})
    return result


def audit(driver, reference):
    required = ('PDU_VCI.dll', 'pdu2.dll', 'db.dll', 'Marstool564.dll', 'PassthruManage.dll', 'MDF_VCI.xml', 'PDU_VCI.ini')
    absent = [name for name in required if not (driver / name).is_file()]
    files = [source(driver / name) for name in required if (driver / name).is_file()]
    archived = ROOT / '.local/pt3g-support/vendor/PORSCHE-VCI/X64'
    comparisons = [{'file': name, 'archivePresent': (archived / name).is_file(),
                    'matchesArchive': source(driver / name)['sha256'] == source(archived / name)['sha256']
                      if (driver / name).is_file() and (archived / name).is_file() else None}
                   for name in required if name != 'PDU_VCI.ini']
    ini = (driver / 'PDU_VCI.ini').read_text(encoding='utf-8-sig') if (driver / 'PDU_VCI.ini').is_file() else ''
    detections = re.findall(r'^\s*15765StartDetect\s*=\s*(\d+)\s*$', ini, re.M)
    rows = resources(driver / 'MDF_VCI.xml') if (driver / 'MDF_VCI.xml').is_file() else []
    target = [r for r in rows if r['busRef'] == 'ID_ISO_11898_2_DWCAN' and
              {p['pin'] for p in r['pins']} == {'6', '14'}]
    definitions = json.loads(reference.read_text(encoding='utf-8'))
    signals = [r for r in definitions['signals'] if any('981' in m or '982' in m for m in r.get('models', []))]
    limitations = definitions.get('parser', {}).get('limitations', [])
    return {'ok': True, 'kind': 'offline-adapter-preparation', 'noDeviceIO': True, 'driverLoaded': False,
        'vehicleVerified': False, 'pt3g': {'sources': files, 'missingFiles': absent,
            'archiveComparisons': comparisons, 'automaticProtocolDetectionDisabled': detections == ['0'],
            'sourceResourceCount': len(rows), 'diagnosticPinCandidates': target, 'executionEnabled': False,
            'gaps': ['current-hardware-and-firmware-binding', 'D-PDU-object-and-parameter-contract',
                     'no-spontaneous-traffic-on-link-creation', 'cancel/timeout/close-native-behavior',
                     'independent-ECU-read-and-response', 'physical-silent-receive-qualification']},
        'internalCan': {'source': source(reference), 'targetCandidateRows': len(signals),
            'sourceCounts': dict(Counter(r['sourceId'] for r in signals)), 'signals': signals,
            'sourceLimitations': limitations, 'runtimeDecodeEnabled': False,
            'physicalSilenceVerified': False, 'gaps': ['physical-network/pin/bitrate-qualification',
                'Motorola-bit-numbering-and-source-conflicts', 'independent-reference-capture',
                'exact-target-model/version-applicability']}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver-root', type=Path, default=Path('C:/ProgramData/PORSCHE-VCI/X64'))
    parser.add_argument('--reference', type=Path, default=ROOT / 'docs/research/can-data/internal/signals.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise ValueError('output-already-exists')
    result = audit(args.driver_root, args.reference)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': True, 'noDeviceIO': True, 'PT3GResources': result['pt3g']['sourceResourceCount'],
        'diagnosticPinCandidates': len(result['pt3g']['diagnosticPinCandidates']),
        'internalCandidateRows': result['internalCan']['targetCandidateRows'], 'executionEnabled': False}))


if __name__ == '__main__': main()
