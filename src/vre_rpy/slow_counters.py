"""Five slow cumulative counters (one record per ~10 s) — meaning NOT verified.

Stride-16 snapshot array: bytes 0-4 are u8 counters that rise while driving, hold in the garage,
saturate at 255 and sometimes partly reset; +8 is an f64 session time. Hypothesis: damage/wear
(5 values like shared memory ``carDamage[5]``). Exported as ``UNVERIFIED_COUNTER_0..4`` only;
never cite as damage or wear until checked against a shared-memory capture.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np


def decode_slow_counters(ex, car_index: Optional[int] = None) -> Optional[Dict[str, Any]]:
    from .processor import snapshot_arrays

    arrays = snapshot_arrays(ex, car_index)
    if arrays is None:
        return None
    arr = next((a for a in arrays if a.stride == 16 and a.count > 1), None)
    if arr is None:
        return None
    rec = np.frombuffer(arr.data[: arr.count * 16], dtype=np.uint8).reshape(arr.count, 16)
    t = rec[:, 8:16].copy().view("<f8").ravel()
    if not np.all(np.diff(t) >= 0):
        return None
    return {
        "status": "unverified",
        "time_s": t,
        "counters": {f"UNVERIFIED_COUNTER_{i}": rec[:, i].astype(np.float64) for i in range(5)},
        "note": "meaning unknown (hypothesis: damage/wear); verify against an acc_shm capture",
    }
