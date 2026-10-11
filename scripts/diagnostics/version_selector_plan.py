"""Join DSN selectors to exact SYS_DATA/COMMAND read occurrences, offline only.

Command IDs, descriptors and wire read candidates are different source fields.
No actual route/link/pair, postprocessed heap, native map or vehicle is inferred.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .offline_match import FLAGS, verify_source
from .x431_variant_selection import REVIEWED_LANGUAGE_SHA256, audit_version_vectors
from .x431_command_origin import _lookup_command, audit_command_postprocessing, audit_enter_sys_command_caller
from .selector_source_gap import SelectorReadSourceGap

GUARDS = {**FLAGS, 'vehicleVerified': False, 'ecuVersionQualified': False,
          'actualDsnRouteSelectionQualified': False, 'runtimeSourcePathQualified': False,
          'selectedVehicleVariant': None, 'selectedLinkIndex': None, 'selectedPairIndex': None,
          'nativeResponseMapOriginProven': False}


class VersionSelectorSource:
    def __init__(self, diagnostic_source, version_source, language_path):
        self.diagnostic = diagnostic_source
        self.version = version_source
        self.path = Path(language_path)
        self.evidence = verify_source(self.path, REVIEWED_LANGUAGE_SHA256)
        self.language = self.path.read_bytes()
        if hashlib.sha256(self.language).hexdigest() != REVIEWED_LANGUAGE_SHA256:
            raise ValueError('selector-command-language-changed-before-parse')
        self.commands = {}
        self.gap_inspector = None

    def _command(self, command_id):
        if command_id not in self.commands:
            lookup = _lookup_command(self.language, command_id)
            body = bytes.fromhex(lookup['decodedPayloadHex'] or '')
            post = audit_command_postprocessing({'decodedPayloadHex': body.hex(), 'lookupCapacity': 512})
            complete = len(body) >= 2 and len(body) >= body[1] + 2
            request = body[2:2+body[1]] if complete else b''
            read = lookup['recordFound'] and ((len(request) == 3 and request[0] == 0x22)
                    or (len(request) == 2 and request[0] in (0x21, 0x1a)))
            self.commands[command_id] = {'commandId': command_id, 'commandIdHex': f'{command_id:08X}',
                'recordFound': lookup['recordFound'], 'commandRecordIndex': lookup['commandRecordIndex'],
                'languageSourceSha256': lookup['languageSourceSha256'], 'commandGagSha256': lookup['commandGagSha256'],
                'decodedDescriptorSha256': lookup.get('decodedPayloadSha256'),
                'decodedDescriptorBytes': len(body), 'flagByte': body[0] if len(body) >= 2 else None,
                'declaredRequestBytes': body[1] if len(body) >= 2 else None,
                'rawDescriptorTrailingBytes': len(body)-2-body[1] if complete else None,
                'rawDescriptorComplete': complete, 'readCandidateHex': request.hex().upper() if read else None,
                'nonReadOrUnresolvedRequestRetainedAsPayload': False,
                'postprocessingAudit': post, 'actualCommandLookupRuntimeQualified': False, **GUARDS}
        return self.commands[command_id]

    def reference(self, ecu_id, module, name):
        diagnostic = self.diagnostic.reference(ecu_id, name)
        versions = self.version.reference(ecu_id, module, name, system_key=diagnostic['systemKey'])
        routes = []
        for route in versions['routes']:
            definition = {'selectors': route['selectors'], 'variants': [],
                          'sourceSha256': versions['sourceSha256'], 'definitionOffset': route['definitionOffset']}
            audit = audit_version_vectors(definition, [])
            groups = []
            for group in audit['requestGroups']:
                occurrences = []
                for link in diagnostic['links']:
                    comm = link['communication']
                    for pair in comm['pairs']:
                        if (comm['class'] != 'static-source-confirmed'
                                or 'communication-record-trailing-bytes-unresolved' in link.get('qualificationGaps', [])):
                            continue
                        try:
                            caller = audit_enter_sys_command_caller(comm['modeByte'], pair['subtypeByte'])
                        except ValueError:
                            continue
                        for index, command_id in enumerate(pair['parameterIds']):
                            command = self._command(command_id)
                            if command['readCandidateHex'] != group['requestCandidateHex']:
                                continue
                            occurrences.append({'linkIndex': link['linkIndex'], 'pairIndex': pair['pairIndex'],
                                'requestIndex': index, 'communicationSourceOffset': link['communicationSourceOffset'],
                                'serviceVariantIndex': link['serviceVariantIndex'],
                                'sourceCommand': command, 'enterSysCallerSourceAudit': caller,
                                'actualRequestIndexSelected': False, **GUARDS})
                missing_audit = None
                if not occurrences:
                    if self.gap_inspector is None:
                        self.gap_inspector = SelectorReadSourceGap(self.diagnostic.logical_blob, self.language)
                    missing_audit = self.gap_inspector.reference(ecu_id, name, group['requestCandidateHex'])
                groups.append({'requestCandidateHex': group['requestCandidateHex'],
                    'nativeResponseMapKey': group['nativeResponseMapKey'], 'selectorIndices': group['selectorIndices'],
                    'sourceOccurrences': occurrences, 'sourceOccurrenceCount': len(occurrences),
                    'bindingStatus': 'exact-read-source-occurrences' if occurrences else 'read-source-occurrence-missing',
                    'missingSourceAudit': missing_audit,
                    **GUARDS})
            covered = bool(groups) and not audit['unresolvedSelectors'] and not audit['mapKeyCollisions'] and all(
                group['sourceOccurrences'] for group in groups)
            gaps = ['actual-DSN-route/link/pair-selection', 'actual-postprocessed-command-buffer',
                    'actual-native-response-map-origin', 'fresh-complete-ECU-identity/version']
            if any(not occurrence['sourceCommand']['postprocessingAudit']['sourcePostprocessingComplete']
                   for group in groups for occurrence in group['sourceOccurrences']):
                gaps.append('command-string-termination-unqualified')
            tuples = [(link['communication']['modeByte'], pair['subtypeByte'])
                      for link in diagnostic['links'] for pair in link['communication']['pairs']]
            if any(t not in ((0x10, 3), (0x30, 1), (0x30, 4), (0x50, 5)) for t in tuples):
                gaps.append('command-lookup-caller-mode-unreviewed')
            if any(not occurrence['enterSysCallerSourceAudit']['commandResponseAssociationAuditImplemented']
                   for group in groups for occurrence in group['sourceOccurrences']):
                gaps.append('command-response-association-mode-unreviewed')
            if not covered:
                gaps.append('selector-read-source-coverage-incomplete')
            routes.append({'routeIndexOffset': route['routeIndexOffset'], 'definitionOffset': route['definitionOffset'],
                'versionMatchCount': route['versionMatchCount'], 'requestGroups': groups,
                'unresolvedSelectors': audit['unresolvedSelectors'], 'mapKeyCollisions': audit['mapKeyCollisions'],
                'allSelectorRequestsHaveSourceOccurrences': covered, 'qualificationGaps': gaps, **GUARDS})
        exact = (diagnostic['bindingStatus'] == 'exact-source-name-match'
                 and versions['bindingStatus'] == 'exact-source-version-rule-match')
        covered = exact and len(routes) == 1 and routes[0]['allSelectorRequestsHaveSourceOccurrences']
        return {'kind': 'x431-offline-version-selector-source-plan', 'requestedEcuId': ecu_id,
            'module': module, 'variantName': name, 'diagnosticBindingStatus': diagnostic['bindingStatus'],
            'versionBindingStatus': versions['bindingStatus'], 'languageSourceSha256': self.evidence['sha256'],
            'routes': routes, 'allSelectorRequestsHaveSourceOccurrences': bool(covered),
            'bindingStatus': 'exact-selector-read-source-coverage' if covered else
                'source-binding-missing-or-ambiguous' if not exact else 'selector-read-source-coverage-incomplete',
            'fallbackUsed': False, **GUARDS}

    def verify_unchanged(self):
        return verify_source(self.path, self.evidence['sha256'])
