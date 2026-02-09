from __future__ import annotations

from pydantic import BaseModel


class Instrument(BaseModel):
    instId: str
    instType: str | None = None
    tickSz: str | None = None
    lotSz: str | None = None
    minSz: str | None = None
    ctVal: str | None = None
    ctValCcy: str | None = None
    settleCcy: str | None = None


class Position(BaseModel):
    instId: str
    posSide: str
    pos: str | None = None
    avgPx: str | None = None
    upl: str | None = None
    mgnMode: str | None = None
