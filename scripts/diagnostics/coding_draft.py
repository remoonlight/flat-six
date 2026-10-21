"""Private main-process coding draft worker. No device or vehicle operations.

Renderer cannot supply this worker's complete original blocks or contract.
They come from a released source-qualified read after mandatory first backup.
"""
import json
import re
import sys

from .manufacturer_coding import source_coding_plan
from .manufacturer_coding_fields import coding_display_fields,coding_edit_draft
from .pdk_read import _source_profile

MAX_JSON=8*1024*1024


def handle(request):
    try:
        if (not isinstance(request,dict) or set(request)!={'context','edits'}
                or not isinstance(request['context'],dict) or not isinstance(request['edits'],list)):
            raise ValueError('coding-draft-invalid')
        context=request['context']
        if set(context)!={'profileId','generation','ecuId','sourceHash','scopeHash','contract','blocks'}:
            raise ValueError('coding-draft-invalid')
        profile=dict(profileId=context['profileId'],selectedEcuId=context['ecuId'],requestedGeneration=context['generation'])
        contract=context['contract']
        plan=source_coding_plan(profile,contract)
        if (context['sourceHash']!=plan['definitionSha256']
                or context['scopeHash']!=plan.get('completeScopeSource',{}).get('sha256')):
            raise ValueError('coding-draft-source-mismatch')
        blocks=context['blocks'];expected={g['requestHex']:g['expectedTotalBytes'] for g in plan['requestGroups']}
        if (not isinstance(blocks,list) or len(blocks)!=len(expected)
                or len({b['requestHex'] for b in blocks})!=len(expected)):
            raise ValueError('coding-draft-original-incomplete')
        for block in blocks:
            if (not isinstance(block,dict) or set(block)!={'requestHex','dataHex'}
                    or block['requestHex'] not in expected or type(expected[block['requestHex']]) is not int
                    or not isinstance(block['dataHex'],str) or not re.fullmatch(r'(?:[0-9A-F]{2})+',block['dataHex'])
                    or len(block['dataHex'])!=2*expected[block['requestHex']]):
                raise ValueError('coding-draft-original-incomplete')
        source=_source_profile(context['profileId'],context['ecuId'],generation=context['generation'])
        fields=coding_display_fields(source,blocks,lambda:None,draft_choices=True)
        result=dict(ok=True,fields=fields,profileId=context['profileId'],sourceHash=plan['definitionSha256'],
                    scopeHash=context['scopeHash'],executionEnabled=False,writePayload=None)
        if request['edits']:
            draft=coding_edit_draft(source,blocks,request['edits'],lambda:None)
            result['draft']=draft
        return result
    except (ValueError,KeyError,TypeError,AttributeError,OSError) as error:
        return dict(ok=False,error=str(error) or 'coding-draft-failed',executionEnabled=False,writePayload=None)


def main():
    raw=sys.stdin.buffer.read(MAX_JSON+1)
    try:
        if len(raw)>MAX_JSON:raise ValueError('coding-draft-input-limit')
        result=handle(json.loads(raw))
    except (ValueError,UnicodeError):
        result=dict(ok=False,error='coding-draft-invalid',executionEnabled=False,writePayload=None)
    sys.stdout.write(json.dumps(result,ensure_ascii=False)+'\n')


if __name__=='__main__':main()
