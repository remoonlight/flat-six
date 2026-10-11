"""Pinned DSN rule/route references for offline collection; never selects an ECU.

Native selector map keys are sums of source words, not wire requests. A rule
name in DSN does not supply a missing SYS_DATA service table or runtime context.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.x431_re.index_parse import parse_dsn_index, parse_l2_u16unk_id_name_off
from .offline_match import FLAGS, verify_source
from .x431_variant_selection import (
    REVIEWED_DSN_SHA256, parse_version_definition, classify_version_formula_route)
from .x431_definition_binding import audit_dsn_route_context

GUARDS = {**FLAGS, 'vehicleVerified': False, 'ecuVersionQualified': False,
          'actualDsnRouteSelectionQualified': False, 'selectedVehicleVariant': None,
          'selectedRouteIndexOffset': None, 'responseMapOriginQualified': False}


class VersionRouteSource:
    def __init__(self, path):
        self.path = Path(path)
        self.evidence = verify_source(self.path, REVIEWED_DSN_SHA256)
        blob = self.path.read_bytes()
        if hashlib.sha256(blob).hexdigest() != REVIEWED_DSN_SHA256:
            raise ValueError('version-route-source-changed-before-parse')
        families = parse_dsn_index(blob)['entries']
        matches = [row for row in families if row['id'] == 13 and row['name'] == '9x1']
        if len(matches) != 1:
            raise ValueError('version-route-family-not-unique')
        family = matches[0]
        limit = min((row['off'] for row in families if row['off'] > family['off']), default=len(blob))
        index = parse_l2_u16unk_id_name_off(blob, family['off'], limit)
        self.index_start = family['off']
        self.index_end = family['off'] + index['consumed']
        self.routes = index['entries']
        self.definitions = {row['off']: parse_version_definition(blob, row['off']) for row in self.routes}

    def reference(self, ecu_id, module, name, *, system_key=None):
        if (type(ecu_id) is not int or not 0 <= ecu_id <= 255
                or any(not isinstance(value, str) or not value or not value.isascii()
                       or '\0' in value for value in (module, name))):
            raise ValueError('version-route-selector-invalid')
        if system_key is not None and (type(system_key) is not int
                or system_key >> 8 != 13 or system_key & 255 != ecu_id):
            raise ValueError('version-route-system-key-mismatch')
        same_id = [row for row in self.routes if row['id'] == ecu_id]
        routes = []
        for row in same_id:
            if row['name'] != module:
                continue
            definition = self.definitions[row['off']]
            versions = [{'versionIndex': i, **variant, **GUARDS}
                        for i, variant in enumerate(definition['variants']) if variant['name'] == name]
            selectors = [{**selector, 'formulaRoute': classify_version_formula_route(selector['formulaId']),
                          'wireRequestHex': None, **GUARDS} for selector in definition['selectors']]
            routes.append({'routeIndexOffset': row['at'], 'routeId': row['id'], 'module': row['name'],
                'flagsWord': row['unk'], 'flagLowByte': row['unk'] & 255, 'flagHighByte': row['unk'] >> 8,
                'definitionOffset': row['off'], 'definitionIndexEnd': definition['indexEnd'],
                'sourcePredicateAudit': audit_dsn_route_context(ecu_id, row['unk'], system_key)
                    if system_key is not None else None,
                'selectors': selectors, 'versionMatchCount': len(versions), 'versionMatches': versions,
                'qualificationGaps': ['fresh-complete-identity/version', 'actual-selector-response-map-origin',
                    'actual-iComCmt-and-TOKEN-context', 'actual-DSN-route-selection',
                    'exact-SYS_DATA-service-name/link/pair', 'actual-runtime-rule-calculation-and-comparison'],
                **GUARDS})
        count = sum(row['versionMatchCount'] for row in routes)
        status = ('source-route-missing' if not routes else 'source-version-name-missing' if not count else
                  'source-route-or-version-ambiguous' if len(routes) != 1 or count != 1 else
                  'exact-source-version-rule-match')
        return {'kind': 'x431-offline-version-route-reference', 'sourceSha256': self.evidence['sha256'],
            'requestedEcuId': ecu_id, 'module': module, 'variantName': name, 'systemKey': system_key,
            'bindingStatus': status, 'routeMatchCount': len(routes), 'versionMatchCount': count,
            'routes': routes, 'sameIdSourceRoutes': [
                {'module': row['name'], 'routeIndexOffset': row['at'], 'definitionOffset': row['off'],
                 'flagsWord': row['unk']} for row in same_id],
            'fallbackUsed': False, 'countBasis': 'exact ECU/module/name source references; not installed units or runtime selections',
            **GUARDS}

    def verify_unchanged(self):
        return verify_source(self.path, self.evidence['sha256'])

    def summary(self):
        return {'source': self.evidence, 'familyId': 13, 'routeCount': len(self.routes),
                'ecuIdCount': len({row['id'] for row in self.routes}),
                'uniqueDefinitionCount': len(self.definitions),
                'routeIndexSourceStart': self.index_start, 'routeIndexSourceEnd': self.index_end,
                'vehicleGenerationSelected': None, **GUARDS}
