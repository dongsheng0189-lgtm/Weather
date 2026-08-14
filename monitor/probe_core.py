"""分層 HTTP/DNS 探測。

與 app_unified/scripts/probe_outage.py 同源，**刻意複製而非 import**：
monitor 與 app-unified 是兩個 container、兩個 process，誰崩誰活互不波及
（沿用 2026-05-08 monitor spec 的隔離守則）。

每次探測拆成四段計時：DNS → TCP connect → TLS handshake → 第一個 byte。
分段才分得出「網路到不了」與「app 不回應」。
"""
import json
import socket
import ssl
import subprocess
import time

SLOW_SECONDS = 3.0
_MAX_BODY = 200_000


def resolve(name: str, resolver: str) -> list:
    """問指定 resolver 拿 A 記錄。解不出來回空 list。"""
    try:
        out = subprocess.run(
            ["dig", "+short", "+time=3", "+tries=1", "A", name, f"@{resolver}"],
            capture_output=True, text=True, timeout=8,
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    return [ln.strip() for ln in out.splitlines()
            if ln.strip() and ln.strip()[0].isdigit()]


def probe(host, path, *, port=443, use_tls=True, timeout=15.0) -> dict:
    r = {"verdict": "ok", "code": None, "err": None, "cf_ray": None, "app": None,
         "t_dns": None, "t_conn": None, "t_tls": None, "t_ttfb": None,
         "t_total": None}
    t0 = time.monotonic()
    sock = None
    try:
        # --- DNS ---
        try:
            addrs = socket.getaddrinfo(host, port, socket.AF_INET,
                                       socket.SOCK_STREAM)
        except socket.gaierror as e:
            r.update(verdict="dns_fail", err=f"getaddrinfo: {e}")
            return r
        r["t_dns"] = round(time.monotonic() - t0, 3)

        # --- TCP connect ---
        t = time.monotonic()
        try:
            sock = socket.create_connection(addrs[0][4], timeout=timeout)
        except OSError as e:
            r.update(verdict="conn_fail", err=f"tcp: {e}",
                     t_conn=round(time.monotonic() - t, 3))
            return r
        r["t_conn"] = round(time.monotonic() - t, 3)

        # --- TLS handshake ---
        if use_tls:
            t = time.monotonic()
            try:
                sock = ssl.create_default_context().wrap_socket(
                    sock, server_hostname=host)
            except Exception as e:
                r.update(verdict="tls_fail",
                         err=f"tls: {type(e).__name__}: {e}",
                         t_tls=round(time.monotonic() - t, 3))
                return r
            r["t_tls"] = round(time.monotonic() - t, 3)

        # --- 送 request，等第一個 byte ---
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
               "User-Agent: monitor-probe/1\r\nCache-Control: no-cache\r\n"
               "Connection: close\r\n\r\n")
        t = time.monotonic()
        sock.sendall(req.encode())
        sock.settimeout(timeout)
        chunks, first_at = [], None
        try:
            while True:
                b = sock.recv(65536)
                if first_at is None:
                    first_at = time.monotonic()
                if not b:
                    break
                chunks.append(b)
                if sum(len(c) for c in chunks) > _MAX_BODY:
                    break
        except (socket.timeout, TimeoutError):
            if first_at is None:
                # TCP/TLS 都通，app 一個 byte 都不吐 —— hub 被卡住的典型樣貌
                r.update(verdict="ttfb_hang",
                         err=f"no response byte within {timeout}s",
                         t_ttfb=round(time.monotonic() - t, 3))
                return r
        except OSError as e:
            r.update(verdict="read_fail", err=f"read: {e}")
            return r

        r["t_ttfb"] = round((first_at or time.monotonic()) - t, 3)
        raw = b"".join(chunks)
        head, _, body = raw.partition(b"\r\n\r\n")
        head_s = head.decode("latin-1", "replace")
        try:
            r["code"] = int(head_s.split(None, 2)[1])
        except (IndexError, ValueError):
            r.update(verdict="bad_response", err=f"unparsable: {head_s[:120]!r}")
            return r
        for line in head_s.splitlines():
            if line.lower().startswith("cf-ray:"):
                r["cf_ray"] = line.split(":", 1)[1].strip()
        try:
            payload = json.loads(body.decode("utf-8", "replace"))
            if isinstance(payload, dict) and "pid" in payload:
                r["app"] = {"pid": payload.get("pid"),
                            "uptime_s": payload.get("uptime_s")}
        except (ValueError, UnicodeDecodeError):
            pass   # 舊版 /healthz 回純文字，不是錯誤

        if r["code"] >= 500:
            r["verdict"] = "http_5xx"
        elif r["code"] >= 400:
            r["verdict"] = "http_4xx"
        return r
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if r["t_total"] is None:
            r["t_total"] = round(time.monotonic() - t0, 3)
        if r["verdict"] == "ok" and r["t_total"] > SLOW_SECONDS:
            r["verdict"] = "slow"
