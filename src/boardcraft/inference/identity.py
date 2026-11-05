"""Backend-independent content identity for a validated policy tensor subset."""

import hashlib
import re
import struct
from collections.abc import Mapping
import numpy as np
from .spec import MAX_WEIGHT_BYTES


def canonical_tensor_digest(weights):
    """Hash sorted names, framed shapes and finite float32 little-endian C bytes.

    This identifies policy content, independently of an archive or backend.
    Callers still validate the architecture's exact policy tensor allowlist.
    """
    if not isinstance(weights, Mapping) or not 0 < len(weights) <= 65535:
        raise ValueError("Expected a bounded nonempty policy tensor mapping")
    if any(
        not isinstance(name, str)
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,255}", name)
        for name in weights
    ):
        raise ValueError("Invalid policy tensor name")
    result = hashlib.sha256(b"boardcraft-policy-tensors-v1\x00")
    result.update(struct.pack("<I", len(weights)))
    total = 0
    for name in sorted(weights):
        array = weights[name]
        if (
            not isinstance(array, np.ndarray)
            or array.dtype.kind != "f"
            or array.dtype.itemsize != 4
            or not 1 <= array.ndim <= 32
            or any(size < 1 or size > MAX_WEIGHT_BYTES // 4 for size in array.shape)
        ):
            raise ValueError("Policy tensors must be finite float32 arrays")
        total += array.nbytes
        if total > MAX_WEIGHT_BYTES:
            raise ValueError("Policy tensor content exceeds format size limit")
        if not np.isfinite(array).all():
            raise ValueError("Policy tensors must be finite float32 arrays")
        encoded = name.encode("utf-8")
        result.update(struct.pack("<I", len(encoded)))
        result.update(encoded)
        result.update(struct.pack("<I", array.ndim))
        for size in array.shape:
            result.update(struct.pack("<Q", size))
        result.update(struct.pack("<Q", array.nbytes))
        canonical = np.asarray(array, dtype="<f4", order="C")
        result.update(memoryview(canonical).cast("B"))
    return result.hexdigest()
