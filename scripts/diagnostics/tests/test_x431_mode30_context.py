import io
import json
import struct
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from scripts.diagnostics.x431_version_response import (
    audit_mode30_filter_context, audit_mode30_non4_request_packet, main, VERSION_GUARDS)


def record(pairs=((0x4000,0x44c0,1),(0xa320,0x6040,1),(0xa320,0x6040,4)), mode=0x30):
    data=bytes((mode,3,len(pairs)))
    for tx,rx,subtype in pairs:
        data+=struct.pack('<IIBB',tx,rx,subtype,0)
    return (data+bytes(13)).hex()


def packet(length=3, pair=0):
    payload=bytes.fromhex('22f19e') if length==3 else bytes(range(length))
    return dict(communicationRecordHex=record(),pairIndex=pair,
                requestAreaHex=(bytes([length])+payload).hex())


class Mode30ContextTests(unittest.TestCase):
    def test_selected_pair_word_and_source_order(self):
        for index,expected in [(0,0x400044c0),(1,0xa3206040),(2,0xa3206040)]:
            result=audit_mode30_filter_context(dict(communicationRecordHex=record(),pairIndex=index))
            self.assertEqual(result['sourceFilterWordCandidate'],expected)
            self.assertEqual(result['sourceTokenFilterOffset'],0x65c+index*4)
            self.assertEqual(result['sourceFilterSetterCallSite'],0x19eec)
            self.assertTrue(result['filterAssignmentBeforeSelectedRequestSourceVerified'])
            self.assertFalse(result['sourceInitializerMode30BranchWritesFilter'])
            self.assertFalse(result['actualFilterSetterCallProven'])
            self.assertFalse(result['runtimeFilterStableAcrossCallbacksProven'])
            for key,value in VERSION_GUARDS.items():self.assertEqual(result[key],value)

    def test_stored_tx_uses_low_halfword_without_can_address_conversion(self):
        r=audit_mode30_filter_context(dict(communicationRecordHex=record(((0x12344000,0x567844c0,1),)),pairIndex=0))
        self.assertEqual(r['sourceFilterWordCandidate'],0x400044c0)
        self.assertFalse(r['hardwareAddressConversionQualified'])

    def test_literal_internal_provider_arguments_from_two_pairs(self):
        for index,expected in [(0,'55AA08610103400022F19E66'),(1,'55AA08610103A32022F19EA5')]:
            r=audit_mode30_non4_request_packet(packet(pair=index))
            self.assertEqual(r['providerCommandHexCandidate'],expected)
            self.assertEqual(r['providerCommandBytesCandidate'],12)
            self.assertEqual(r['providerCapacity'],1024)
            for key in ('canFrameLayoutQualified','nativeRequestScratchBoundsQualified','providerRuntimeBindingQualified'):
                self.assertFalse(r[key])
            for key,value in VERSION_GUARDS.items():self.assertEqual(r[key],value)

    def test_all_length_bytes_refuse_overlap_and_wrap(self):
        for length in range(256):
            with self.subTest(length=length):
                r=audit_mode30_non4_request_packet(packet(length))
                self.assertEqual(r['sourceLengthArithmeticWrapped'],length>=250)
                self.assertEqual(r['sourceCommandOverlapsReceiveScratch'],length>=56)
                self.assertEqual(r['sourcePayloadFullyRepresented'],length<=55)
                if length<=55:
                    frame=bytes.fromhex(r['providerCommandHexCandidate'])
                    self.assertEqual(len(frame),length+9)
                    self.assertEqual(frame[8:-1],bytes.fromhex(packet(length)['requestAreaHex'])[1:])
                    checksum=0
                    for byte in frame[2:-1]:checksum^=byte
                    self.assertEqual(frame[-1],checksum)
                else:self.assertIsNone(r['providerCommandHexCandidate'])

    def test_subtype_four_never_uses_nonfour_packet(self):
        r=audit_mode30_filter_context(dict(communicationRecordHex=record(),pairIndex=2))
        self.assertEqual(r['communicationPairSourceAudit']['nativeResponseEntryCandidate'],0x10da4)
        with self.assertRaisesRegex(ValueError,'subtype-mismatch'):
            audit_mode30_non4_request_packet(packet(pair=2))

    def test_short_source_is_not_padded_and_trailing_bytes_are_explicit(self):
        p=packet();p['requestAreaHex']='0322'
        with self.assertRaisesRegex(ValueError,'extent-incomplete'):audit_mode30_non4_request_packet(p)
        p=packet();p['requestAreaHex']+='abcd'
        r=audit_mode30_non4_request_packet(p)
        self.assertEqual(r['sourceTrailingBytesIgnored'],2)
        self.assertEqual(r['providerCommandHexCandidate'],'55AA08610103400022F19E66')

    def test_record_and_selector_rejections(self):
        examples=[{},dict(communicationRecordHex=record(),pairIndex=True),
                  dict(communicationRecordHex=record(),pairIndex=-1),
                  dict(communicationRecordHex=record(),pairIndex=3),
                  dict(communicationRecordHex=record(mode=0x10),pairIndex=0),
                  dict(communicationRecordHex=record()+'00',pairIndex=0),
                  dict(communicationRecordHex=record()[:-2],pairIndex=0)]
        for p in examples:
            with self.subTest(p=p),self.assertRaises(ValueError):audit_mode30_filter_context(p)

    def test_packet_fields_and_hex_rejections(self):
        for p in [{},packet()|{'ecuQualified':True},packet()|{'requestAreaHex':''},
                  packet()|{'requestAreaHex':'abc'},packet()|{'requestAreaHex':'zz'},
                  packet()|{'requestAreaHex':bytes(4)},packet()|{'requestAreaHex':'00'*257}]:
            with self.subTest(p=p),self.assertRaises(ValueError):audit_mode30_non4_request_packet(p)

    def test_cli_accepts_both_and_rejects_bad_subtype(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'observation.json'
            for kind,p in [('mode30-filter-context',dict(communicationRecordHex=record(),pairIndex=2)),
                           ('mode30-non4-request-packet',packet())]:
                path.write_text(json.dumps(p));out=io.StringIO()
                with redirect_stdout(out):self.assertEqual(main(['--kind',kind,'--observation-json',str(path)]),0)
                self.assertFalse(json.loads(out.getvalue())['vehicleIO'])
            path.write_text(json.dumps(packet(pair=2)))
            with redirect_stderr(io.StringIO()),redirect_stdout(io.StringIO()),self.assertRaises(SystemExit) as result:
                main(['--kind','mode30-non4-request-packet','--observation-json',str(path)])
            self.assertEqual(result.exception.code,2)


if __name__=='__main__':unittest.main()
