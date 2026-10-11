"""Explain an explicit selector read's source gap without borrowing another ECU.

Only the pinned COMMAND namespace is scanned, never all GAG namespaces.
Presence elsewhere cannot qualify the current route or any vehicle request.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from scripts.x431_re.sysdata import parse_payload
from .x431_command_origin import (
    _decode_command_records, audit_command_postprocessing,
    audit_enter_sys_command_caller)
from .x431_version_response import SYS_DATA_LOGICAL_SHA256, VERSION_GUARDS


def _read_request(value):
    if not isinstance(value,str) or not re.fullmatch(r'(?:22[0-9A-Fa-f]{4}|(?:21|1[Aa])[0-9A-Fa-f]{2})',value):
        raise ValueError('selector-source-gap-single-read-required')
    return value.upper()


class SelectorReadSourceGap:
    def __init__(self, logical_blob, language_blob):
        if not isinstance(logical_blob,bytes) or hashlib.sha256(logical_blob).hexdigest()!=SYS_DATA_LOGICAL_SHA256:
            raise ValueError('selector-source-gap-SYS_DATA-unreviewed')
        self.parsed=parse_payload(logical_blob)
        if not self.parsed.get('ok') or self.parsed['familyIdQueried']!=13:
            raise ValueError('selector-source-gap-family-unreviewed')
        source,records=_decode_command_records(language_blob)
        self.records=[];self.by_request={}
        for index,stored,command_id,body in records:
            complete=len(body)>=2 and len(body)>=body[1]+2
            candidate=body[2:body[1]+2].hex().upper() if complete else ''
            row={'commandId':command_id,'commandIdHex':f'{command_id:08X}',
                 'commandRecordIndex':index,'decodedDescriptorSha256':hashlib.sha256(body).hexdigest(),
                 'decodedDescriptorBytes':len(body),'rawDescriptorComplete':complete,
                 'flagByte':body[0] if len(body)>=2 else None,
                 'declaredRequestBytes':body[1] if len(body)>=2 else None,
                 'postprocessingAudit':audit_command_postprocessing({'decodedPayloadHex':body.hex(),'lookupCapacity':512})}
            # Do not expose non-read descriptors or search their payload substring.
            if re.fullmatch(r'(?:22[0-9A-F]{4}|(?:21|1A)[0-9A-F]{2})',candidate):
                self.by_request.setdefault(candidate,[]).append(row)
            self.records.append(row)
        self.catalog={key:source[key] for key in ('languageSourceSha256','commandGagSha256',
            'commandGagSourceOffset','sourceFileSelectionAudit')}
        self.catalog.update(commandRecordsAudited=len(self.records),namespace='COMMAND.GAG')

    def reference(self,ecu_id,variant_name,request):
        if (type(ecu_id) is not int or not 0<=ecu_id<=255 or not isinstance(variant_name,str)
                or not variant_name or not variant_name.isascii() or '\0' in variant_name or len(variant_name)>240):
            raise ValueError('selector-source-gap-exact-ECU-name-required')
        request=_read_request(request);matches=self.by_request.get(request,[])
        command_ids={row['commandId'] for row in matches}
        current_matches=[];occurrences=[]
        for system in self.parsed['systems']:
            system_ecu=system['systemKey']&255
            for link in system['links']:
                names=[row['name'] for row in link['service']['variants']]
                in_current=system_ecu==ecu_id and variant_name in names
                if in_current:
                    current_matches.extend({'linkIndex':link['index'],'serviceVariantIndex':i}
                                           for i,name in enumerate(names) if name==variant_name)
                comm=link['comm']
                for pi,pair in enumerate(comm.get('pairs',[])):
                    try:caller=audit_enter_sys_command_caller(comm['protocolKind'],pair['mode'])
                    except ValueError:caller=None
                    layout_complete=(comm.get('class')=='static-source-confirmed'
                                     and comm.get('consumedEnd')==link['serviceOff'])
                    for ri,command_id in enumerate(pair['params']):
                        if command_id not in command_ids:continue
                        occurrences.append({'sourceEcuId':system_ecu,'sourceSystemKey':system['systemKey'],
                            'linkIndex':link['index'],'pairIndex':pi,'requestIndex':ri,
                            'serviceVariantNames':names,'communicationSourceOffset':link['commOff'],
                            'modeByte':comm['protocolKind'],'subtypeByte':pair['mode'],
                            'commandId':command_id,'currentExactSourceNameLink':in_current,
                            'communicationRecordComplete':layout_complete,
                            'lookupCallerReviewed':caller is not None,
                            'eligibleCurrentSourceOccurrence':in_current and layout_complete and caller is not None,
                            **VERSION_GUARDS})
        current=[row for row in occurrences if row['currentExactSourceNameLink']]
        eligible=[row for row in current if row['eligibleCurrentSourceOccurrence']]
        if not current_matches:status='current-exact-source-name-missing'
        elif len(current_matches)!=1:status='current-source-link-ambiguous'
        elif eligible:status='current-source-read-occurrence-present'
        elif current:status='current-source-occurrence-layout-or-caller-unreviewed'
        elif not matches:status='read-command-absent-from-pinned-catalog'
        elif not occurrences:status='read-command-present-without-family-SYS_DATA-reference'
        elif any(row['sourceEcuId']==ecu_id for row in occurrences):status='read-command-only-in-other-current-ECU-links'
        else:status='read-command-only-in-other-ECUs'
        return {'kind':'x431-offline-selector-read-source-gap','sysDataSourceSha256':SYS_DATA_LOGICAL_SHA256,
                'catalogAudit':self.catalog,'requestedEcuId':ecu_id,'variantName':variant_name,
                'requestCandidateHex':request,'commandCatalogMatchCount':len(matches),
                'commandCatalogMatches':matches,'sourceFamilyId':13,
                'currentExactSourceMatches':current_matches,
                'currentExactSourceNameMatchCount':len(current_matches),
                'currentSourceOccurrenceCount':len(current),'eligibleCurrentSourceOccurrenceCount':len(eligible),
                'sourceOccurrences':occurrences,'sourceOccurrenceCount':len(occurrences),'status':status,
                'otherSourceOccurrencesBorrowed':False,'selectedCurrentLinkIndex':None,
                'missingSelectorCoverageRepaired':False,'vehicleRequestsSent':False,**VERSION_GUARDS}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--logical-sys-data',type=Path,required=True)
    parser.add_argument('--language-ggp',type=Path,required=True)
    parser.add_argument('--ecu-id',type=int,required=True)
    parser.add_argument('--variant-name',required=True)
    parser.add_argument('--request',required=True)
    args=parser.parse_args(argv)
    try:
        _read_request(args.request)
        audit=SelectorReadSourceGap(args.logical_sys_data.read_bytes(),args.language_ggp.read_bytes())
        report=audit.reference(args.ecu_id,args.variant_name,args.request)
    except (OSError,ValueError) as error:parser.exit(2,f'{error}\n')
    print(json.dumps(report,ensure_ascii=True,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
