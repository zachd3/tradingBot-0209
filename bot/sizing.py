from __future__ import annotations

import math
from dataclasses import dataclass

from bot.models import Instrument


@dataclass
class SizeResult:
    sz: str
    approx_notional: float


def _to_float(x: str | None, default: float = 0.0) -> float:
    try:
        return float(x) if x is not None else default
    except Exception:
        return default


def compute_contract_size(
    *,
    instrument: Instrument,
    last_px: float,
    target_notional: float,
) -> SizeResult:
    """Compute OKX `sz` (contract quantity) for SWAP instruments.

    For linear USDT-settled perps, a rough approximation is:
      notional ~= sz * ctVal * last_px

    Notes:
    - OKX sizing rules can vary across instruments; we fetch `ctVal`, `lotSz`, `minSz`.
    - We round *down* to lot size to avoid oversizing.
    """

    ct_val = _to_float(instrument.ctVal, 0.0)
    lot_sz = _to_float(instrument.lotSz, 1.0)
    min_sz = _to_float(instrument.minSz, lot_sz)

    if ct_val <= 0 or last_px <= 0:
        raise ValueError(f"Invalid instrument specs for sizing: ctVal={instrument.ctVal} lastPx={last_px}")

    raw_sz = target_notional / (ct_val * last_px)

    # round down to lot size
    steps = math.floor(raw_sz / lot_sz)
    sz = steps * lot_sz

    if sz < min_sz:
        sz = min_sz

    approx_notional = sz * ct_val * last_px

    # OKX expects sz as a string
    # If lot size is integer-like, keep it clean.
    if abs(sz - round(sz)) < 1e-9:
        sz_str = str(int(round(sz)))
    else:
        sz_str = ("%f" % sz).rstrip("0").rstrip(".")

    return SizeResult(sz=sz_str, approx_notional=approx_notional)
