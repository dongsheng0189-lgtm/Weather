#!/usr/bin/env python3
"""警政署 (eze8.npa.gov.tw) 連線分層診斷 — 在 prod 容器內跑，指認斷在哪一層。

背景：使用者回報人員查詢顯示「警政署系統暫時無法連線」。該訊息由
`vetting/routes.py` 的 `/start`（與 `/refresh_captcha`）在 NPA prep 段
攔到任意 Exception 後回 502 產生。本機（台灣 IP）跑完整 prep 三步驟全通，
所以問題在 prod 環境到 NPA 的路徑上，必須在容器內取證。

把「連不上」拆成可指認的層：
    egress_ip   本服務對外的公網 IP 與地理位置（NPA 是否地域封鎖的關鍵）
    dns         eze8.npa.gov.tw 解出哪些 A / AAAA
    tcp         逐個 IP 直接 connect 443，分開量 v4 / v6
    tls         握手是否成功、憑證 subject/issuer/效期
    http_home   GET /CE_Query.jsp 的 status、長度、csrf/_key 解不解得出來
    http_auth   POST /Auth 取 JWT
    http_captcha GET /genCheckCharImage 取圖
    gevent      是否處於 monkey patch 環境（patched socket 走不同的 resolver）

用法（在 app-unified 的 Zeabur 指令 shell 內）：
    python3 /tmp/diagnose_npa.py

    # 想同時比對「有 gevent monkey patch」的行為（prod 實際情況）：
    python3 /tmp/diagnose_npa.py --patched

    # 只量 DNS 間歇失敗率（EAI_AGAIN 取證用）：
    python3 /tmp/diagnose_npa.py --dns-stress

判讀：
    egress 顯示非 TW 的 IP        → 高度懷疑 NPA 地域封鎖，換出口或走中繼
    dns 只剩 AAAA / 解不出       → DNS 層，NPA 或容器 resolver 的問題
    tcp v4 通但 v6 不通          → 若 patched resolver 先挑 v6 就會表現成「連不上」
    tcp 全部 timeout             → 出口 IP 被 NPA 防火牆丟包（靜默 drop）
    tcp 通但 tls 失敗            → 憑證鏈 / TLS 版本
    http_home 403 / 非 200       → 被 WAF 擋（有回應，不是網路層）
    全部 OK                      → 不是連線層；回頭看 app log 的實際例外字串
"""
from __future__ import annotations

import json
import socket
import ssl
import sys
import time

HOST = "eze8.npa.gov.tw"
PORT = 443
HOME_PATH = "/NpaE8ServerRWD/CE_Query.jsp"
AUTH_PATH = "/NpaE8ServerRWD/Auth"
CAPTCHA_PATH = "/NpaE8ServerRWD/genCheckCharImage"
TIMEOUT = 15

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36")


def out(layer: str, verdict: str, **kw):
    """一行一層，好貼回聊天室。"""
    extra = " ".join(f"{k}={v!r}" for k, v in kw.items())
    print(f"[{layer:<12}] {verdict:<10} {extra}", flush=True)


def check_gevent():
    patched = False
    try:
        from gevent import monkey  # noqa: F401
        patched = monkey.is_module_patched("socket")
    except Exception:
        pass
    out("gevent", "patched" if patched else "plain",
        note="patched 代表走 gevent resolver，與 plain 的 getaddrinfo 行為可能不同")
    return patched


def check_egress():
    """出口公網 IP + 國別。NPA 若地域封鎖，這一行就是答案。"""
    for url_host, path in (("ipinfo.io", "/json"), ("ifconfig.co", "/json")):
        try:
            body = _https_get(url_host, path, timeout=8)
            # 可能是 chunked，直接從第一個 '{' 取到最後一個 '}'
            data = json.loads(body[body.index("{"):body.rindex("}") + 1])
            out("egress_ip", "ok", ip=data.get("ip"),
                country=data.get("country"), city=data.get("city"),
                org=data.get("org") or data.get("asn"))
            return
        except Exception:
            continue
    out("egress_ip", "unknown", err="兩個查詢服務都問不到（可能整個對外都不通）")


def check_dns():
    v4, v6 = [], []
    try:
        infos = socket.getaddrinfo(HOST, PORT, 0, socket.SOCK_STREAM)
    except Exception as e:
        out("dns", "FAIL", err=f"{type(e).__name__}: {e}")
        return [], []
    for fam, _, _, _, sa in infos:
        if fam == socket.AF_INET and sa[0] not in v4:
            v4.append(sa[0])
        elif fam == socket.AF_INET6 and sa[0] not in v6:
            v6.append(sa[0])
    out("dns", "ok" if (v4 or v6) else "FAIL", v4=v4, v6=v6,
        first=infos[0][4][0] if infos else None)
    return v4, v6


def check_tcp(ips, family, label):
    """逐個 IP 直連，分開量。timeout 與 refused 是完全不同的病。"""
    for ip in ips:
        t0 = time.time()
        s = socket.socket(family, socket.SOCK_STREAM)
        s.settimeout(TIMEOUT)
        try:
            s.connect((ip, PORT))
            out(f"tcp_{label}", "ok", ip=ip, ms=int((time.time() - t0) * 1000))
        except socket.timeout:
            out(f"tcp_{label}", "TIMEOUT", ip=ip, ms=int((time.time() - t0) * 1000),
                note="靜默丟包，典型的防火牆封鎖")
        except Exception as e:
            out(f"tcp_{label}", "FAIL", ip=ip, err=f"{type(e).__name__}: {e}",
                ms=int((time.time() - t0) * 1000))
        finally:
            s.close()


def check_tls():
    ctx = ssl.create_default_context()
    t0 = time.time()
    try:
        with socket.create_connection((HOST, PORT), timeout=TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=HOST) as ss:
                cert = ss.getpeercert()
                subj = dict(x[0] for x in cert["subject"])
                iss = dict(x[0] for x in cert["issuer"])
                out("tls", "ok", ms=int((time.time() - t0) * 1000),
                    version=ss.version(), cn=subj.get("commonName"),
                    issuer=iss.get("commonName"),
                    notAfter=cert.get("notAfter"), peer=ss.getpeername()[0])
                return True
    except Exception as e:
        out("tls", "FAIL", err=f"{type(e).__name__}: {e}",
            ms=int((time.time() - t0) * 1000))
        return False


def _https_get(host, path, timeout=TIMEOUT, headers=None):
    """不依賴 requests 的最小 HTTPS GET，回完整 raw response（含 header）。"""
    ctx = ssl.create_default_context()
    hdrs = {"Host": host, "User-Agent": UA, "Accept": "*/*",
            "Accept-Encoding": "identity", "Connection": "close"}
    hdrs.update(headers or {})
    req = f"GET {path} HTTP/1.1\r\n" + "".join(
        f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n"
    with socket.create_connection((host, 443), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ss:
            ss.sendall(req.encode())
            buf = b""
            while True:
                chunk = ss.recv(65536)
                if not chunk:
                    break
                buf += chunk
    return buf.decode("utf-8", "replace")


def check_http_chain():
    """走真正的 prep 三步驟（有 requests 就用 requests，跟 app 同一條路）。"""
    try:
        import requests
    except Exception as e:
        out("http", "SKIP", err=f"沒有 requests：{e}；上面的 tls 層已足夠判斷網路")
        return

    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/webp,image/apng,*/*;q=0.8"),
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    base = f"https://{HOST}"

    # 1) 首頁 + token
    try:
        t0 = time.time()
        r = s.get(base + HOME_PATH, timeout=TIMEOUT)
        ms = int((time.time() - t0) * 1000)
        csrf = _find_meta(r.text, "_csrf")
        csrf_h = _find_meta(r.text, "_csrf_header")
        key = _find_key(r.text)
        ok = bool(csrf and csrf_h and key)
        out("http_home", "ok" if (r.status_code == 200 and ok) else "PARSE_FAIL"
            if r.status_code == 200 else f"HTTP_{r.status_code}",
            status=r.status_code, len=len(r.text), ms=ms,
            csrf=bool(csrf), csrf_header=csrf_h, key=bool(key))
        if r.status_code != 200:
            print("--- body head 800 ---")
            print(r.text[:800])
            return
        if not ok:
            print("--- body head 1500（首頁結構可能改版）---")
            print(r.text[:1500])
            return
    except Exception as e:
        out("http_home", "EXC", err=f"{type(e).__name__}: {e}")
        return

    # 2) /Auth 取 JWT
    try:
        t0 = time.time()
        r = s.post(base + AUTH_PATH, data={"auu": key},
                   headers={csrf_h: csrf, "Referer": base + HOME_PATH},
                   timeout=TIMEOUT)
        jwt = r.text.strip()
        out("http_auth", "ok" if (r.status_code == 200 and jwt.startswith("ey"))
            else f"HTTP_{r.status_code}",
            status=r.status_code, jwt_len=len(jwt),
            jwt_head=jwt[:24], ms=int((time.time() - t0) * 1000))
        if r.status_code != 200 or not jwt.startswith("ey"):
            print("--- body head 500 ---")
            print(r.text[:500])
            return
    except Exception as e:
        out("http_auth", "EXC", err=f"{type(e).__name__}: {e}")
        return

    # 3) captcha 圖
    try:
        t0 = time.time()
        r = s.get(base + CAPTCHA_PATH, params={"btnType": "1", "t": str(time.time())},
                  headers={"Referer": base + HOME_PATH}, timeout=TIMEOUT)
        is_jpeg = r.content[:3] == b"\xff\xd8\xff"
        out("http_captcha", "ok" if (r.status_code == 200 and is_jpeg)
            else f"HTTP_{r.status_code}",
            status=r.status_code, bytes=len(r.content), jpeg=is_jpeg,
            ms=int((time.time() - t0) * 1000))
    except Exception as e:
        out("http_captcha", "EXC", err=f"{type(e).__name__}: {e}")


def check_resolv_conf():
    """印出容器的 resolver 設定。ndots 與 search 網域數量決定一次解析要打幾個查詢。"""
    try:
        txt = open("/etc/resolv.conf").read()
    except Exception as e:
        out("resolv.conf", "FAIL", err=str(e))
        return
    print("--- /etc/resolv.conf ---")
    print(txt.strip())
    ndots = 1
    searches = []
    for line in txt.splitlines():
        if line.startswith("options") and "ndots:" in line:
            try:
                ndots = int(line.split("ndots:")[1].split()[0])
            except Exception:
                pass
        if line.startswith("search"):
            searches = line.split()[1:]
    host_dots = HOST.count(".")
    expands = host_dots < ndots
    out("resolv", "ok", ndots=ndots, search_count=len(searches),
        host_dots=host_dots,
        note=(f"{HOST} 只有 {host_dots} 點 < ndots:{ndots} → 會先試 "
              f"{len(searches)} 個 search 網域，A+AAAA 共約 "
              f"{(len(searches) + 1) * 2} 個查詢才解得出來"
              if expands else "不會展開 search 網域"))
    print()


DNS_STRESS_N = 40


def dns_stress():
    """重複解析，量間歇失敗率。EAI_AGAIN(-3) 是逾時/掉封包，EAI_NONAME 才是查無此名。

    同時測 FQDN（結尾加點）作為對照：若加點就不再失敗，代表問題出在
    search 網域展開（ndots），而不是上游 DNS 本身。
    """
    targets = [
        ("npa", HOST),
        ("npa-fqdn", HOST + "."),
        ("judicial", "judgment.judicial.gov.tw"),
        ("mjib", "www.mjib.gov.tw"),
    ]
    for label, name in targets:
        fails, times, errs = 0, [], {}
        for _ in range(DNS_STRESS_N):
            t0 = time.time()
            try:
                socket.getaddrinfo(name, 443, 0, socket.SOCK_STREAM)
                times.append((time.time() - t0) * 1000)
            except Exception as e:
                fails += 1
                errs[f"{type(e).__name__}: {e}"] = errs.get(
                    f"{type(e).__name__}: {e}", 0) + 1
        avg = int(sum(times) / len(times)) if times else -1
        mx = int(max(times)) if times else -1
        out(f"dns_{label}", "ok" if fails == 0 else f"FAIL_{fails}/{DNS_STRESS_N}",
            name=name, avg_ms=avg, max_ms=mx)
        for msg, n in errs.items():
            print(f"      ×{n}  {msg}")


PEERS = [
    ("judicial", "judgment.judicial.gov.tw", "/FJUD/default.aspx"),
    ("mjib", "www.mjib.gov.tw", "/Crimes/Crimes_List"),
]


def check_peers():
    """同儕對照：司法院 / 調查局。分辨「NPA 專屬被擋」還是「整台機器對外都不通」。

    NPA 失敗但這兩家成功 → 針對 NPA 的封鎖（地域 / WAF / 出口 IP 黑名單）。
    三家都失敗          → 容器整體 egress 或 DNS 問題，與 NPA 無關。
    """
    for label, host, path in PEERS:
        t0 = time.time()
        try:
            raw = _https_get(host, path, timeout=TIMEOUT)
            status = raw.split(" ", 2)[1] if raw.startswith("HTTP/") else "?"
            out(f"peer_{label}", "ok" if status.startswith("2") else f"HTTP_{status}",
                host=host, status=status, len=len(raw),
                ms=int((time.time() - t0) * 1000))
        except Exception as e:
            out(f"peer_{label}", "FAIL", host=host, err=f"{type(e).__name__}: {e}",
                ms=int((time.time() - t0) * 1000))


def _find_meta(html, name):
    import re
    m = re.search(rf'<meta[^>]+name="{name}"[^>]+content="([^"]*)"', html, re.I)
    if not m:
        m = re.search(rf'<meta[^>]+content="([^"]*)"[^>]+name="{name}"', html, re.I)
    return m.group(1) if m else ""


def _find_key(html):
    import re
    m = re.search(r'<input[^>]+id="_key"[^>]+value="([^"]*)"', html, re.I)
    if not m:
        m = re.search(r'<input[^>]+value="([^"]*)"[^>]+id="_key"', html, re.I)
    return m.group(1) if m else ""


def main():
    if "--patched" in sys.argv:
        from gevent import monkey
        monkey.patch_all()
        print(">>> 已套用 gevent monkey.patch_all()，重現 prod worker 的環境\n")
    print(f"=== 警政署連線診斷 {time.strftime('%Y-%m-%d %H:%M:%S')} "
          f"target={HOST} ===\n")
    check_gevent()
    check_egress()
    v4, v6 = check_dns()
    if v4:
        check_tcp(v4, socket.AF_INET, "v4")
    if v6:
        check_tcp(v6, socket.AF_INET6, "v6")
    if "--dns-stress" in sys.argv:
        check_resolv_conf()
        dns_stress()
        return
    check_tls()
    check_http_chain()
    check_peers()
    print("\n=== 完 ===")


if __name__ == "__main__":
    main()
