"""Local-only byte-exact replay/selection commitments for independent review.

This module never reads a device, decodes CAN, writes output, or transmits data.
Use only inside the private R-log analysis environment. Hashes are *not*
independent attestations and must never be committed with private route data.
"""

from __future__ import annotations

import hashlib
import struct

SOURCE = "1f7233cb983f3c13fdbba47a86f8fbc796ad9211"
_ROUTE_DOMAIN = b"ram1500/byte-exact-route/v1\x00"
_WINDOW_DOMAIN = b"ram1500/frozen-carstate-selection/v1\x00"
_MAX_U32 = 2**32 - 1
_MAX_U64 = 2**64 - 1


def _index(value, maximum=_MAX_U32):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("invalid nonnegative bounded sample/segment index")
    return value


def _digest(value):
    if not (type(value) is str and len(value) == 64 and
            all(c in "0123456789abcdef" for c in value)):
        raise ValueError("invalid canonical digest")
    return bytes.fromhex(value)


def hash_route_inputs(segments, *, source_commit=SOURCE):
    """Hash exact private serialized replay bytes with an explicit segment frame.

    segments: ordered iterable of (integer segment index, iterable of bytes
    chunks). Feed the byte-identical frozen input used by baseline/candidate,
    not a different decode/recompression. Segment indices must be increasing;
    chunk boundaries do not change the digest. An independent reviewer must
    separately validate the source build, schema, route and acquisition.
    """
    if source_commit != SOURCE:
        raise ValueError("recorded-build source mismatch")
    overall = hashlib.sha256(_ROUTE_DOMAIN + bytes.fromhex(SOURCE))
    previous_segment = -1
    count = 0
    for segment, chunks in segments:
        _index(segment)
        if segment <= previous_segment:
            raise ValueError("segments out of order or repeated")
        previous_segment = segment
        sub = hashlib.sha256()
        size = 0
        for chunk in chunks:
            if type(chunk) is not bytes:
                raise ValueError("route input must be binary bytes")
            size += len(chunk)
            if size > _MAX_U64:
                raise ValueError("route input oversized")
            sub.update(chunk)
        if not size:
            raise ValueError("empty replay input segment")
        overall.update(struct.pack(">IQ", segment, size))
        overall.update(sub.digest())
        count += 1
    if not count:
        raise ValueError("missing private replay segments")
    return overall.hexdigest()


def hash_eligible_window(route_input_sha256, sample_markers):
    """Hash frozen eligible carState anchors: (segment, ordinal, logMonoTime ns).

    Anchors must be selected before fitting candidates and must identify the
    same ordinal and time in the exact binary route input. Require increasing
    order/unique timestamps per segment; invalid/ambiguous anchors fail closed.
    No raw timestamps or segment IDs are emitted, just a private commitment.
    """
    result = hashlib.sha256(_WINDOW_DOMAIN + _digest(route_input_sha256))
    previous_key = (-1, -1)
    previous_time = -1
    count = 0
    for segment, ordinal, mono_ns in sample_markers:
        _index(segment)
        _index(ordinal)
        _index(mono_ns, _MAX_U64)
        key = (segment, ordinal)
        if key <= previous_key:
            raise ValueError("duplicate or out-of-order eligible sample")
        if segment != previous_key[0]:
            previous_time = -1
        if mono_ns <= previous_time:
            raise ValueError("nonmonotonic or duplicate eligible timestamp")
        result.update(struct.pack(">IIQ", segment, ordinal, mono_ns))
        previous_key = key
        previous_time = mono_ns
        count += 1
    if not count:
        raise ValueError("missing frozen eligible observations")
    return result.hexdigest()
