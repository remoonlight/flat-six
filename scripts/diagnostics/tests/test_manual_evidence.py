"""Manual DTC evidence: header ownership, continuation, 991 exclusion, glyphs."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.diagnostics.manual_evidence import (
    base_code,
    build_manual_evidence,
    codes_in_header_zone,
    extract_needs,
    group_pages,
    groups_to_entries,
    join_x431,
    main,
    parse_981_header,
    parse_982_header,
    recover_span,
)


def pages(rows):
    return [{"page1based": p, "text": t, "uncertainGlyph": u, "bodyModel": "boxster"} for p, t, u in rows]


class ManualEvidenceTests(unittest.TestCase):
    def test_arial_unicode_offset_and_simsun_cjk(self):
        src = chr(ord("诊") - 0x2D71)
        got, flags = recover_span(src, "ArialUnicodeMS", 13.6)
        self.assertEqual(got, "诊")
        self.assertFalse(flags)
        keep, f2 = recover_span("缸", "SimSun", 11.7)
        self.assertEqual(keep, "缸")
        self.assertFalse(f2)

    def test_unknown_glyph_not_overclaimed(self):
        got, flags = recover_span("\uE000", "SidisSymbole", 13.6)
        self.assertEqual(got, "\uFFFD")
        self.assertTrue(flags)
        text = "诊断DME 控制\nP000A00\n" + got
        needs = extract_needs(text, uncertain=True)
        # no needs section; flag must travel with any extracted needs
        hdr = parse_981_header("诊断DME 控制\nP000A00气缸组1\n诊断条\nI rpm")
        self.assertTrue(hdr["rawCodes"])
        entries = groups_to_entries(
            [
                {
                    "generation": "981",
                    "sourceId": "manual-981",
                    "ecuId": 1,
                    "heading": hdr["heading"],
                    "rawCodes": hdr["rawCodes"],
                    "pages": [3841],
                    "uncertainGlyph": True,
                    "bodyModel": "boxster",
                    "text": "诊断条\nI 发动机转速> 608",
                }
            ],
            [],
        )
        self.assertTrue(entries[0]["uncertainGlyph"])
        self.assertEqual(entries[0]["bodyEvidenceStatus"], "body_header_uncertain_glyph")
        self.assertFalse(entries[0]["procedureVerified"])

    def test_header_owns_page_not_cross_referenced_codes(self):
        text = (
            "P000A\nDiagnostic information - DME (DFI) control unit\n"
            "Bank 1 intake camshaft adjustment\n"
            "Diagnostic conditions\nI Engine speed 1200\n"
            "None of the following faults stored: P0010, P0020, P2088\n"
        )
        hdr = parse_982_header(text)
        self.assertEqual(hdr["rawCodes"][0], "P000A")
        self.assertNotIn("P0010", hdr["rawCodes"])
        zone = codes_in_header_zone(text, ignore_cross_ref=True)
        self.assertIn("P000A", zone)
        self.assertNotIn("P0010", zone)

    def test_continuation_pages_until_next_header(self):
        p3962 = "诊断DME 控制\nP030100\n气缸1\nP030200\n诊断条\nI 缺火\n故障设\nI x\n监控的部\nJ y\n故障查找\n"
        p3963 = "J 触点松动\nJ 电源或接地\n"
        p3843 = "诊断DME 控制\nP001000\n凸轮阀\n诊断条\nI z\n"
        g, toc = group_pages(
            pages([(3962, p3962, False), (3963, p3963, False), (3843, p3843, False)]),
            generation="981",
            source_id="manual-981",
            parser=parse_981_header,
        )
        self.assertEqual(len(g), 2)
        self.assertEqual(g[0]["pages"], [3962, 3963])
        self.assertIn("P030100", g[0]["rawCodes"])
        self.assertEqual(g[1]["pages"], [3843])
        self.assertIn("P001000", g[1]["rawCodes"])
        self.assertEqual(toc, [])

    def test_toc_only_not_body(self):
        toc = (
            "Porsche DTC Diagnostic Information\n"
            "Code                                                                     Document\n"
            "P000A00                                 P000A00\n"
            "P000C00                                 P000C00\n"
            "P001000                                 P001000\n"
            "P001100                                 P001100\n"
            "P001600                                 P001600\n"
            "P001800                                 P001800\n"
            "P002000                                 P002000\n"
            "P002100                                 P002100\n"
        )
        body = "诊断DME 控制\nP000A00进气\n诊断条\nI rpm\n"
        g, mentions = group_pages(
            pages([(3831, toc, False), (3841, body, False)]),
            generation="981",
            source_id="manual-981",
            parser=parse_981_header,
        )
        entries = groups_to_entries(g, mentions)
        body_e = [e for e in entries if e["rawCode"] == "P000A00" and e["bodyEvidenceStatus"] != "toc_only"]
        toc_e = [e for e in entries if e["rawCode"] == "P000C00"]
        self.assertEqual(body_e[0]["pages"][0], 3841)
        self.assertEqual(toc_e[0]["bodyEvidenceStatus"], "toc_only")
        self.assertNotIn(3831, body_e[0]["pages"])

    def test_991_excluded_and_generations_not_merged(self):
        e981 = {
            "generation": "981",
            "sourceId": "manual-981",
            "ecuId": 1,
            "heading": "P000A",
            "rawCodes": ["P000A00"],
            "pages": [3841],
            "uncertainGlyph": False,
            "bodyModel": "boxster",
            "text": "诊断条\nI 发动机转速> 608 转/分",
        }
        e982 = {
            "generation": "982",
            "sourceId": "manual-982",
            "ecuId": 1,
            "heading": "P000A",
            "rawCodes": ["P000A"],
            "pages": [4242],
            "uncertainGlyph": False,
            "bodyModel": "boxster",
            "text": "Diagnostic conditions\nI Engine speed 1,200 ... 6,000 rpm",
        }
        e991 = {
            "generation": "991",
            "sourceId": "manual-991",
            "ecuId": 1,
            "heading": "P000A",
            "rawCodes": ["P000A"],
            "pages": [1],
            "uncertainGlyph": False,
            "bodyModel": "carrera",
            "text": "Diagnostic conditions\nI 991-only",
        }
        entries = groups_to_entries([e981, e982, e991], [])
        gens = {e["generation"] for e in entries}
        self.assertIn("981", gens)
        self.assertIn("982", gens)
        n981 = [e["needs"][0]["itemsPreview"] if False else e["needs"] for e in entries if e["generation"] == "981"][0]
        n982 = [e["needs"] for e in entries if e["generation"] == "982"][0]
        self.assertNotEqual(json.dumps(n981), json.dumps(n982))
        filtered = [e for e in entries if e.get("sourceId") != "manual-991" and e.get("generation") in ("981", "982")]
        self.assertTrue(all(e["generation"] != "991" for e in filtered))

    def test_hash_mismatch_skips_heavy_facts_via_skip_build(self):
        empty = Path(tempfile.mkdtemp())
        fake = empty / "Boxster BoxsterS BoxsterGTS (981).pdf"
        fake.write_bytes(b"%PDF-1.4 fake")
        data = build_manual_evidence(
            wiring_981=None,
            wiring_982=None,
            wiring_gt4=None,
            manual_981=fake,
            manual_982=None,
            manual_991=None,
            variants_path=empty / "nope.jsonl",
            skip_heavy=True,
            include_extras=False,
            output_root=empty / "out",
        )
        ident = next(s for s in data["sources"] if s["id"] == "manual-981")
        self.assertFalse(ident["baselineMatch"])
        self.assertEqual(data["counts"]["bodyHeaderEntries"], 0)
        self.assertFalse(data["flags"]["991TargetImported"])

    def test_x431_join_is_relation_not_fit(self):
        entries = [
            {
                "generation": "981",
                "ecuId": 1,
                "rawCode": "P000A00",
                "baseCode": "P000A",
                "sourceId": "manual-981",
                "bodyEvidenceStatus": "body_header",
                "needs": [],
            }
        ]
        joins = join_x431(
            entries,
            {
                "unique": [
                    {
                        "ecuId": 1,
                        "x431Code": "P000A",
                        "variantHits": 3,
                        "joinScope": ["981"],
                        "membership": "confirmed",
                        "generationExplicit": True,
                        "unspecifiedGen": False,
                    },
                    {
                        "ecuId": 1,
                        "x431Code": "P9999",
                        "variantHits": 1,
                        "joinScope": ["981"],
                        "membership": "candidate",
                        "generationExplicit": True,
                        "unspecifiedGen": False,
                    },
                ]
            },
        )
        by = {(j["generation"], j["x431Code"]): j for j in joins}
        self.assertEqual(by[("981", "P000A")]["relation"], "base_code_equal")
        self.assertFalse(by[("981", "P000A")]["fittedClaim"])
        self.assertEqual(by[("981", "P9999")]["relation"], "x431_only_no_manual_body")
        self.assertNotIn(("982", "P000A"), by)

    def test_base_code_format(self):
        self.assertEqual(base_code("P000A00"), "P000A")
        self.assertEqual(base_code("P000A"), "P000A")
        self.assertIsNone(base_code("WM033500"))
        self.assertIsNone(base_code("P000A0"))
        self.assertIsNone(base_code("P000A0X"))

    def test_cli_missing_optional_inputs(self):
        dest = Path(tempfile.mkdtemp()) / "me.json"
        rc = main(
            [
                "--out",
                str(dest),
                "--wiring-981",
                "",
                "--wiring-982",
                "",
                "--wiring-gt4",
                "",
                "--manual-981",
                "",
                "--manual-982",
                "",
                "--manual-991",
                "",
                "--variants",
                str(Path(tempfile.mkdtemp()) / "missing.jsonl"),
                "--skip-heavy",
                "--output-root",
                str(Path(tempfile.mkdtemp()) / "cache"),
            ]
        )
        self.assertEqual(rc, 0)
        parsed = json.loads(dest.read_text(encoding="ascii"))
        self.assertEqual(parsed["schemaVersion"], 1)
        self.assertEqual(parsed["counts"]["groupCount"], 35)
        self.assertFalse(parsed["procedureVerified"])
        self.assertFalse(any(s.get("id", "").startswith("pdk") for s in parsed["sources"]))


    def test_needs_sections_do_not_bleed(self):
        text = (
            "诊断条\nI 发动机转速> 608\n"
            "故障设\nI 凸轮轴动态跟随设定点过慢\n"
            "监控的部\nJ 电磁凸轮轴调节阀\n"
            "故障查找\nJ 碎屑"
        )
        needs = {n["kind"]: n for n in extract_needs(text, False)}
        set_items = " ".join(needs["settingConditions"]["items"])
        self.assertNotIn("电磁凸轮轴", set_items)
        self.assertIn("凸轮轴动态", set_items)
        self.assertTrue(any("电磁" in x for x in needs["monitoredComponents"]["items"]))

    def test_sequential_981_headers_p000a_p000c_p0010(self):
        g, _ = group_pages(
            pages(
                [
                    (3841, "诊断DME 控制\nP000A00进气太慢\n诊断条\nI 608\n", True),
                    (3842, "诊断DME 控制\nP000C00排气太慢\n诊断条\nI 608\n", True),
                    (3843, "诊断DME 控制\nP001000凸轮阀\n诊断条\nI z\n", True),
                ]
            ),
            generation="981",
            source_id="manual-981",
            parser=parse_981_header,
        )
        self.assertEqual([x["rawCodes"][0] for x in g], ["P000A00", "P000C00", "P001000"])
        self.assertEqual(g[0]["pages"], [3841])
        self.assertTrue(g[0]["uncertainGlyph"])

    def test_unspecified_x431_joins_both_gens_as_candidate(self):
        entries = [
            {
                "generation": "981",
                "ecuId": 1,
                "rawCode": "P000A00",
                "baseCode": "P000A",
                "sourceId": "manual-981",
                "pages": [3841],
                "bodyEvidenceStatus": "body_header",
            },
            {
                "generation": "982",
                "ecuId": 1,
                "rawCode": "P000A",
                "baseCode": "P000A",
                "sourceId": "manual-982",
                "pages": [4242],
                "bodyEvidenceStatus": "body_header",
            },
        ]
        joins = join_x431(
            entries,
            {
                "unique": [
                    {
                        "ecuId": 1,
                        "x431Code": "P000A",
                        "variantHits": 10,
                        "joinScope": ["981", "982"],
                        "membership": "candidate",
                        "generationExplicit": False,
                        "unspecifiedGen": True,
                    }
                ]
            },
        )
        self.assertEqual({(j["generation"], j["membership"]) for j in joins}, {("981", "candidate"), ("982", "candidate")})
        self.assertTrue(all(j["manualHits"][0]["sourceId"] for j in joins))
        self.assertFalse(any(j["fittedClaim"] for j in joins))

    def test_join_rejects_mismatched_ecu_and_991(self):
        entries = [
            {
                "generation": "981",
                "ecuId": 9,
                "rawCode": "U000100",
                "baseCode": "U0001",
                "sourceId": "manual-991",
                "pages": [1],
                "bodyEvidenceStatus": "body_header",
            }
        ]
        joins = join_x431(
            entries,
            {
                "unique": [
                    {
                        "ecuId": 1,
                        "x431Code": "U0001",
                        "variantHits": 1,
                        "joinScope": ["981"],
                        "membership": "candidate",
                        "generationExplicit": True,
                        "unspecifiedGen": False,
                    }
                ]
            },
        )
        self.assertEqual(joins[0]["relation"], "x431_only_no_manual_body")

    def test_caption_honesty_and_hash_bound_pin_pairs(self):
        from scripts.diagnostics.manual_evidence import apply_caption_honesty, diagnostic_topology, overlay_wiring_pages
        from scripts.diagnostics.workshop_registry import bind_obd_gateway_facts

        pages = apply_caption_honesty(
            [
                {"page1based": 1, "printedCaption": "BOSE", "captionSource": "page_title_block", "visualVerified": False},
                {"page1based": 62, "printedCaption": "wrong", "captionSource": "toc", "visualVerified": False},
            ],
            "wiring-982",
        )
        self.assertEqual(pages[0]["captionSource"], "page_right_region_candidate")
        self.assertTrue(pages[0]["candidate"])
        self.assertFalse(pages[0]["visualVerified"])
        self.assertEqual(pages[1]["printedCaption"], "PSM")
        self.assertEqual(pages[1]["captionSource"], "page_title_block_hash_bound_visual")
        self.assertTrue(pages[1]["visualVerified"])

        missing = diagnostic_topology([], generation="981", source_id="wiring-981", identity={"baselineMatch": False})
        self.assertFalse(missing["obdToGateway"]["sourcePinAssignmentsVerified"])
        self.assertEqual(missing["obdToGateway"]["pinPairs"], [])
        self.assertFalse(missing["obdToGateway"]["liveContinuityVerified"])

        wrong = bind_obd_gateway_facts("wiring-981", {"baselineMatch": True, "sha256": "DEADBEEF"})
        self.assertIsNone(wrong)

        ok_ident = {
            "baselineMatch": True,
            "sha256": "40E4C38CFBEA097EDBA39E5ECA57A4C93D08A489F5248DED9985BA16153DD5BD",
        }
        bound = bind_obd_gateway_facts("wiring-981", ok_ident)
        self.assertIsNotNone(bound)
        topo = diagnostic_topology([], generation="981", source_id="wiring-981", identity=ok_ident)
        self.assertTrue(topo["obdToGateway"]["sourcePinAssignmentsVerified"])
        self.assertEqual(topo["obdToGateway"]["pages"], [11])
        self.assertEqual(topo["obdToGateway"]["printedModelYear"], "2013 (D)")
        pair = topo["obdToGateway"]["pinPairs"][0]
        self.assertEqual((pair["fromComponent"], pair["fromPin"], pair["toComponent"], pair["toPin"]), ("X001.1", "A6", "A010.1", "A19"))
        self.assertTrue(topo["obdToGateway"]["notADiagnosticAddress"])
        self.assertIn("A5", {p["lowPin"] for p in topo["gatewayConnectorPairs"]})
        over = overlay_wiring_pages([{"page1based": 11, "printedCaption": "网关"}], "wiring-981", ok_ident)
        self.assertTrue(over[0]["pinPairExtracted"])
        self.assertFalse(over[0]["liveContinuityVerified"])

    def test_caption_match_requires_compound_headlamp_token(self):
        from scripts.diagnostics.manual_evidence import caption_matches_ecu

        door = {"printedCaption": "驾驶员侧车门", "tocTitle": "09A 驾驶员侧车门"}
        left_lamp = {"printedCaption": "左侧前照灯", "tocTitle": "40A 左端线束前裙板"}
        self.assertFalse(caption_matches_ecu(47, door))
        self.assertTrue(caption_matches_ecu(47, left_lamp))
        axle = {"printedCaption": "PSM", "tocTitle": "51 右后桥线束 982 X440.2B1"}
        self.assertTrue(caption_matches_ecu(5, axle))
        self.assertFalse(caption_matches_ecu(47, axle))


if __name__ == "__main__":
    unittest.main()
