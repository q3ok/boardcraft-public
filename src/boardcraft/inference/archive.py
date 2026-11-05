"""Read the bounded, stored-only NPZ envelope written by numpy.savez."""

from binascii import crc32
import hashlib
import io
import math
import struct
import numpy as np
from .spec import MAX_WEIGHT_BYTES

# Standard ZIP local header, central entry and end record (little endian).
_LOCAL = struct.Struct("<4s5H3I2H")
_CENTRAL = struct.Struct("<4s6H3I5H2I")
_END = struct.Struct("<4s4H2IH")


def read_stored_arrays(path, shapes, expected_digest):
    """Account for every byte before loading any tensor. No decompressor runs."""
    names = {(key + ".npy").encode("ascii"): key for key in shapes}
    headers = {}
    sizes = {}
    for name, key in names.items():
        header = io.BytesIO()
        np.lib.format.write_array_header_1_0(
            header,
            {
                "descr": np.dtype(np.float32).str,
                "shape": shapes[key],
                "fortran_order": False,
            },
        )
        headers[name] = header.getvalue()
        sizes[name] = len(headers[name]) + 4 * math.prod(shapes[key])

    # NumPy reserves one 20-byte ZIP64 size field on each local header.
    central_start = sum(_LOCAL.size + len(n) + 20 + sizes[n] for n in names)
    central_size = sum(_CENTRAL.size + len(n) for n in names)
    total_size = central_start + central_size + _END.size
    if len(names) > 65535 or total_size > MAX_WEIGHT_BYTES + 1024 * 1024:
        raise ValueError("Archive exceeds format size limits")
    # Refuse a mismatched physical length before allocating the file buffer.
    with path.open("rb") as stream:
        if stream.seek(0, 2) != total_size:
            raise ValueError("Archive length differs from the declared tensor layout")
        stream.seek(0)
        data = stream.read(total_size + 1)
    if hashlib.sha256(data).hexdigest() != expected_digest:
        raise ValueError("Weight digest mismatch")
    end = _END.pack(
        b"PK\x05\x06", 0, 0, len(names), len(names), central_size, central_start, 0
    )
    if len(data) != central_start + central_size + len(end) or not data.endswith(end):
        raise ValueError("Unexpected archive bounds or end record")

    cursor, central = 0, central_start
    members = {}
    view = memoryview(data)
    max_name = max(map(len, names))
    for _ in names:
        if cursor + _LOCAL.size > central_start:
            raise ValueError("Missing array member")
        fields = _LOCAL.unpack_from(data, cursor)
        crc, name_length = fields[6], fields[9]
        if not 0 < name_length <= max_name:
            raise ValueError("Invalid array member name length")
        name_start = cursor + _LOCAL.size
        name = data[name_start : name_start + name_length]
        if name not in names or name in members:
            raise ValueError("Unknown or duplicate array member")
        size = sizes[name]
        local = _LOCAL.pack(
            b"PK\x03\x04", 45, 0, 0, 0, 33, crc, 0xFFFFFFFF, 0xFFFFFFFF, len(name), 20
        )
        extra = struct.pack("<HHQQ", 1, 16, size, size)
        prefix = local + name + extra
        if not data.startswith(prefix, cursor):
            raise ValueError("Only canonical stored array members are supported")

        # NumPy-created ZipInfo uses DOS or Unix host and fixed attributes.
        if central + _CENTRAL.size > len(data) - _END.size:
            raise ValueError("Missing archive directory entry")
        creator = _CENTRAL.unpack_from(data, central)[1]
        if creator not in (45, (3 << 8) | 45):
            raise ValueError("Unexpected archive creator flags")
        directory = (
            _CENTRAL.pack(
                b"PK\x01\x02",
                creator,
                45,
                0,
                0,
                0,
                33,
                crc,
                size,
                size,
                len(name),
                0,
                0,
                0,
                0,
                0o600 << 16,
                cursor,
            )
            + name
        )
        if not data.startswith(directory, central):
            raise ValueError("Invalid archive directory metadata")
        central += len(directory)
        start = cursor + len(prefix)
        cursor = start + size
        payload = view[start:cursor]
        if (
            cursor > central_start
            or not data.startswith(headers[name], start)
            or crc32(payload) != crc
        ):
            raise ValueError("Invalid array header, size or checksum")
        members[name] = payload
    if cursor != central_start or central != len(data) - _END.size:
        raise ValueError("Unaccounted archive contents")
    return {
        names[name]: np.load(io.BytesIO(payload), allow_pickle=False)
        for name, payload in members.items()
    }
