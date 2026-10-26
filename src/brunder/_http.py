"""Shared HTTP client with retry/backoff for TSE and Polymarket endpoints."""

from __future__ import annotations

import asyncio
import time

import httpx

UA = {"User-Agent": "Mozilla/5.0 (research; brunder)"}
RETRY_STATUS = {429, 500, 502, 503, 504}


def get_json(client: httpx.Client, url: str, params: dict | None = None, tries: int = 5):
    for attempt in range(tries):
        r = client.get(url, params=params)
        if r.status_code in RETRY_STATUS and attempt < tries - 1:
            time.sleep(2**attempt)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("unreachable")


def _delay(r: httpx.Response | None, attempt: int) -> float:
    ra = r.headers.get("Retry-After") if r is not None else None
    return max(float(ra) if ra and ra.isdigit() else 0.0, min(2**attempt, 60))


async def aget_bytes(client: httpx.AsyncClient, url: str, tries: int = 10) -> bytes | None:
    """GET raw bytes; None on 404 (file not published for this unit)."""
    for attempt in range(tries):
        try:
            r = await client.get(url)
        except httpx.TransportError:
            if attempt == tries - 1:
                raise
            await asyncio.sleep(_delay(None, attempt))
            continue
        if r.status_code == 404:
            return None
        if r.status_code in RETRY_STATUS and attempt < tries - 1:
            await asyncio.sleep(_delay(r, attempt))
            continue
        r.raise_for_status()
        return r.content
    raise RuntimeError("unreachable")

def client(**kw) -> httpx.Client:
    return httpx.Client(headers=UA, timeout=60, follow_redirects=True, **kw)


def aclient(**kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=UA, timeout=60, follow_redirects=True, **kw)
