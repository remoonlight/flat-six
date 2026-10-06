import tempfile
from pathlib import Path
import unittest
from scripts.diagnostics.adapter_preparation import resources, protocol_contracts


class AdapterPreparationTests(unittest.TestCase):
    def read(self, body):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'MDF.xml'; path.write_text(body, encoding='utf8')
            return resources(path)

    def test_pins_and_protocol_are_source_references_not_live_qualification(self):
        row = self.read('<MDF><RESOURCE><SHORT_NAME>candidate</SHORT_NAME><ID>123</ID>'
            '<PIN_ON_MODULE><PIN_ON_MODULE>6</PIN_ON_MODULE><PINTYPE IDREF="HI"/></PIN_ON_MODULE>'
            '<BUSTYPE IDREF="CAN"/><PROTOCOL IDREF="KWP"/></RESOURCE></MDF>')[0]
        self.assertEqual(row['pins'], [{'pin': '6', 'typeRef': 'HI'}])
        self.assertEqual(row['protocolRef'], 'KWP')
        self.assertFalse(row['executionEnabled']); self.assertFalse(row['silentReceiveQualified'])

    def test_duplicate_resources_and_external_entities_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.read('<MDF><RESOURCE><SHORT_NAME>x</SHORT_NAME></RESOURCE><RESOURCE><SHORT_NAME>x</SHORT_NAME></RESOURCE></MDF>')
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            self.read('<!DOCTYPE MDF SYSTEM "file:///secret"><MDF/>')

    def test_parameter_contract_resolves_protocol_override_and_keeps_missing_refs(self):
        body = ('<MDF><COMPARAM EID="rate"><ID>1</ID><SHORT_NAME>CP_Baudrate</SHORT_NAME>'
            '<DATA_TYPE>PDU_PT_UNUM32</DATA_TYPE><DEFAULT_VALUE>0</DEFAULT_VALUE></COMPARAM>'
            '<PROTOCOL EID="protocol"><SHORT_NAME>ISO_15765_3_on_ISO_15765_2</SHORT_NAME>'
            '<COMPARAM_REF><COMPARAM IDREF="rate"/><DEFAULT_VALUE>500000</DEFAULT_VALUE>'
            '<MIN_VALUE>0</MIN_VALUE></COMPARAM_REF>'
            '<COMPARAM_REF><COMPARAM IDREF="missing"/></COMPARAM_REF></PROTOCOL></MDF>')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'MDF.xml'; path.write_text(body, encoding='utf8')
            out = protocol_contracts(path)
            row = out['protocols'][0]
            self.assertEqual(row['parameters'][0]['defaultValue'], '500000')
            self.assertEqual(row['parameters'][0]['dataType'], 'PDU_PT_UNUM32')
            self.assertEqual(row['unresolvedReferences'], ['missing'])
            self.assertEqual(len(out['missingProtocols']), 2)
            self.assertFalse(row['executionEnabled']); self.assertFalse(row['runtimeAbiVerified'])
            path.write_text(body.replace('EID="protocol"', 'EID="rate"'), encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'duplicate-MDF-object'):
                protocol_contracts(path)


if __name__ == '__main__': unittest.main()
