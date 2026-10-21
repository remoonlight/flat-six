from copy import deepcopy
import unittest
from unittest.mock import patch
from scripts.diagnostics import coding_draft as worker


class OwnedDraftWorkerTests(unittest.TestCase):
    def setUp(self):
        self.request=dict(context=dict(profileId='source-profile',generation='981',ecuId=47,
            sourceHash='a'*64,scopeHash='b'*64,contract={'owned':'contract'},
            blocks=[dict(requestHex='2204A1',dataHex='A5')]),edits=[])
        self.plan=dict(definitionSha256='a'*64,completeScopeSource={'sha256':'b'*64},
            requestGroups=[dict(requestHex='2204A1',expectedTotalBytes=1)])
        self.patches=[patch.object(worker,'source_coding_plan',return_value=self.plan),
            patch.object(worker,'_source_profile',return_value={'source':'immutable'}),
            patch.object(worker,'coding_display_fields',return_value=[{'id':'1','editable':True}]),
            patch.object(worker,'coding_edit_draft',return_value={'draft':'source-only'})]
        self.mocks=[p.start() for p in self.patches]
        for p in self.patches:self.addCleanup(p.stop)

    def test_owned_source_context_produces_fields_without_execution_permission(self):
        result=worker.handle(self.request)
        self.assertTrue(result['ok']);self.assertFalse(result['executionEnabled']);self.assertIsNone(result['writePayload'])
        self.assertNotIn('draft',result);self.mocks[-1].assert_not_called()
        self.assertTrue(self.mocks[2].call_args.kwargs['draft_choices'])

    def test_preview_only_passes_owned_blocks_and_edits_to_source_builder(self):
        self.request['edits']=[dict(fieldId='1',optionId='source-option-1')]
        result=worker.handle(self.request)
        self.assertTrue(result['ok']);self.assertEqual(result['draft'],{'draft':'source-only'})
        self.assertEqual(self.mocks[-1].call_args.args[1:3],(self.request['context']['blocks'],self.request['edits']))

    def test_scope_definition_length_duplicates_and_extra_blocks_fail_before_display(self):
        for change in (lambda r:r['context'].update(sourceHash='c'*64),
            lambda r:r['context'].update(scopeHash='c'*64),
            lambda r:r['context']['blocks'][0].update(dataHex='A500'),
            lambda r:r['context']['blocks'][0].update(requestHex='2204A2'),
            lambda r:r['context']['blocks'].append(deepcopy(r['context']['blocks'][0])),
            lambda r:r['context']['blocks'][0].update(formula='external')):
            request=deepcopy(self.request);change(request)
            self.assertFalse(worker.handle(request)['ok']);self.mocks[2].assert_not_called()

    def test_no_supplied_formula_or_file_or_unqualified_source_is_accepted(self):
        for extra in ({'formula':'external'},{'path':'external'},{'blocks':[]}):
            self.assertFalse(worker.handle({**self.request,**extra})['ok'])
        with patch.object(worker,'source_coding_plan',side_effect=ValueError('source-unqualified')):
            self.assertEqual(worker.handle(self.request)['error'],'source-unqualified')
        self.mocks[2].assert_not_called()


if __name__=='__main__':unittest.main()
