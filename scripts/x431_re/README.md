# X431 981/982 offline extractor

This directory contains the portable extractor for the 981/982 target data set. It parses already-decoded DSN/9X1, joins the MENU selection tree, and resolves GGP/GAG records. It does not perform YZJM decryption. It is an offline research tool: it creates no vehicle traffic, does not produce write payloads, and keeps every read candidate disabled.

## Inputs and outputs

Decoded source files remain local under `.local/`; the extractor does not depend on date-specific scratch scripts. It accepts decoded DSN and 9X1 data, the plaintext MENU, and optional GGP input. Without GGP, MENU labels remain empty. A representative invocation is:

```powershell
python scripts/x431_re/generate.py `
  --dsn-decoded <decoded-dsn> `
  --x9-decoded <decoded-9x1> `
  --menu <menu-bin> `
  --ggp <porsche-ggp> `
  --out-local <local-output-directory> `
  --out-summary data/seed/diagnostics/coverage-981-982.v1.json
```

Run `python scripts/x431_re/generate.py --help` for the authoritative parameter list. MENU labels are parsed directly from GGP; `--menu-labels` is a legacy unused parameter. An unsupported MENU layout, including a version mismatch, is rejected explicitly. The repository summary is deliberately small; private per-variant output and decoded source material remain in `.local/`. The local output directory contains `variants.jsonl`, `summary.json`, and `audit-excluded.jsonl`.

## Scope and provenance

The target data set is 981/982 only. Other model definitions are not exported, although source hashes, native reader offsets, file-layout findings, and validation methods may be reused. Every output row retains its source file, source hash, and offset. `confirmed` means only a generation label supports membership; `candidate` requires ECU identity matching before physical fit can be asserted.

MENU reachability means a system is selectable. It does not establish that all variants physically contain that ECU. The extractor must preserve excluded, candidate, and confirmed membership separately.

## Record interpretation

GAG namespaces are authoritative for labels, formulas, units, and text. Do not infer a namespace from an ID high byte or an `F0` prefix. A source entry with an empty string is distinct from a missing entry.

The native readers establish these offline record shapes:

| Pool | Record shape |
|---|---|
| identity | 7-byte prefix + `u16` byte length + NUL-terminated ID chain + 7-byte suffix |
| coding | 7-byte prefix + `u16` byte length + NUL-terminated ID chain + 8-byte suffix; final four suffix bytes are an EXPRESS formula ID |
| measurement | group header is `u32` count plus one format byte; format 0 has observed 16 B rows: SID, `u32le` PID, DSTREAM ID, byte offset, bit offset, EXPRESS ID. Formats 1–3 have native 19 B structures with a 4-byte SID, but no current target-row observation |

SID 31 is an internal branch alias that COMM maps to wire SID 22. Never turn it into a routine-control 31 request. Within-byte coding bit positions describe source definitions only: they do not prove installation, a safe write operation, write payload, readback, or recovery.

## Verification boundary

Keep source hashes and native-offset evidence with the local research inputs. Static decoding, capture comparison, and source structure do not establish an independent live vehicle capability. Do not export other-model detail, VINs, device identifiers, derived keys, or proprietary binaries.
