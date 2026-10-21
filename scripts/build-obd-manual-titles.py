"""Build scoped factory DTC title references from the two pinned original PDFs.

Requires pdfplumber in the developer's PDF runtime. Never opens a device or writes
an original PDF. Rejected/ambiguous source text is retained in local evidence;
legacy translated code tables are not inputs. This does not qualify a live ECU.
"""
from pathlib import Path
import argparse,collections,gzip,hashlib,json,re,signal,time

CODE=re.compile(r'^([PBCU][0-9A-F]{4}(?:[0-9A-F]{1,2})?)(?![0-9A-F])\s*')
SECTION=re.compile(r'^(?:诊断(?:条|条件)|故障(?:设|条件|查找)|可能.{0,4}故障|监控|监测|Diagnostic conditions|Conditions for|Fault setting|Fault conditions|Possible fault|Fault finding|Monitored components|Diagnosis conditions)',re.I)

# Exact printed module headings, checked against each chapter's footer. Generic
# "Control module", "Component(s)" and all-unit CAN pages remain unresolved.
MODULE_HEADINGS={
    'rear-end electronics':'bcm-rear',
    'front-end electronics':'bcm-front','control module front-end electronics':'bcm-front',
    'level-control control unit (pasm)':'pasm',
    'tire pressure monitoring system control module':'rdk',
    'parking brake control unit':'epb','airbag':'airbag','airbag control unit':'airbag',
    'air-conditioning system':'hvac','instrument cluster':'cluster',
    'control module stopwatch':'clock','steering wheel electronics control module':'steering-column',
    'headlight control unit (central)':'headlamp-sg',
    'headlights':'headlamp-left','headlight control unit':'headlamp-left',
}
MODULE_SECTIONS={
    'dme':'9x1_DME_','pdk':'9x1_PDK_','psm':'9x1_PSM_',
    'gateway':'9x1_CAN_CAN_Gateway_','bcm-rear':'9x1_BCM_hinten_',
    'bcm-front':'9x1_BCM_vorne_','pasm':'9x1_PASM_PADM_','rdk':'9x1_RDK_',
    'epb':'9x1_EPB_','airbag':'9x1_Airbag_','hvac':'9x1_BKE_',
    'cluster':'9x1_Kombiinstrument_MM_','clock':'9x1_Zusatzinstrument_',
    'steering-column':'9x1_Lenkstockschalter_','headlamp-sg':'9x1_SCHEINWERFER_',
    'headlamp-left':'9x1_SWSG_links_',
}

def recover(text,font):
    match=re.fullmatch(r'\(cid:(\d+)\)',text)
    value=int(match[1]) if match else ord(text) if len(text)==1 else None
    # These embedded-font punctuation CIDs do not use the CJK offset.
    # Verified against the pinned 981 original pages 3844, 4085 and 4225.
    if 'SimSun' in font and value in (708,709,712):
        return {708:'（',709:'）',712:'，'}[value]
    if 'ArialUnicodeMS' in font and value==0x19e8:return '，'
    offset=0x2d71 if 'ArialUnicodeMS' in font else 0x4a2c if 'SimSun' in font else None
    if offset is not None:
        if match:return chr(int(match[1])+offset)
        if len(text)==1 and ord(text)>127:return chr(ord(text)+offset)
    return text.replace('\xad','-').replace('\xa0',' ')

def header(page):
    import pdfplumber
    chars=[]
    for item in page.chars:
        if not item['upright'] or item['x0']>=page.width or item['top']>=page.height:continue
        chars.append(dict(item,text=recover(item['text'],item['fontname'])))
    words=pdfplumber.utils.extract_words(chars,extra_attrs=['fontname','size'],x_tolerance=2,y_tolerance=3)
    lines=[]
    for word in sorted(words,key=lambda item:(item['top'],item['x0'])):
        if not lines or word['top']-lines[-1]['top']>6:
            lines.append({'top':word['top'],'words':[]})
        lines[-1]['words'].append(word)
    for line in lines:
        line['words'].sort(key=lambda word:word['x0'])
        line['text']=' '.join(word['text'] for word in line['words']).strip()
    return lines

def parse(lines,family,page):
    candidates=[(i,CODE.match(line['text'])) for i,line in enumerate(lines) if CODE.match(line['text'])]
    if not candidates:return [],'no-header-code'
    code_index,match=candidates[0]
    # Only the large page heading can identify the module. Body references do not.
    heading=re.compile(r'^(?:诊断.*控制|Diagnos(?:tic|is) information\b)',re.I)
    modules=[(i,line) for i,line in enumerate(lines)
             if heading.match(line['text']) and line['top']<(145 if family=='981' else 110)
             and any(word['size']>=14 for word in line['words'])]
    if len(modules)!=1:return [],'module-header-not-unique'
    module_index,module_line=modules[0]
    module_match=re.search(r'\b(DME|PDK|PSM|Gateway|CAN Gateway|TCM)\b',module_line['text'],re.I)
    module=module_match[1].lower() if module_match else MODULE_HEADINGS.get(
        re.sub(r'^Diagnos(?:tic|is) information\s*-\s*','',module_line['text'],flags=re.I).casefold())
    sections={match[0] for line in lines if line['top']>760
              for match in re.finditer(r'\b(?:9x1|[0-9][0-9A-Z]{2})_[A-Za-z0-9_-]+\b',line['text'])}
    source_section=next(iter(sections)) if len(sections)==1 else None
    if family=='981' and not (module_index<code_index and 100<lines[code_index]['top']<230):return [],'code-outside-title-band'
    if family=='982' and not (code_index<module_index and lines[code_index]['top']<60):return [],'code-outside-title-band'
    start=module_index+1
    applicability=[]
    if family=='982':
        # The original title is the first Arial 11/12-point block, before any
        # notice, applicability bullet or diagnostic section. Preserve wrapped
        # words across the full page width; do not append body notes to a title.
        # PDF symbol fonts can expose warning icons as "8" or "MN". They
        # introduce a notice, not a vehicle/variant applicability condition.
        notice=re.compile(r'^(?:[A-Z0-9]{1,2}\s+)?(?:INFORMATION|NOTE|WARNING|CAUTION|DANGER)\b',re.I)
        block=[];boundary=None
        for i in range(start,len(lines)):
            line=lines[i]
            title_style=all(('Arial-BoldMT' in word['fontname'] or 'ArialMT' in word['fontname']) and 10<=word['size']<13 for word in line['words'])
            if SECTION.match(line['text']) or notice.match(line['text']) or re.match(r'^[IJK]\s+',line['text']) or not title_style:
                boundary=i
                if not SECTION.match(line['text']) and not notice.match(line['text']):
                    for context in lines[i:]:
                        if SECTION.match(context['text']) or notice.match(context['text']):break
                        if '(cid:' in context['text'] or any(0xd800<=ord(c)<=0xdfff for c in context['text']):
                            return [],'header-context-unreadable'
                        applicability.append(context['text'])
                break
            if not block and line['top']>170:return [],'title-outside-title-band'
            block.append(line)
        if boundary is None:return [],'title-boundary-not-found'
        if not block:return [],'title-not-complete'
        pieces=[]
        for line in block:
            repeated=CODE.match(line['text'])
            if repeated and repeated[1]!=match[1]:return [],'different-code-in-title'
            pieces.append(CODE.sub('',line['text'],count=1) if repeated else line['text'])
        specs=[(match[1],' '.join(pieces),block)]
    else:
        boundary=next((i for i in range(start,len(lines)) if SECTION.match(lines[i]['text'])),None)
        if boundary is None:return [],'first-section-not-found'
        # Guided-fault-finding availability is a separate notice below the title.
        end=next((i for i in range(start,boundary) if '导' in lines[i]['text'] and '可' in lines[i]['text'] and '用' in lines[i]['text']),boundary)
        block=lines[start:end]
        code_rows=[(i,CODE.match(line['text'])) for i,line in enumerate(block) if CODE.match(line['text'])]
        if not code_rows:return [],'no-code-after-module'
        shared=' '.join(line['text'] for line in block[:code_rows[0][0]])
        specs=[]
        for n,(i,m) in enumerate(code_rows):
            stop=code_rows[n+1][0] if n+1<len(code_rows) else len(block)
            selected=block[i:stop]
            title=' '.join([shared,CODE.sub('',selected[0]['text'],count=1),*(line['text'] for line in selected[1:])]).strip()
            specs.append((m[1],title,selected))
    rows=[]
    for code,title,selected in specs:
        if not title or len(title)>240 or '(cid:' in title:return [],'title-not-complete'
        if any(ord(c)<32 or 0x3400<=ord(c)<0x4e00 or 0xd800<=ord(c)<=0xdfff for c in title):return [],'unreviewed-recovered-glyph'
        if re.search(r'[PBCU][0-9A-F]{4}',title):return [],'additional-code-in-title'
        language='zh' if re.search(r'[\u4e00-\u9fff]',title) else 'en'
        if family=='981' and language!='zh':return [],'chinese-header-not-recovered'
        rows.append({'generation':family,'code':code,'module':module,'moduleHeader':module_line['text'],
            'title':title,'language':language,'page':page,'codeTop':round(lines[code_index]['top'],4),
            'sectionBoundary':lines[boundary]['text'],'titleFonts':sorted({word['fontname'] for line in selected for word in line['words']}),
            'headerLines':[{'text':line['text'],'top':round(line['top'],4)} for line in lines[:boundary+1]],
            'referenceOnly':True,'ecuVersionQualified':False,'vehicleVerified':False,
            **({'sourceSection':source_section} if source_section else {}),
            **({'applicability':applicability} if applicability else {})})
    return rows,None


def reference_rejection(row):
    """A bundle filename or generic printed header cannot qualify a module."""
    if row['module'] not in (*MODULE_SECTIONS,'tcm'):
        return 'module-unresolved'
    if any(re.match(r'^(?:I\s+)?(?:Carrera only|Only for (?:Carrera|911|991)\b)',text,re.I)
           for text in row.get('applicability',[])):
        return 'extracted-header-non-target-applicability'
    if row['generation']=='982':
        section=row.get('sourceSection')
        if not section:return 'source-section-unavailable'
        if not section.startswith('9x1_'):return 'non-target-source-section'
        expected=MODULE_SECTIONS.get(row['module'])
        if not expected or not section.startswith(expected):return 'module-source-conflict'
        if row['module'] not in ('dme','pdk') and not re.fullmatch('[0-9A-F]{6}',row.get('code','')):
            return 'code-namespace-unqualified'
    return None


def main():
    import pdfplumber
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--981',dest='manual981',required=True,type=Path)
    parser.add_argument('--982',dest='manual982',required=True,type=Path)
    parser.add_argument('--output',type=Path,default=Path('data/seed/dtc/manuals/manual-titles.v1.json'))
    parser.add_argument('--evidence-dir',type=Path,default=Path('.local/obd-manual-title-build'))
    args=parser.parse_args();B=args.evidence_dir;B.mkdir(parents=True,exist_ok=True)
    if hasattr(signal,'alarm'):signal.alarm(900)
    started=time.monotonic()
    sources=[
        {'family':'981','path':args.manual981,'fileName':'Boxster BoxsterS BoxsterGTS (981).pdf','pages':4455,
         'sha256':'366e165914400046685fae9d36a91120eeac565597b4ced2ac2920804da58df5'},
        {'family':'982','path':args.manual982,'fileName':'Porsche 718 Boxster BoxsterS BoxsterGTS (982).pdf','pages':8160,
         'sha256':'8191d809b1b21938f97a80963bd619bd2ea0142186ef495acdc98351f3a8bbfa'},
    ]

    rows=[];rejects=[];counts=collections.Counter()
    for source in sources:
        path=Path(source['path']);hasher=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1<<20),b''):hasher.update(block)
        assert hasher.hexdigest()==source['sha256']
        family=source['family'];first,last=(3841,4455) if family=='981' else (4242,7915)
        with pdfplumber.open(path,pages=list(range(first,last+1))) as pdf:
            for page in pdf.pages:
                lines=header(page);row,reason=parse(lines,family,page.page_number)
                if row:
                    for record in row:record['sourceSha256']=source['sha256'];rows.append(record)
                    counts[family+':accepted']+=1
                else:
                    rejects.append({'generation':family,'page':page.page_number,'reason':reason,
                                    'headerLines':[{'text':line['text'],'top':round(line['top'],4)} for line in lines[:12]]})
                    counts[family+':'+reason]+=1
                page.close()
                if sum(counts.values())%100==0:print(json.dumps({'pages':sum(counts.values()),'accepted':len(rows),'seconds':round(time.monotonic()-started,1)}),flush=True)
    raw=json.dumps({'records':rows,'rejectedPages':rejects},ensure_ascii=True,separators=(',',':')).encode()
    with (B/'headers.json.gz').open('wb') as stream:
        with gzip.GzipFile(fileobj=stream,mode='wb',mtime=0) as compressed:compressed.write(raw)
    selected=[r for r in rows if reference_rejection(r) is None]
    excluded=[{k:r.get(k) for k in ('generation','module','moduleHeader','code','page','sourceSection','sourceSha256')}
              | {'reason':reference_rejection(r)} for r in rows if reference_rejection(r)]
    (B/'reference-admission.json').write_text(json.dumps({'excluded':excluded},ensure_ascii=True,indent=2)+'\n')
    value={'schemaVersion':1,'kind':'factory-manual-dtc-title-reference','referenceOnly':True,
           'ecuVersionQualified':False,'vehicleVerified':False,
           'sources':[{'generation':s['family'],'fileName':s['fileName'],'sha256':s['sha256'],'pages':s['pages']} for s in sources],
           'records':[{k:r[k] for k in ('generation','module','code','title','language','page','sourceSha256')}
                      | {k:r[k] for k in ('sourceSection','applicability') if r.get(k)} for r in selected]}
    for row in value['records']:
        if re.fullmatch('[0-9A-F]{6}',row['code']):row['codeNamespace']='manufacturer-hex'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    assert args.output.resolve() not in {source['path'].resolve() for source in sources}
    args.output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    summary={'passed':True,'scannedPages':sum(counts.values()),'acceptedHeaders':len(rows),'distributedReferences':len(selected),'referenceRejections':dict(collections.Counter(r['reason'] for r in excluded)),'outputSha256':hashlib.sha256(args.output.read_bytes()).hexdigest(),'counts':dict(counts),
             'modules':dict(collections.Counter((row['generation']+':'+str(row['module'])) for row in rows)),
             'oldTranslatedIndexUsed':False,'nativePdfFilesChanged':False,'deviceIO':False,'vehicleIO':False,
             'seconds':round(time.monotonic()-started,2)}
    (B/'extract-result.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary),flush=True)


if __name__=="__main__":
    main()
