"""Explicit C-locale numeric foundation models for isolated X431 research.

Integer saturation and decimal-prefix syntax are checked against pinned Bionic
sources. No retained Android libc executes; errno/TLS and FP flags are not
modified. Hex floats, special floats, subnormals and overflow remain boundaries.
"""
import re
import struct

UPSTREAM = dict(
    integer=dict(url='https://android.googlesource.com/platform/bionic/+/9dc37b9439063df2b0db6d1b3a37b491bc3cd427/libc/bionic/strtol.cpp',
        sha256='06048965e39d0b4aa0342f3289bcbe70165d4b90264d4e94c3d9c3b58a5cdc57'),
    decimal=dict(url='https://android.googlesource.com/platform/bionic/+/9dc37b9439063df2b0db6d1b3a37b491bc3cd427/libc/upstream-openbsd/lib/libc/gdtoa/strtod.c',
        sha256='312de0f406cd2df552311a4bd6a776656916bcf094bfcf10a5988862ba65762b'))
SPACE=b' \t\r\n\v\f'
DECIMAL=re.compile(rb'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')
GUARDS=dict(foundationModeled=True,retainedAndroidLibcExecuted=False,
    actualLibcVersionMatched=False,hostLibcCalled=False,androidErrnoTlsModified=False,
    sourceErrnoEffectsQualified=False,floatingExceptionFlagsModeled=False,
    vehicleVerified=False,codingWriteQualified=False,executionEnabled=False,writePayload=None)


def _input(data,maximum_bytes=1024):
    if type(maximum_bytes) is not int or maximum_bytes not in (1024,8192):
        raise ValueError('numeric-foundation-storage-budget-unreviewed')
    if type(data) is not bytes or len(data)>maximum_bytes or b'\0' in data or not data.isascii():
        raise ValueError('numeric-foundation-bounded-ascii-required')
    return len(data)-len(data.lstrip(SPACE))


def integer_prefix(data,radix,signed,bits,*,maximum_bytes=1024):
    start=_input(data,maximum_bytes)
    if type(radix) is not int or radix not in (2,10,16) or type(signed) is not bool or type(bits) is not int or bits not in (32,64):
        raise ValueError('numeric-foundation-integer-contract-unreviewed')
    at=start;negative=data[at:at+1]==b'-'
    if data[at:at+1] in (b'+',b'-'):at+=1
    digits=b'0123456789abcdef';lower=data.lower()
    if radix==16 and lower[at:at+2]==b'0x' and at+2<len(data) and lower[at+2] in digits:at+=2
    first=at;value=0
    while at<len(data) and lower[at] in digits:
        digit=digits.index(lower[at])
        if digit>=radix:break
        value=value*radix+digit;at+=1
    consumed=at if at>first else 0
    limit=((1<<(bits-1)) if negative else (1<<(bits-1))-1) if signed else (1<<bits)-1
    overflow=value>limit
    if overflow:value=limit
    if negative and (signed or not overflow):value=-value
    return dict(valueBits=value&((1<<bits)-1),consumedBytes=consumed,
        anyDigits=consumed>0,overflow=overflow,sourceExpectedErrno='ERANGE' if overflow else None,
        radix=radix,signed=signed,bits=bits,**GUARDS)


def _round_even(numerator,denominator):
    q,r=divmod(numerator,denominator)
    return q+(2*r>denominator or 2*r==denominator and q&1)


def _normal_binary64(numerator,denominator,negative):
    sign=int(negative)<<63
    if numerator==0:return sign
    exponent=numerator.bit_length()-denominator.bit_length()
    below=(numerator < denominator<<exponent) if exponent>=0 else (numerator<<-exponent < denominator)
    if below:exponent-=1
    if exponent < -1022:raise ValueError('numeric-foundation-subnormal-unreviewed')
    shift=52-exponent
    mantissa=_round_even(numerator<<shift,denominator) if shift>=0 else _round_even(numerator,denominator<<-shift)
    if mantissa==1<<53:mantissa>>=1;exponent+=1
    if exponent>1023:raise ValueError('numeric-foundation-double-overflow-unreviewed')
    return sign|((exponent+1023)<<52)|(mantissa-(1<<52))


def decimal_prefix(data,fpscr=0):
    start=_input(data)
    if type(fpscr) is not int or not 0<=fpscr<=0xffffffff or fpscr&0x00c00000:
        raise ValueError('numeric-foundation-rounding-mode-unreviewed')
    unsigned=data[start:]
    if unsigned[:1] in (b'+',b'-'):unsigned=unsigned[1:]
    if unsigned.lower().startswith((b'0x',b'inf',b'nan')):
        raise ValueError('numeric-foundation-hex-or-special-float-unreviewed')
    match=DECIMAL.match(data,start)
    if match is None:
        bits=0;consumed=0
    else:
        token=match.group();consumed=match.end();negative=token.startswith(b'-')
        if token[:1] in (b'+',b'-'):token=token[1:]
        parts=re.split(rb'[eE]',token);mantissa=parts[0];exponent=int(parts[1]) if len(parts)>1 else 0
        if abs(exponent)>4096:raise ValueError('numeric-foundation-decimal-exponent-unreviewed')
        digits=mantissa.replace(b'.',b'');fraction=len(mantissa)-mantissa.index(b'.')-1 if b'.' in mantissa else 0
        exponent-=fraction;number=int(digits)
        numerator,denominator=(number*10**exponent,1) if exponent>=0 else (number,10**-exponent)
        bits=_normal_binary64(numerator,denominator,negative)
    raw=struct.pack('<Q',bits)
    return dict(returnedDoubleHex=raw.hex().upper(),returnedWords=list(struct.unpack('<II',raw)),
        consumedBytes=consumed,anyDigits=match is not None,rounding='nearest-ties-to-even',
        normalFiniteOrZeroOnly=True,**GUARDS)
