#!/usr/bin/env python3
"""容器內 DNS 守望 — 2026-09-05 起的 EAI_AGAIN 事故取證用。

背景：app-unified 容器對外解析間歇失敗（`[Errno -3] Temporary failure in name
resolution`），使用者看到「警政署系統暫時無法連線」。發作是陣發的、抓不到現行犯，
所以放一支常駐程式等下一波。

每 5 秒同時做兩件事，這是重點：
  1. 正常的 `socket.getaddrinfo()`  —— app 實際走的路
  2. **直接對 resolv.conf 的 nameserver 發原始 DNS 查詢，讀 rcode**
     —— getaddrinfo 只會回一個籠統的 EAI_AGAIN，分不出是「CoreDNS 回 SERVFAIL」
        還是「根本沒回應」。原始查詢才看得到真正的失敗形態。

判讀（看 /tmp/dnswatch.log）：
    raw_dns=SERVFAIL                  CoreDNS 有回應但自己解不出外部網域(上游壞) → Zeabur
    raw_dns=TIMEOUT                   CoreDNS 完全沒回(封包掉/掛了) → single-request-reopen 可能有效
    raw_dns=REFUSED                   CoreDNS 拒絕服務 → Zeabur
    getaddrinfo=FAIL 但 raw_dns=NOERROR   壞在 glibc 那層(A/AAAA 並行競態)
                                          → single-request-reopen 正是解

健康時安靜，每 15 分鐘才寫一行 `still ok`，所以檔案很小。

部署（容器一重啟就沒了，要重貼；Zeabur web shell 會把多行貼上內容斷行，故用單行）：
    # 產生單行安裝指令：
    #   echo "echo $(gzip -9c scripts/dnswatch.py | base64 -w0) | base64 -d | gunzip > /tmp/w.py && nohup python3 /tmp/w.py >/dev/null 2>&1 & echo started"
    # 現成的 base64 存在同目錄的 dnswatch.b64.txt
    cat /tmp/dnswatch.log        # 看結果
"""
import socket,struct,time,random,os
LOG='/tmp/dnswatch.log'
H='eze8.npa.gov.tw'
RC={0:'NOERROR',1:'FORMERR',2:'SERVFAIL',3:'NXDOMAIN',4:'NOTIMP',5:'REFUSED'}
def ns():
    try:
        for l in open('/etc/resolv.conf'):
            if l.startswith('nameserver'): return l.split()[1]
    except Exception: pass
    return '127.0.0.1'
NS=ns()
def q(name,ns,timeout=5):
    """發一個原始 A 查詢，回 (rcode字串, 毫秒) 或 ('TIMEOUT'/'ERR:...', 毫秒)。"""
    i=random.randint(0,65535)
    pkt=struct.pack('>HHHHHH',i,0x0100,1,0,0,0)
    for p in name.split('.'): pkt+=bytes([len(p)])+p.encode()
    pkt+=b'\x00'+struct.pack('>HH',1,1)
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(timeout)
    t=time.time()
    try:
        s.sendto(pkt,(ns,53)); d,_=s.recvfrom(4096)
        rc=d[3]&0x0F
        return RC.get(rc,'RCODE%d'%rc), int((time.time()-t)*1000)
    except socket.timeout:
        return 'TIMEOUT', int((time.time()-t)*1000)
    except Exception as e:
        return 'ERR:%s'%type(e).__name__, int((time.time()-t)*1000)
    finally: s.close()
def log(m):
    line='%s %s'%(time.strftime('%Y-%m-%d %H:%M:%S'),m)
    print(line,flush=True)
    try:
        with open(LOG,'a') as f: f.write(line+'\n')
    except Exception: pass
log('watch start ns=%s host=%s pid=%d'%(NS,H,os.getpid()))
ok=0
while True:
    t=time.time()
    try:
        socket.getaddrinfo(H,443,0,socket.SOCK_STREAM); gai='ok(%dms)'%int((time.time()-t)*1000)
    except Exception as e:
        gai='FAIL %s: %s (%dms)'%(type(e).__name__,e,int((time.time()-t)*1000))
    rc,ms=q(H,NS)
    if gai.startswith('ok') and rc=='NOERROR':
        ok+=1
        if ok%180==0: log('still ok x%d (raw %s %dms)'%(ok,rc,ms))
    else:
        log('>>> getaddrinfo=%s | raw_dns=%s %dms'%(gai,rc,ms)); ok=0
    time.sleep(5)
