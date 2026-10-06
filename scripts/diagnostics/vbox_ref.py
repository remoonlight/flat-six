"""Offline candidate layouts using Racelogic's frame-wide LSB numbering.

The manufacturer's CAN02 manual pp. 10–11 numbers Motorola bytes backwards;
VBOXTools manual p. 19 shows start 56, length 8 selecting the first byte.
This resolves numbering only: REF column binding and vehicle applicability
still require independent evidence. No runtime monitor imports this module.
"""
from decimal import Decimal

REFERENCES = [
    {'url': 'https://www.racelogic.co.uk/_downloads/vbox/Manuals/Input_Modules/RLVBCAN02_Manual.pdf',
     'pages': [10, 11], 'evidence': 'Motorola byte numbering runs from 63..56 to 7..0'},
    {'url': 'https://www.racelogic.co.uk/_downloads/vbox/Manuals/Software/VBOXTools%20Software%20Manual%20-%20English.pdf',
     'pages': [19], 'evidence': 'Motorola start 56, length 8 selects the first byte'},
]


def candidate_layout(row):
    if row.get('sourceId') not in ('vbox-ref-boxster-981', 'vbox-ref-cayman-981'):
        raise ValueError('unqualified-reference-source')
    if row.get('signed') is not False or any(row.get(key) not in (None, '')
                                           for key in ('multiplex', 'multiplexing')):
        raise ValueError('unqualified-reference-encoding')
    start, width, dlc = (row.get(k) for k in ('startBit', 'length', 'dlc'))
    if any(type(v) is not int for v in (start, width, dlc)) or dlc != 8:
        raise ValueError('invalid-reference-layout')
    if not 0 <= start < 64 or not 1 <= width <= 64 or start + width > 64:
        raise ValueError('invalid-reference-layout')
    order = row.get('byteOrder')
    if order not in ('motorola-vendor-label', 'intel-vendor-label'):
        raise ValueError('unqualified-reference-byte-order')
    for key in ('factor', 'offset'):
        try:
            value = Decimal(str(row[key]))
            if not value.is_finite():
                raise ValueError()
        except (KeyError, ArithmeticError, ValueError):
            raise ValueError('invalid-reference-scaling') from None
    # Positions are indexed by value bit, least significant first.
    positions = [((dlc - 1 - (start + i) // 8) if order == 'motorola-vendor-label'
                  else (start + i) // 8, (start + i) % 8) for i in range(width)]
    msb_byte, msb_bit = positions[-1]
    return {'sourceId': row['sourceId'], 'name': row.get('signalName'),
        'canIdInt': row.get('canIdInt'), 'rawRow': row.get('rawRow'),
        'sourceStartBit': start, 'sourceLength': width, 'sourceByteOrder': order,
        'valueBitPositions': positions,
        'vectorDbcStartBit': msb_byte * 8 + msb_bit if order == 'motorola-vendor-label' else start,
        'vectorDbcByteOrder': 0 if order == 'motorola-vendor-label' else 1,
        'dlc': dlc, 'factor': str(row['factor']), 'offset': str(row['offset']),
        'unit': row.get('unit'), 'columnBindingVerified': False,
        'numberingConvention': 'racelogic-frame-wide-field-lsb',
        'vehicleVerified': False, 'runtimeDecodeEnabled': False, 'executionEnabled': False}


def decode_candidate(row, frame):
    """Evaluate a complete synthetic/captured frame offline, without qualification."""
    layout = candidate_layout(row)
    if not isinstance(frame, bytes) or len(frame) != layout['dlc']:
        raise ValueError('reference-frame-length')
    raw = sum(((frame[byte] >> bit) & 1) << i
              for i, (byte, bit) in enumerate(layout['valueBitPositions']))
    return {'raw': raw, 'value': Decimal(raw) * Decimal(layout['factor']) + Decimal(layout['offset']),
            'layout': layout}
