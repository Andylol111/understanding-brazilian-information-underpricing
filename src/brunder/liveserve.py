"""Live data server for the election-night dashboard (`election.html?id=<eid>&live=<public url>`).

Serves only the dashboard's live JSON: `<root>/<eid>/**.json` for the listed elections plus a few listed top-level
files (elections.json, summary.json). Pages and geometry stay on GitHub Pages, which reads these files
cross-origin, so every response carries `Access-Control-Allow-Origin` for the Pages origin. Bound to 127.0.0.1
behind the Cloudflare quick tunnel (deploy/brunder-site.service, deploy/brunder-tunnel.service).

One asyncio loop, no threads. Each file is read and gzipped once per change (keyed by mtime/size/inode, the model
replaces files atomically) and then served from memory; ETag is a content hash, so a rewrite with the same content
still answers 304. Anything that is not a whitelisted `.json` path is 404: no directory listing, no `..`, no
percent-encoding, no symlinks out of the root. Stdlib only and Python >= 3.10, so it runs on the server's system
python without the project environment:

    PYTHONPATH=src python3 -m brunder.liveserve --root site/data --election us2026
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

PAGES_ORIGIN = "https://andylol111.github.io"
MAX_AGE = 10                      # s; the dashboard revalidates every poll anyway (fetch cache: "no-cache")
HEADER_LIMIT = 16 * 1024          # bytes of request line + headers
IDLE_TIMEOUT = 30.0               # s a keep-alive connection may sit idle (cloudflared re-dials transparently)
WRITE_TIMEOUT = 30.0              # s a client may take to drain one response
MAX_CONNECTIONS = 1024            # open connections (idle keep-alive included, ~10 kB each); above: 503 and close
MAX_FILE = 32 * 1024 * 1024       # bytes; a larger file is not served (404) rather than held in memory

_PATH = re.compile(r"/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.json")
_REASON = {200: "OK", 204: "No Content", 304: "Not Modified", 400: "Bad Request", 404: "Not Found",
           405: "Method Not Allowed", 431: "Request Header Fields Too Large", 500: "Internal Server Error",
           503: "Service Unavailable"}


@dataclass(frozen=True)
class Entry:
    key: tuple[int, int, int]     # (mtime_ns, size, inode) of the file this was read from
    raw: bytes
    gz: bytes
    etag: str                     # content hash, without quotes
    mtime: float                  # when the content last changed (seconds); Last-Modified


class Files:
    """Whitelisted JSON files under `root`, each read and gzipped once per change."""

    def __init__(self, root: Path, elections: list[str], top: list[str]):
        self.root = Path(os.path.realpath(root))
        self.elections = frozenset(elections)
        self.top = frozenset(top)
        self.cache: dict[str, Entry] = {}

    def resolve(self, path: str) -> Path | None:
        """URL path (no query) -> file inside root, or None when it is not servable."""
        if not _PATH.fullmatch(path):
            return None
        rel = path[1:]
        head, sep, _ = rel.partition("/")
        if not (rel in self.top if not sep else head in self.elections):
            return None
        real = os.path.realpath(self.root / rel)
        if not real.startswith(str(self.root) + os.sep):
            return None                                   # a symlink pointing out of the root
        return Path(real)

    def get(self, path: str) -> Entry | None:
        f = self.resolve(path)
        if f is None:
            self.cache.pop(path, None)
            return None
        try:
            st = os.stat(f)
        except OSError:
            self.cache.pop(path, None)
            return None
        old = self.cache.get(path)
        if old is not None and old.key == (st.st_mtime_ns, st.st_size, st.st_ino):
            return old
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
            return None                                   # never open a directory or fifo; never hold a huge file
        try:
            with open(f, "rb") as fh:                     # fstat of the opened file: the key matches what is read
                st = os.fstat(fh.fileno())
                raw = fh.read(MAX_FILE + 1)
            if len(raw) > MAX_FILE:
                raise OSError("file too large")
        except OSError:
            self.cache.pop(path, None)
            return None
        etag = hashlib.blake2b(raw, digest_size=12).hexdigest()
        changed = old is None or old.etag != etag
        e = Entry((st.st_mtime_ns, st.st_size, st.st_ino), raw, gzip.compress(raw, 6, mtime=0) if changed else old.gz,
                  etag, st.st_mtime if changed else old.mtime)
        self.cache[path] = e
        return e


def _etags(value: str) -> list[str]:
    """If-None-Match -> bare tags (weak prefix and our -gz suffix stripped; proxies may weaken or re-suffix)."""
    out = []
    for tag in value.split(","):
        tag = tag.strip()
        if tag.startswith("W/"):
            tag = tag[2:]
        tag = tag.strip('"')
        out.append(tag[:-3] if tag.endswith("-gz") else tag)
    return out


def _not_modified(e: Entry, headers: dict[str, str]) -> bool:
    inm = headers.get("if-none-match")
    if inm is not None:
        tags = _etags(inm)
        return "*" in tags or e.etag in tags
    ims = headers.get("if-modified-since")
    if ims:
        try:
            return int(e.mtime) <= parsedate_to_datetime(ims).timestamp()
        except (TypeError, ValueError, OverflowError):
            return False
    return False


def _accepts_gzip(headers: dict[str, str]) -> bool:
    for part in headers.get("accept-encoding", "").lower().split(","):
        name, _, params = part.partition(";")
        if name.strip() in ("gzip", "*"):
            q = params.replace(" ", "")
            try:
                return not q.startswith("q=") or float(q[2:]) > 0
            except ValueError:
                return True
    return False


class LiveServer:
    def __init__(self, files: Files, origins: list[str], max_connections: int = MAX_CONNECTIONS,
                 idle_timeout: float = IDLE_TIMEOUT):
        self.files = files
        self.origins = list(origins)
        self.max_connections = max_connections
        self.idle_timeout = idle_timeout
        self.active = 0
        self.idle: dict[asyncio.StreamWriter, None] = {}   # keep-alive connections between requests, oldest first
        self.stats = {"conn": 0, "req": 0, "200": 0, "304": 0, "404": 0, "other": 0, "bytes": 0, "busy": 0,
                      "evicted": 0}

    # ---------------------------------------------------------------- responses

    def _cors(self, headers: dict[str, str]) -> list[tuple[str, str]]:
        origin = headers.get("origin")
        allow = origin if origin in self.origins else self.origins[0]
        return [("Access-Control-Allow-Origin", allow), ("Vary", "Origin, Accept-Encoding")]

    def _response(self, status: int, headers: list[tuple[str, str]], body: bytes, head_only: bool) -> bytes:
        lines = [f"HTTP/1.1 {status} {_REASON[status]}", f"Date: {formatdate(usegmt=True)}"]
        lines += [f"{k}: {v}" for k, v in headers]
        if status not in (204, 304):
            lines.append(f"Content-Length: {len(body)}")
        out = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
        return out if head_only or status in (204, 304) else out + body

    def handle(self, method: str, target: str, headers: dict[str, str]) -> tuple[int, bytes]:
        """One request -> (status, bytes to send). Pure apart from the file cache, so tests can call it."""
        path = target.split("?", 1)[0].split("#", 1)[0]
        cors = self._cors(headers)
        head_only = method == "HEAD"
        if method == "OPTIONS":
            if self.files.resolve(path) is None:
                return 404, self._response(404, cors + [("Content-Type", "text/plain")], b"not found\n", False)
            return 204, self._response(204, cors + [
                ("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS"),
                ("Access-Control-Allow-Headers", "If-None-Match, If-Modified-Since, Cache-Control"),
                ("Access-Control-Max-Age", "86400")], b"", False)
        if method not in ("GET", "HEAD"):
            return 405, self._response(405, cors + [("Allow", "GET, HEAD, OPTIONS"), ("Content-Type", "text/plain")],
                                       b"method not allowed\n", head_only)
        e = self.files.get(path)
        cache = [("Cache-Control", f"public, max-age={MAX_AGE}")]
        if e is None:
            return 404, self._response(404, cors + cache + [("Content-Type", "text/plain")], b"not found\n",
                                       head_only)
        gz = _accepts_gzip(headers)
        meta = cors + cache + [("ETag", f'"{e.etag}-gz"' if gz else f'"{e.etag}"'),
                               ("Last-Modified", formatdate(e.mtime, usegmt=True))]
        if _not_modified(e, headers):
            return 304, self._response(304, meta, b"", True)
        meta += [("Content-Type", "application/json; charset=utf-8"), ("X-Content-Type-Options", "nosniff")]
        if gz:
            meta.append(("Content-Encoding", "gzip"))
        return 200, self._response(200, meta, e.gz if gz else e.raw, head_only)

    # ---------------------------------------------------------------- connections

    async def connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.stats["conn"] += 1
        if self.active >= self.max_connections:
            if not self.idle:
                self.stats["busy"] += 1
                writer.write(self._response(503, [("Retry-After", "5"), ("Connection", "close")], b"busy\n", False))
                await self._close(writer)
                return
            old = next(iter(self.idle))                  # close the longest-idle keep-alive connection instead;
            del self.idle[old]                           # HTTP clients re-dial for the next request
            old.transport.abort()
            self.stats["evicted"] += 1
        self.active += 1
        try:
            served = False
            while await self._one(reader, writer, idle=served):
                served = True
        except (ConnectionError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            self.idle.pop(writer, None)
            self.active -= 1
            await self._close(writer)

    async def _one(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, idle: bool) -> bool:
        """Serve one request; False when the connection should close. `idle`: waiting between keep-alive requests,
        so the connection may be closed to admit a new one."""
        if idle:
            self.idle[writer] = None
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.idle_timeout)
        except asyncio.LimitOverrunError:
            await self._send(writer, self._response(431, [("Connection", "close")], b"", False))
            return False
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return False
        finally:
            self.idle.pop(writer, None)
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3 or parts[2] not in ("HTTP/1.0", "HTTP/1.1") or not parts[1].startswith("/"):
            await self._send(writer, self._response(400, [("Connection", "close")], b"bad request\n", False))
            return False
        method, target, version = parts
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
        if headers.get("content-length", "0") != "0" or "transfer-encoding" in headers:
            await self._send(writer, self._response(400, [("Connection", "close")], b"no request bodies\n", False))
            return False                                   # nothing here takes a body; never try to skip one
        conn = headers.get("connection", "").lower()
        keep = version == "HTTP/1.1" and "close" not in conn
        status, out = self.handle(method, target, headers)
        if not keep:
            out = out.replace(b"\r\n\r\n", b"\r\nConnection: close\r\n\r\n", 1)
        self.stats["req"] += 1
        self.stats[str(status) if str(status) in self.stats else "other"] += 1
        self.stats["bytes"] += len(out)
        await self._send(writer, out)
        return keep

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, data: bytes) -> None:
        writer.write(data)
        await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT)

    @staticmethod
    async def _close(writer: asyncio.StreamWriter) -> None:
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), 5)
        except (ConnectionError, asyncio.TimeoutError, OSError):
            pass

    async def report(self, every: float) -> None:
        """One journal line per interval instead of one per request."""
        last = dict(self.stats)
        while True:
            await asyncio.sleep(every)
            now = dict(self.stats)
            d = {k: now[k] - last[k] for k in now}
            last = now
            print(f"liveserve {every:.0f}s: req={d['req']} 200={d['200']} 304={d['304']} 404={d['404']} "
                  f"other={d['other']} busy={d['busy']} evicted={d['evicted']} MB={d['bytes'] / 1e6:.1f} "
                  f"open={self.active}", flush=True)


async def serve(root: Path, elections: list[str], top: list[str], origins: list[str], host: str, port: int,
                max_connections: int, report_every: float) -> None:
    srv = LiveServer(Files(root, elections, top), origins, max_connections)
    server = await asyncio.start_server(srv.connection, host, port, limit=HEADER_LIMIT, backlog=256)
    print(f"liveserve: {srv.files.root} elections={sorted(elections)} files={sorted(top)} on {host}:{port}, "
          f"CORS {origins}", flush=True)
    async with server:
        if report_every > 0:
            asyncio.get_running_loop().create_task(srv.report(report_every))
        await server.serve_forever()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m brunder.liveserve", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, required=True, help="the served data root (site/data)")
    ap.add_argument("--election", action="append", required=True, help="election id whose folder is served")
    ap.add_argument("--file", action="append", default=None,
                    help="top-level file served from the root (default: elections.json, summary.json)")
    ap.add_argument("--allow-origin", action="append", default=None,
                    help=f"CORS origin (default {PAGES_ORIGIN}); the first is sent to unknown origins")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--max-connections", type=int, default=MAX_CONNECTIONS)
    ap.add_argument("--report-every", type=float, default=60.0, help="seconds between journal lines (0 = never)")
    a = ap.parse_args(argv)
    try:
        asyncio.run(serve(a.root, a.election, a.file or ["elections.json", "summary.json"],
                          a.allow_origin or [PAGES_ORIGIN], a.host, a.port, a.max_connections, a.report_every))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
