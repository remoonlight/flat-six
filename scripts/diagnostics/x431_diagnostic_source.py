"""Exact SYS_DATA name/link references for offline collection; no transport.

Command order is source table order, never a chosen runtime DTC procedure.
Communication fields stay source candidates, without an inferred CAN pair.
"""
from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path

from scripts.x431_re.sysdata import parse_payload
from .x431_version_response import SYS_DATA_LOGICAL_SHA256
from .offline_match import FLAGS, verify_source

GUARDS = {**FLAGS, 'vehicleVerified': False, 'ecuVersionQualified': False,
          'diagnosticsQualified': False, 'selectedLinkIndex': None, 'selectedPairIndex': None}


def _commands(rows):
    return [{'index': i, 'sourceOffset': row.get('off') if row else None,
             'tableHex': row.get('tableHex') if row else None,
             'wireCandidateHex': row.get('wireHex') if row else None,
             'commandFormat': row.get('commandFormat') if row else None,
             'nativeDescriptorHexCandidate': row.get('nativeDescriptorHexCandidate') if row else None,
             'runtimeReadSequenceQualified': False,
             'role': row.get('role') if row else None,
             'class': row.get('class') if row else 'missing-source-pointer',
             'reason': row.get('reason') if row else 'null-command-pointer', **FLAGS}
            for i, row in enumerate(rows)]


def source_reference(system, name):
    """Preserve all matches, including identical DTC tables on different links."""
    links = []
    for link in (system or {}).get('links', []):
        service = link['service']
        for index, variant in enumerate(service['variants']):
            if variant['name'] != name:
                continue
            comm = link['comm']; dtc = variant.get('dtc')
            raw_reads = (dtc or {}).get('read', [])
            reads = _commands(raw_reads)
            gaps = ['fresh-complete-identity/version', 'actual-link/pair-selection',
                    'adapter-initialization-and-firmware', 'complete-request/response-and-close']
            if not dtc:
                gaps.append('dtc-table-missing')
            elif not reads:
                gaps.append('dtc-read-command-table-empty')
            if any(row['wireCandidateHex'] is None for row in reads):
                gaps.append('dtc-read-command-layout-unresolved')
            if comm.get('class') != 'static-source-confirmed':
                gaps.append('communication-layout-unresolved')
            if comm.get('consumedEnd') != link.get('serviceOff'):
                gaps.append('communication-record-trailing-bytes-unresolved')
            links.append({'linkIndex': link['index'], 'serviceVariantIndex': index,
                'communicationSourceOffset': link['commOff'], 'serviceSourceOffset': link['serviceOff'],
                'sourceSlots': variant['slots'], 'dtcSourceOffset': variant.get('dtcTableOff'),
                'communication': {'class': comm.get('class'), 'modeByte': comm.get('protocolKind'),
                    'pairCount': comm.get('pairCount'), 'pairs': [
                        {'pairIndex': i, 'storedTx': pair['storedTx'], 'storedRx': pair['storedRx'],
                         'subtypeByte': pair['mode'], 'parameterIds': pair['params'],
                         'derived11bitCandidate': pair.get('derived11bit'),
                         'hardwareConversionQualified': False, **FLAGS}
                        for i, pair in enumerate(comm.get('pairs', []))],
                    'reason': comm.get('reason'), 'actualPairSelected': False},
                'dtc': {'styleByte': (dtc or {}).get('style'), 'metadataHex': (dtc or {}).get('metadataHex'),
                    'readCommandSlots': reads, 'clearCommandSlots': _commands((dtc or {}).get('clear', [])),
                    'clearTypeByte': (dtc or {}).get('clearType'),
                    'otherPointers': (dtc or {}).get('otherPointers', []),
                    'expectedResponseHex': None, 'runtimeReadSequenceQualified': False,
                    'countBasis': 'ordered source pointer slots, not verified executed commands'},
                'qualificationGaps': gaps, **GUARDS})
    status = 'source-name-missing' if not links else 'exact-source-name-match' if len(links) == 1 else 'source-link-ambiguous'
    return {'kind': 'x431-offline-diagnostic-source-reference', 'variantName': name,
        'systemKey': (system or {}).get('systemKey'), 'linkMatchCount': len(links),
        'bindingStatus': status, 'links': links, 'fallbackUsed': False, **GUARDS}


class DiagnosticSource:
    def __init__(self, path):
        self.path = Path(path)
        self.evidence = verify_source(self.path, SYS_DATA_LOGICAL_SHA256)
        blob = self.path.read_bytes()
        # Check the actual bytes used by the parser, not only a prior file read.
        if hashlib.sha256(blob).hexdigest() != SYS_DATA_LOGICAL_SHA256:
            raise ValueError('diagnostic-source-changed-before-parse')
        self.logical_blob = blob
        self.parsed = parse_payload(blob)
        if not self.parsed.get('ok') or self.parsed['familyIdQueried'] != 13:
            raise ValueError('diagnostic-source-family-unresolved')
        self.by_ecu = self.parsed['byEcuId']

    def reference(self, ecu_id, name):
        if type(ecu_id) is not int or not isinstance(name, str) or not name:
            raise ValueError('diagnostic-source-selector-invalid')
        return {**source_reference(self.by_ecu.get(ecu_id), name), 'requestedEcuId': ecu_id,
                'sourceSha256': self.evidence['sha256']}

    def verify_unchanged(self):
        return verify_source(self.path, self.evidence['sha256'])

    def summary(self):
        return {'source': self.evidence, 'familyId': 13,
            'systemCount': self.parsed['systemCount'], 'linkCount': self.parsed['linkCount'],
            'modeCounts': dict(Counter(f"{link['comm']['protocolKind']:02X}"
                for system in self.parsed['systems'] for link in system['links'])),
            'vehicleGenerationSelected': None, **GUARDS}
