"""Apply pinned DSN version rules to one offline capture scope, never select an ECU.

Only identical complete responses may share a candidate calculation. Changed,
failed or incomplete selector inputs block it; synthetic and observed inputs
remain separate. Memory is bounded by source requests, not capture length.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .x431_variant_selection import (
    REVIEWED_DSN_SHA256, REVIEWED_LANGUAGE_SHA256, audit_version_vectors, audit_application_version_observations)

GUARDS = {'selectedVehicleVariant': None, 'ecuVersionQualified': False,
          'freshReadProven': False, 'nativeResponseMapOriginProven': False,
          'applicationVectorEqualsNativeMapProven': False,
          'executionEnabled': False, 'writePayload': None}


class CaptureVersionRules:
    def __init__(self, profiles, ecu_id):
        self.routes = {}
        for profile in profiles:
            for reference in profile.get('versionRoutes', []):
                if reference.get('sourceSha256') != REVIEWED_DSN_SHA256:
                    raise ValueError('identity-version-DSN-source-unreviewed')
                if reference.get('requestedEcuId') != ecu_id:
                    if reference.get('requestedEcuId') not in profile.get('ecuIds', [ecu_id]):
                        raise ValueError('identity-version-source-unit-conflict')
                    continue
                for route in reference['routes']:
                    key = (route['routeIndexOffset'], route['definitionOffset'])
                    selectors = route['selectors']
                    current = self.routes.setdefault(key, {
                        'sourceSha256': REVIEWED_DSN_SHA256, 'matchesReviewedSource': True,
                        'definitionOffset': route['definitionOffset'], 'selectors': selectors,
                        'variants': [], 'routeIndexOffset': route['routeIndexOffset'],
                        'sourceProfileIds': [], **GUARDS})
                    if current['selectors'] != selectors:
                        raise ValueError('identity-version-source-selector-conflict')
                    for variant in route['versionMatches']:
                        candidate = {key: variant[key] for key in ('name', 'ruleOffset', 'rules')}
                        prior = next((v for v in current['variants'] if v['name'] == candidate['name']), None)
                        if prior is not None and prior != candidate:
                            raise ValueError('identity-version-source-rule-conflict')
                        if prior is None: current['variants'].append(candidate)
                    if profile['profileId'] not in current['sourceProfileIds']:
                        current['sourceProfileIds'].append(profile['profileId'])
        self.requests = {group['requestCandidateHex'] for route in self.routes.values()
                         for group in audit_version_vectors(route, [])['requestGroups']}
        # One bounded record per source request and provenance class.
        self.inputs = {'observed': {}, 'synthetic': {}}

    def observe(self, row, error=None):
        hx = row['context']['requestHex']
        if hx not in self.requests: return
        category = 'synthetic' if row['synthetic'] else 'observed'
        inputs = self.inputs[category]
        current = inputs.setdefault(hx, {'responseHex': None, 'sha256': None,
            'conflict': False, 'failed': False, 'observations': 0})
        current['observations'] += 1
        if error:
            current['failed'] = True
            return
        response = row['pduHex']
        # The native selector copy has 512 bytes, including identifier bytes.
        if not isinstance(response, str) or not 1 <= len(response) // 2 <= 513:
            current['failed'] = True
            return
        digest = hashlib.sha256(bytes.fromhex(response)).hexdigest()
        if current['sha256'] is not None and current['sha256'] != digest:
            current['conflict'] = True
        elif current['sha256'] is None:
            current.update(responseHex=response, sha256=digest)

    def finish(self, language_path, *, capture_complete=True):
        reports = []
        for route in self.routes.values():
            requests = [g['requestCandidateHex'] for g in audit_version_vectors(route, [])['requestGroups']]
            for category, inputs in self.inputs.items():
                conflicts = [hx for hx in requests if inputs.get(hx, {}).get('conflict')]
                failures = [hx for hx in requests if inputs.get(hx, {}).get('failed')]
                missing = [hx for hx in requests if inputs.get(hx, {}).get('responseHex') is None]
                report = {'routeIndexOffset': route['routeIndexOffset'],
                    'definitionOffset': route['definitionOffset'], 'sourceProfileIds': route['sourceProfileIds'],
                    'sourceVariantCount': len(route['variants']), 'source': category,
                    'conflictingRequests': conflicts, 'failedRequests': failures,
                    'missingRequests': missing, 'calculationComplete': False,
                    'matchingCandidates': [], 'matchingCandidateCount': 0, 'error': None, **GUARDS}
                if not capture_complete:
                    report['status'] = 'capture-integrity-incomplete'
                elif conflicts or failures or missing:
                    report['status'] = 'selector-inputs-incomplete-or-conflicting'
                elif not route['variants']:
                    report['status'] = 'source-version-rules-missing'
                else:
                    observations = [{'requestHex': hx, 'responseHex': inputs[hx]['responseHex'],
                        'sourceKind': 'simulation' if category == 'synthetic' else 'historical-capture'}
                        for hx in requests]
                    try:
                        audit = audit_application_version_observations(route, observations, Path(language_path))
                        report['calculationComplete'] = audit['calculationComplete']
                        report['applicationInputsComplete'] = audit['applicationInputsComplete']
                        report['languageSourceVerified'] = audit.get('formulaSourceMatchesReviewedBytes', False)
                        match = audit['versionRuleMatch']
                        report['matchingCandidates'] = match['matchingCandidates'] if match else []
                        report['matchingCandidateCount'] = len(report['matchingCandidates'])
                        report['fieldAudits'] = audit.get('calculatedFields', [])
                        report['status'] = ('offline-rule-candidates' if report['calculationComplete'] else
                                            'selector-response-or-formula-unresolved')
                    except (OSError, ValueError, KeyError, TypeError, ArithmeticError, UnicodeError) as error:
                        report.update(status='selector-calculation-failed', error=str(error))
                reports.append(report)
        return {'kind': 'offline-capture-version-rule-comparison',
            'sourceSha256': REVIEWED_DSN_SHA256, 'routeCount': len(self.routes),
            'reviewedLanguageSha256': REVIEWED_LANGUAGE_SHA256,
            'captureIntegrityComplete': capture_complete,
            'reports': reports, 'atomicReadProven': False,
            'identicalRepeatedResponsesRequired': True,
            'sourceCatalogVersionsOnly': True, **GUARDS}
