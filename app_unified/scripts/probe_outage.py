#!/usr/bin/env python3
"""尖峰時段斷線探針 — 把「連不上」拆成可指認的層。

每輪對三件事同時取樣：
  1. DNS：分別問 Cloudflare(1.1.1.1) 與 Google(8.8.8.8)，看網域還解不解得出來
  2. 對外網域（走 Cloudflare 邊緣）的 /healthz
  3. Zeabur origin 網域（繞過 Cloudflare）的 /healthz

每個 HTTP 取樣再拆成四段計時：DNS → TCP connect → TLS handshake → 第一個 byte(TTFB)。
分段是重點：「TCP 接得上但 TTFB 卡死」跟「TCP 就連不上」是完全不同的病。

用法：
    export PROBE_DOMAIN=notes.example.com     # 對外網域（走 Cloudflare）
    export PROBE_ORIGIN=xxx.zeabur.app        # Zeabur 原始網域（繞過 Cloudflare）
    ../venv/bin/python scripts/probe_outage.py

    # 整晚背景跑：
    nohup ../venv/bin/python scripts/probe_outage.py > /tmp/probe.out 2>&1 &

輸出：
    /tmp/probe_outage.jsonl      每輪一行 JSON
    /tmp/probe_outage.jsonl.d/   失敗當下的完整 response headers + body
    stdout                       正常時安靜；異常時印一行

判讀對照表（看 JSONL 的 verdict 欄位，或事後跑 --report）：
    dns_fail        DNS 真的解不出來        → Cloudflare DNS / 網域設定層
    conn_fail       TCP 連不上 / 逾時       → 邊緣到 origin 之間，或 origin 整個沒了
    ttfb_hang       TCP+TLS 正常但沒回 byte → app 卡住（gevent hub 被 block / worker 被 SIGKILL 中）
    http_5xx        有回但 5xx             → 看 dump 裡的 Cloudflare 錯誤碼(521/522/523/524)或 app traceback
    slow            有回但很慢             → 開始飽和，還沒斷
    ok              正常
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))  # 台灣時間，跟事故回報對得上

DOMAIN = os.environ.get("PROBE_DOMAIN", "")
ORIGIN = os.environ.get("PROBE_ORIGIN", "")
INTERVAL = float(os.environ.get("PROBE_INTERVAL", "20"))
TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "15"))
PATH = os.environ.get("PROBE_PATH", "/healthz")
LOG = os.environ.get("PROBE_LOG", "/tmp/probe_outage.jsonl")
DUMPDIR = LOG + ".d"
SLOW_SEC = float(os.environ.get("PROBE_SLOW_SEC", "3"))


def now() -> datetime:
    return datetime.now(TZ)


def dns_query(name: str, resolver: str) -> list[str]:
    """問指定 resolver。回傳 A 記錄列表；解不出來回空 list。"""
    try:
        out = subprocess.run(
            ["dig", "+short", "+time=3", "+tries=1", "A", name, f"@{resolver}"],
            capture_output=True, text=True, timeout=8,
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    return [ln.strip() for ln in out.splitlines()
            if ln.strip() and ln.strip()[0].isdigit()]


def http_probe(host: str, path: str, tag: str) -> dict:
    """分段計時的 HTTPS 取樣。失敗時把現場 dump 到 DUMPDIR。"""
    r = {"host": host, "code": None, "verdict": "ok", "err": None,
         "t_dns": None, "t_conn": None, "t_tls": None, "t_ttfb": None,
         "t_total": None, "cf_ray": None, "app": None}
    t0 = time.monotonic()
    sock = None
    try:
        # --- 階段 1：DNS ---
        try:
            addrs = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
        except socket.gaierror as e:
            r.update(verdict="dns_fail", err=f"getaddrinfo: {e}",
                     t_total=round(time.monotonic() - t0, 3))
            return r
        r["t_dns"] = round(time.monotonic() - t0, 3)

        # --- 階段 2：TCP connect ---
        t = time.monotonic()
        try:
            sock = socket.create_connection(addrs[0][4], timeout=TIMEOUT)
        except (socket.timeout, TimeoutError) as e:
            r.update(verdict="conn_fail", err=f"tcp timeout: {e}",
                     t_conn=round(time.monotonic() - t, 3),
                     t_total=round(time.monotonic() - t0, 3))
            return r
        except OSError as e:
            r.update(verdict="conn_fail", err=f"tcp: {e}",
                     t_conn=round(time.monotonic() - t, 3),
                     t_total=round(time.monotonic() - t0, 3))
            return r
        r["t_conn"] = round(time.monotonic() - t, 3)

        # --- 階段 3：TLS handshake ---
        t = time.monotonic()
        try:
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)
        except Exception as e:
            r.update(verdict="tls_fail", err=f"tls: {type(e).__name__}: {e}",
                     t_tls=round(time.monotonic() - t, 3),
                     t_total=round(time.monotonic() - t0, 3))
            return r
        r["t_tls"] = round(time.monotonic() - t, 3)

        # --- 階段 4：送 request，等第一個 byte ---
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
               "User-Agent: probe-outage/1\r\nCache-Control: no-cache\r\n"
               "Connection: close\r\n\r\n")
        t = time.monotonic()
        sock.sendall(req.encode())
        sock.settimeout(TIMEOUT)
        chunks, first_byte_at = [], None
        try:
            while True:
                b = sock.recv(65536)
                if first_byte_at is None:
                    first_byte_at = time.monotonic()
                if not b:
                    break
                chunks.append(b)
                if sum(len(c) for c in chunks) > 200_000:
                    break
        except (socket.timeout, TimeoutError):
            if first_byte_at is None:
                # TCP/TLS 都好，但 app 一個 byte 都沒吐 —— 最關鍵的訊號
                r.update(verdict="ttfb_hang",
                         err=f"no response byte within {TIMEOUT}s",
                         t_ttfb=round(time.monotonic() - t, 3),
                         t_total=round(time.monotonic() - t0, 3))
                return r
        except OSError as e:
            r.update(verdict="read_fail", err=f"read: {e}",
                     t_total=round(time.monotonic() - t0, 3))
            return r

        r["t_ttfb"] = round((first_byte_at or time.monotonic()) - t, 3)
        r["t_total"] = round(time.monotonic() - t0, 3)

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

        # /healthz 回 {"status","pid","uptime_s"}：pid 變了就是 worker 被重啟過,
        # uptime 也直接說明它活多久 —— 這是「卡住」與「被 SIGKILL 重開」的分水嶺。
        try:
            payload = json.loads(body.decode("utf-8", "replace"))
            if isinstance(payload, dict) and "pid" in payload:
                r["app"] = {"pid": payload.get("pid"),
                            "uptime_s": payload.get("uptime_s")}
        except (ValueError, UnicodeDecodeError):
            pass  # 舊版 /healthz 回純文字 "ok"，或根本不是我們的服務在回應

        if r["code"] >= 500:
            r["verdict"] = "http_5xx"
        elif r["code"] >= 400:
            r["verdict"] = "http_4xx"
        elif r["t_total"] > SLOW_SEC:
            r["verdict"] = "slow"

        if r["verdict"] not in ("ok", "slow"):
            _dump(tag, head, body)
        return r
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if r["t_total"] is None:
            r["t_total"] = round(time.monotonic() - t0, 3)


def _dump(tag: str, head: bytes, body: bytes) -> None:
    """存失敗現場。Cloudflare 錯誤頁裡的 521/522/523/524 編號是判斷關鍵。"""
    os.makedirs(DUMPDIR, exist_ok=True)
    stamp = now().strftime("%Y%m%d-%H%M%S")
    try:
        with open(os.path.join(DUMPDIR, f"{stamp}-{tag}.txt"), "wb") as f:
            f.write(head + b"\r\n\r\n" + body[:100_000])
    except OSError as e:
        print(f"  (dump 失敗: {e})", file=sys.stderr)


def report(path: str) -> int:
    """事後判讀：把非 ok 的輪次照 verdict 分組印出來。"""
    try:
        with open(path) as f:
            rows = [json.loads(ln) for ln in f if ln.strip()]
    except OSError as e:
        print(f"讀不到 {path}: {e}")
        return 1
    if not rows:
        print(f"{path} 是空的")
        return 1

    # 取樣斷層：電腦休眠 / 探針被砍會讓 JSONL 缺一段。不點出來的話，「沒資料」
    # 會被誤讀成「全程正常」—— 那是最危險的錯誤結論。
    gaps = []
    for a, b in zip(rows, rows[1:]):
        try:
            ta = datetime.fromisoformat(a["ts"])
            tb = datetime.fromisoformat(b["ts"])
        except (ValueError, KeyError):
            continue
        delta = (tb - ta).total_seconds()
        if delta > max(INTERVAL * 3, 60):
            gaps.append((a["ts"], b["ts"], delta))

    bad = [r for r in rows
           if r["public"]["verdict"] != "ok" or not r["dns_cf"] or not r["dns_google"]]
    print(f"共 {len(rows)} 輪，{rows[0]['ts']} → {rows[-1]['ts']}")
    print(f"異常 {len(bad)} 輪")

    restarts = [(r["ts"], rr) for r in rows for rr in r.get("restarts", [])]
    if restarts:
        print(f"\n偵測到 worker 重啟 {len(restarts)} 次 "
              f"（緊接在 ttfb_hang 之後 = 卡死被 SIGKILL）：")
        for ts_, rr in restarts:
            print(f"   {ts_}  {rr['target']}: pid {rr['old_pid']} → {rr['new_pid']}")
    print()
    if gaps:
        print(f"\n⚠ 取樣中斷 {len(gaps)} 段（電腦休眠？探針被砍？）"
              f"—— 這些時段是「沒量到」，不是「正常」：")
        for a, b, d in gaps:
            print(f"   {a} → {b}  缺 {d/60:.1f} 分鐘")
        print()

    if not bad:
        msg = "這段時間沒重現。" if not gaps else "有量到的時段都正常，但上面那些斷層沒資料。"
        print(msg)
        return 0

    groups: dict[str, list] = {}
    for r in bad:
        v = "dns_fail" if not r["dns_cf"] or not r["dns_google"] else r["public"]["verdict"]
        groups.setdefault(v, []).append(r)

    meaning = {
        "dns_fail": "DNS 真的解不出來 → Cloudflare DNS / 網域設定層",
        "conn_fail": "TCP 連不上 → 邊緣到 origin 之間，或 origin 沒了",
        "ttfb_hang": "TCP+TLS 正常但 app 沒回 byte → app 卡住（hub block / worker 重啟中）",
        "http_5xx": "有回 5xx → 看 dump 裡的 CF 錯誤碼或 app traceback",
        "tls_fail": "TLS handshake 失敗",
        "slow": "還沒斷，但已經在飽和",
    }
    for v, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f"── {v} × {len(rs)}  … {meaning.get(v, '')}")
        print(f"   期間 {rs[0]['ts']} → {rs[-1]['ts']}")
        o = rs[0]["origin"]["verdict"] if rs[0].get("origin") else "n/a"
        print(f"   同時間 origin(繞過 CF) = {o}")
        if v != "dns_fail":
            print(f"   例：{json.dumps(rs[0]['public'], ensure_ascii=False)}")
        print()

    print(f"失敗現場 dump：{DUMPDIR}/")
    return 0


def main() -> int:
    if "--report" in sys.argv:
        return report(LOG)

    if not DOMAIN:
        print("ERROR: 請設 PROBE_DOMAIN（對外網域，例如 notes.example.com）", file=sys.stderr)
        return 1
    if not ORIGIN:
        print("WARN: 沒設 PROBE_ORIGIN（Zeabur 的 *.zeabur.app 網域）。", file=sys.stderr)
        print("      少了它就分不出「Cloudflare 邊緣壞」還是「origin 壞」——強烈建議補。",
              file=sys.stderr)

    print(f"probe: domain={DOMAIN} origin={ORIGIN or '<none>'} path={PATH} "
          f"interval={INTERVAL}s timeout={TIMEOUT}s")
    print(f"       log={LOG}  dumps={DUMPDIR}")
    print("       正常時安靜，異常才印。事後判讀：加 --report\n")

    seen_pids: dict[str, int] = {}
    while True:
        started = time.monotonic()
        ts = now().isoformat(timespec="seconds")
        row = {
            "ts": ts,
            "dns_cf": dns_query(DOMAIN, "1.1.1.1"),
            "dns_google": dns_query(DOMAIN, "8.8.8.8"),
            "public": http_probe(DOMAIN, PATH, "public"),
            "origin": http_probe(ORIGIN, PATH, "origin") if ORIGIN else None,
        }
        # worker 重啟偵測：pid 換人 = 上一個 worker 被 SIGKILL / pod 被重建。
        # 這通常就緊接在 ttfb_hang 之後 —— 兩者一起出現即為「卡死→被砍→恢復」。
        # 必須在寫檔前做，否則 restarts 不會進 JSONL。
        for who in ("public", "origin"):
            probe = row.get(who)
            if not probe or not probe.get("app"):
                continue
            pid = probe["app"].get("pid")
            prev = seen_pids.get(who)
            if prev is not None and pid != prev:
                row.setdefault("restarts", []).append(
                    {"target": who, "old_pid": prev, "new_pid": pid,
                     "uptime_s": probe["app"].get("uptime_s")})
                print(f"[{ts}] ** {who} worker 重啟：pid {prev} → {pid} "
                      f"(uptime {probe['app'].get('uptime_s')}s)", flush=True)
            seen_pids[who] = pid

        try:
            with open(LOG, "a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"[{ts}] 寫 log 失敗: {e}", file=sys.stderr)

        pub = row["public"]
        org = row["origin"]
        org_v = org["verdict"] if org else "n/a"
        if not row["dns_cf"] or not row["dns_google"]:
            print(f"[{ts}] !! DNS 解析失敗 cf={row['dns_cf']} google={row['dns_google']}"
                  f"  ← 這才是真的 DNS 問題", flush=True)
        elif pub["verdict"] != "ok":
            print(f"[{ts}] !! public={pub['verdict']} code={pub['code']} "
                  f"t={pub['t_total']}s (origin={org_v}) err={pub['err']}", flush=True)
        elif org and org["verdict"] != "ok":
            print(f"[{ts}] !! 只有 origin 異常：{org_v} (public 正常) "
                  f"← Cloudflare 快取擋著，但 origin 已經在壞", flush=True)

        time.sleep(max(0.0, INTERVAL - (time.monotonic() - started)))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n停止。事後判讀：PROBE_LOG=%s python3 %s --report" % (LOG, sys.argv[0]))
