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


def _document(path):
    raw = path.read_bytes()
    if len(raw) > 4 * 1024 * 1024 or b'<!ENTITY' in raw or b'<!DOCTYPE' in raw:
        raise ValueError('unsafe-or-oversized-MDF')
    return ET.fromstring(raw)


def resources(path):
    document = _document(path)
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


def protocol_contracts(path):
    """Resolve source object/parameter references; do not apply driver settings."""
    document = _document(path)
    objects = {}
    for element in document.iter():
        eid = element.get('EID')
        if not eid:
            continue
        if eid in objects:
            raise ValueError('duplicate-MDF-object')
        objects[eid] = element
    wanted = {'ISO_14230_3_on_ISO_15765_2', 'ISO_15765_3_on_ISO_15765_2', 'ISO_OBD_on_ISO_15765_4'}
    contracts = []
    for element in document.iter('PROTOCOL'):
        name = element.findtext('SHORT_NAME')
        if name not in wanted:
            continue
        parameters, unresolved = [], []
        for ref in element.findall('COMPARAM_REF'):
            link = ref.find('COMPARAM')
            eid = link.get('IDREF') if link is not None else None
            target = objects.get(eid)
            if target is None or target.tag != 'COMPARAM':
                unresolved.append(eid)
                continue
            parameters.append({'name': target.findtext('SHORT_NAME'), 'id': target.findtext('ID'),
                'dataType': target.findtext('DATA_TYPE'), 'class': target.findtext('CLASS'),
                'layer': target.findtext('LAYER'),
                'defaultValue': ref.findtext('DEFAULT_VALUE', target.findtext('DEFAULT_VALUE')),
                'minimum': ref.findtext('MIN_VALUE'), 'maximum': ref.findtext('MAX_VALUE')})
        contracts.append({'protocol': name, 'id': element.findtext('ID'), 'parameters': parameters,
                          'unresolvedReferences': unresolved, 'sourceDeclaredOnly': True,
                          'runtimeAbiVerified': False, 'executionEnabled': False})
    return {'protocols': contracts, 'missingProtocols': sorted(wanted - {p['protocol'] for p in contracts})}


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
    contracts = protocol_contracts(driver / 'MDF_VCI.xml') if (driver / 'MDF_VCI.xml').is_file() else None
    target = [r for r in rows if r['busRef'] == 'ID_ISO_11898_2_DWCAN' and
              {p['pin'] for p in r['pins']} == {'6', '14'}]
    definitions = json.loads(reference.read_text(encoding='utf-8'))
    signals = [r for r in definitions['signals'] if any('981' in m or '982' in m for m in r.get('models', []))]
    limitations = definitions.get('parser', {}).get('limitations', [])
    from .vbox_ref import candidate_layout, REFERENCES
    layouts, layout_errors = [], []
    for signal in signals:
        if signal['sourceId'] not in ('vbox-ref-boxster-981', 'vbox-ref-cayman-981'):
            continue
        try:
            layouts.append(candidate_layout(signal))
        except ValueError as error:
            layout_errors.append({'sourceId': signal['sourceId'], 'name': signal.get('signalName'), 'error': str(error)})
    return {'ok': True, 'kind': 'offline-adapter-preparation', 'noDeviceIO': True, 'driverLoaded': False,
        'vehicleVerified': False, 'pt3g': {'sources': files, 'missingFiles': absent,
            'archiveComparisons': comparisons, 'automaticProtocolDetectionDisabled': detections == ['0'],
            'sourceResourceCount': len(rows), 'diagnosticPinCandidates': target, 'executionEnabled': False,
            'diagnosticProtocolContracts': contracts,
            'gaps': ['current-hardware-and-firmware-binding', 'runtime-D-PDU-object-and-parameter-application',
                     'no-spontaneous-traffic-on-link-creation', 'cancel/timeout/close-native-behavior',
                     'independent-ECU-read-and-response', 'physical-silent-receive-qualification']},
        'internalCan': {'source': source(reference), 'targetCandidateRows': len(signals),
            'sourceCounts': dict(Counter(r['sourceId'] for r in signals)), 'signals': signals,
            'vboxNumberingReferences': REFERENCES, 'vboxCandidateLayouts': layouts,
            'vboxLayoutErrors': layout_errors,
            'sourceLimitations': limitations, 'runtimeDecodeEnabled': False,
            'physicalSilenceVerified': False, 'gaps': ['physical-network/pin/bitrate-qualification',
                'REF-column-binding-and-model-applicability', 'independent-reference-capture',
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
