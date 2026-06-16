#!/usr/bin/env python3
"""一次性診斷：故意對警政署(NPA)送「錯誤驗證碼」，把回應頁面 dump 出來。

目的：Fix B 要把「驗證碼錯」判準（現有偵測 `"驗證碼" in text and ("錯誤" or "不正確")`
很脆）。但驗證碼是 NPA 端驗的，必須先看 NPA 在驗證碼錯時實際回什麼頁面才改得對。

用法（在 prod 容器、app 根目錄 /app）：
    python3 scripts/npa_wrong_captcha_probe.py

它會走完整 NPA 流程（取首頁 token → JWT → 抓驗證碼圖），然後**送錯誤驗證碼 0000**，
印出狀態/長度/關鍵字命中，並把完整回應寫到 /tmp/npa_wrong.txt。只對 NPA 送 1 次請求。
"""
import os
import sys

# 讓 `from vetting...` 能 import（script 在 scripts/，repo root 要進 sys.path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    from vetting.http_client import make_session
    from vetting import npa

    try:
        s = make_session()
        tokens, _ = npa.fetch_home_state(s)
        jwt = npa.fetch_auth_token(s, tokens)
        npa.fetch_captcha_bytes(s)  # 讓 NPA 產生並綁定驗證碼挑戰（不解，故意送錯）
    except Exception as e:
        print("PREP_FAILED:", type(e).__name__, e)
        return

    payload = {
        "QS_NAME": "測試", "QS_NAMEA": "測試",
        "QS_ID": "A123456789", "QS_IDA": "A123456789",
        "answer": "0000", "answerA": "0000",
        "_csrf": tokens["_csrf"],
        "Authorization": f"Bearer {jwt}",
    }
    try:
        r = s.post(npa.QUERY_URL, data=payload,
                   headers={"Referer": npa.HOME_URL}, timeout=15)
    except Exception as e:
        print("POST_FAILED:", type(e).__name__, e)
        return

    text = r.text
    with open("/tmp/npa_wrong.txt", "w", encoding="utf-8") as f:
        f.write(text)

    print("STATUS", r.status_code, "LEN", len(text))
    print("has_驗證碼:", "驗證碼" in text,
          "| has_錯誤:", "錯誤" in text,
          "| has_不正確:", "不正確" in text)
    # 印出關鍵字附近片段，方便找正確的判斷標記
    for kw in ["驗證碼", "錯誤", "不正確", "captcha", "answer", "alert", "msg"]:
        i = text.find(kw)
        if i >= 0:
            print(f"--- around '{kw}' @ {i} ---")
            print(text[max(0, i - 80):i + 160])
    print("=== 完整回應已寫入 /tmp/npa_wrong.txt（可 cat 查看）===")


if __name__ == "__main__":
    main()
