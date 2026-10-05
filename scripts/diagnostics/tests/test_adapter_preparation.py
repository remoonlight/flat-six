import tempfile
from pathlib import Path
import unittest
from scripts.diagnostics.adapter_preparation import resources


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


if __name__ == '__main__': unittest.main()
