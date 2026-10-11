"""Portable, immutable all-unit source snapshots for the desktop; no transport."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

from .offline_match import FLAGS, ANCHORS

PREFIX = '.local/diagnostics/x431-source-references'
ACTIONS = frozenset({'source-catalogs','source-units','source-profile'})
GUARDS = {**FLAGS, 'vehicleVerified':False,'ecuVersionQualified':False,
          'diagnosticsQualified':False,'liveDataQualified':False,'codingWriteQualified':False,
          'codingRestoreQualified':False,'actualDynamicLookupBindingQualified':False}
APP = Path(__file__).resolve().parents[2]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value):
    return (json.dumps(value,ensure_ascii=True,sort_keys=True,indent=2)+'\n').encode()


def build_snapshot(prepared_root, output_root):
    root = Path(prepared_root); output = Path(output_root)
    if output.exists():
        raise ValueError('source-catalog-fresh-output-required')
    m = json.loads((root/'manifest.json').read_text(encoding='utf8'))
    for row in m['inputs']+m['code']:
        path = Path(row['path'])
        if _sha(path.read_bytes())!=row['sha256']:
            raise ValueError('source-catalog-generator-source-drift')
    if sum(row['sha256']==ANCHORS['variants'] for row in m['inputs'])!=1:
        raise ValueError('source-catalog-variant-source-unreviewed')
    files = []
    for row in m['outputs']:
        name = 'profiles/'+Path(row['path']).name if Path(row['path']).parent.name=='profiles' else Path(row['path']).name
        if not _name(name):
            raise ValueError('source-catalog-output-path-invalid')
        file = root/name
        if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
            raise ValueError('source-catalog-output-path-invalid')
        data = file.read_bytes()
        if len(data)!=row['bytes'] or _sha(data)!=row['sha256']:
            raise ValueError('source-catalog-output-drift')
        files.append(dict(name=name,bytes=len(data),sha256=row['sha256']))
    code = []
    for row in m['code']:
        path = Path(row['path']).resolve()
        if not path.is_relative_to(APP):
            raise ValueError('source-catalog-generator-code-outside-app')
        code.append(dict(name=path.relative_to(APP).as_posix(),sha256=row['sha256']))
    code.append(dict(name='scripts/diagnostics/source_catalog.py',sha256=_sha(Path(__file__).read_bytes())))
    manifest = {'schemaVersion':1,'kind':'x431-offline-source-catalog',
        'sourceFingerprints':[dict(name=Path(r['path']).name,bytes=r['bytes'],sha256=r['sha256']) for r in m['inputs']],
        'generatorCode':code,'files':files,**GUARDS}
    raw = _json_bytes(manifest); catalog_id = _sha(raw)
    target = output/PREFIX/catalog_id
    target.mkdir(parents=True)
    for row in files:
        destination = target/row['name']; destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(root/row['name'],destination)
        if _sha(destination.read_bytes())!=row['sha256']:
            raise ValueError('source-catalog-copy-drift')
    (target/'manifest.json').write_bytes(raw)
    return {'ok':True,'sourceCatalogId':catalog_id,'files':len(files)+1,**GUARDS}


def _name(name):
    return isinstance(name,str) and (name in ('all-units.json','全控制单元采集.md') or re.fullmatch(r'profiles/[a-f0-9]{64}\.json',name) is not None)


def _read(root, name, maximum):
    file = root/name
    if root.is_symlink() or file.is_symlink() or any(p.is_symlink() for p in file.parents if p!=root.parent and p.is_relative_to(root)) or not file.resolve().is_relative_to(root.resolve()):
        raise ValueError('source-catalog-path-invalid')
    with file.open('rb') as source:
        data = source.read(maximum+1)
    if len(data)>maximum:
        raise ValueError('source-catalog-file-limit')
    return data


def _catalog(library, catalog_id):
    if not isinstance(catalog_id,str) or re.fullmatch(r'[a-f0-9]{64}',catalog_id) is None:
        raise ValueError('source-catalog-id-invalid')
    root = Path(library)/PREFIX/catalog_id
    if not root.resolve().is_relative_to(Path(library).resolve()) or any(p.is_symlink() for p in (root,root.parent,root.parent.parent)):
        raise ValueError('source-catalog-path-invalid')
    raw = _read(root,'manifest.json',2*1024**2)
    if _sha(raw)!=catalog_id:
        raise ValueError('source-catalog-manifest-hash-mismatch')
    m = json.loads(raw)
    if m.get('schemaVersion')!=1 or m.get('kind')!='x431-offline-source-catalog' or any(m.get(k)!=v for k,v in GUARDS.items()):
        raise ValueError('source-catalog-manifest-invalid')
    files = m.get('files')
    if (not isinstance(files,list) or not 1<=len(files)<=1000 or len({r.get('name') for r in files})!=len(files)
            or any(not _name(r.get('name')) or type(r.get('bytes')) is not int or not 0<=r['bytes']<=32*1024**2
                   or re.fullmatch(r'[a-f0-9]{64}',r.get('sha256','')) is None for r in files)
            or sum(r.get('sha256')==ANCHORS['variants'] for r in m.get('sourceFingerprints',[]))!=1):
        raise ValueError('source-catalog-file-register-invalid')
    return root,m


def _document(root, manifest, name):
    rows = [r for r in manifest['files'] if r['name']==name]
    if len(rows)!=1:
        raise ValueError('source-catalog-file-not-registered')
    row = rows[0]; raw = _read(root,name,row['bytes'])
    if len(raw)!=row['bytes'] or _sha(raw)!=row['sha256']:
        raise ValueError('source-catalog-file-hash-mismatch')
    return json.loads(raw)


def _code_status(manifest):
    drift = []
    for row in manifest.get('generatorCode',[]):
        name = row.get('name',''); file = APP/name
        if not name.startswith('scripts/') or '\\' in name or '..' in Path(name).parts or not file.resolve().is_relative_to(APP):
            raise ValueError('source-catalog-code-reference-invalid')
        if not file.is_file() or _sha(file.read_bytes())!=row.get('sha256'):
            drift.append(name)
    return {'generatorCodeMatchesCurrent':bool(manifest.get('generatorCode')) and not drift,
            'generatorCodeDrift':drift,'sourceFingerprints':manifest['sourceFingerprints']}


def handle_source_catalog(req, library):
    action = req.get('action'); fields = {'action'}
    if action in ('source-units','source-profile'):
        fields |= {'generation','sourceCatalogId'}
    if action=='source-profile':
        fields |= {'ecuId','profileId'}
    if action not in ACTIONS or set(req)!=fields:
        raise ValueError('source-catalog-request-invalid')
    def result(**values):
        return {'ok':True,**values,**GUARDS}
    if action=='source-catalogs':
        parent = Path(library)/PREFIX
        if not parent.exists():
            return result(catalogs=[])
        catalogs = []
        for folder in sorted(parent.iterdir()):
            if re.fullmatch(r'[a-f0-9]{64}',folder.name) is None:
                continue
            root,m = _catalog(library,folder.name)
            catalogs.append(dict(sourceCatalogId=folder.name,profileFiles=sum(r['name'].startswith('profiles/') for r in m['files']),**_code_status(m)))
        return result(catalogs=catalogs)
    if req['generation'] not in ('981','982'):
        raise ValueError('wrong-generation')
    root,m = _catalog(library,req['sourceCatalogId'])
    index = _document(root,m,'all-units.json')
    units = [u for u in index['units'] if u['generation']==req['generation']]
    if action=='source-units':
        return result(units=[dict(ecuId=u['ecuId'],label=u['label'],variants=[dict(profileId=r['profileId'],name=r['variant']) for r in u['variants']]) for u in units],
            sourceBindingCounts=index.get('versionSelectorSourceBindingCounts'),**_code_status(m))
    if type(req['ecuId']) is not int or not isinstance(req['profileId'],str):
        raise ValueError('source-catalog-profile-selector-invalid')
    refs = [r for u in units if u['ecuId']==req['ecuId'] for r in u['variants'] if r['profileId']==req['profileId']]
    if len(refs)!=1:
        raise ValueError('source-catalog-profile-ecu-mismatch')
    ref = refs[0]; p = _document(root,m,ref['file'])
    if p['profileId']!=req['profileId'] or req['ecuId'] not in p['ecuIds'] or req['generation'] not in p['generations']:
        raise ValueError('source-catalog-profile-identity-mismatch')
    selector = [s for s in p.get('versionSelectorReadSources',[]) if s['requestedEcuId']==req['ecuId']]
    groups = []
    for plan in selector:
        for route in plan['routes']:
            for group in route['requestGroups']:
                occurrences = group['sourceOccurrences']; posts = [r['sourceCommand']['postprocessingAudit'] for r in occurrences]
                groups.append(dict(requestHex=group['requestCandidateHex'],selectorIndices=group['selectorIndices'],
                    sourceOccurrences=len(occurrences),postprocessingCompleteOccurrences=sum(bool(post['sourcePostprocessingComplete']) for post in posts),
                    missingReason=(group.get('missingSourceAudit') or {}).get('status'),
                    loaderRequiredSymbolCount=max([post.get('lookupBufferSourceAudit',{}).get('loaderSourceAudit',{}).get('requiredSymbolCount',0) for post in posts],default=0)))
    coding = p.get('coding') or {}
    preview = None
    if coding.get('editorReadinessScope') == 'offline-field-preview-only':
        readiness = coding.get('previewReadiness')
        total = coding.get('definitionFieldCount')
        if (not isinstance(readiness, dict) or type(total) is not int or total != ref.get('codingFields')
                or any(not isinstance(key, str) or type(value) is not int or value < 0 for key, value in readiness.items())
                or sum(readiness.values()) != total):
            raise ValueError('source-catalog-coding-preview-counts-invalid')
        enums = readiness.get('named-options-ready', 0)
        numeric = readiness.get('source-bounded-linear-preview-ready', 0)
        preview = dict(totalFields=total, namedOptionFields=enums, numericCandidateFields=numeric,
            blockedFields=total-enums-numeric, scope='offline-field-preview-only',
            nativeEditingBehaviorVerified=False, codingWriteQualified=False)
    return result(sourceCatalogId=req['sourceCatalogId'],generation=req['generation'],ecuId=req['ecuId'],profileId=p['profileId'],
        variant=p['variant'],module=p['module'],counts={k:ref.get(k) for k in ('identityReadGroups','measurementDefinitions','measurementReadGroups','codingFields','codingReadGroups')},
        diagnosticBindingStatus=ref.get('diagnosticSourceBindingStatus'),versionBindingStatus=ref.get('versionRouteBindingStatus'),
        selectorBindingStatus=ref.get('versionSelectorSourceBindingStatus'),selectorGroups=groups,
        codingPreviewSummary=preview,
        actualDynamicBindingStatus='unqualified',actualVehicleStatus='unqualified',**_code_status(m))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-root',type=Path,required=True)
    parser.add_argument('--output-root',type=Path,required=True)
    args = parser.parse_args()
    print(json.dumps(build_snapshot(args.prepared_root,args.output_root),ensure_ascii=True))


if __name__=='__main__':
    main()
