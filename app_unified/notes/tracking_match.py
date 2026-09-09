"""建檔追蹤的比對規則 —— 刻意不 import flask / models / db。

完整掃描與增量掃描是同一套規則、只有輸入不同（全部資料 vs 水位之後的資料）。
把規則抽在這裡，兩條路徑就不會各自演化出不同的比對行為，也讓大小寫／部分比對
之類的邊界可以單獨測試，不必架 DB。

規則本身：**不分大小寫的子字串比對**，與現有關鍵字搜尋（notes/routes.py 的
_note_matches）一致。不分詞、不做模糊比對 —— 使用者已接受「只是順口提到也會被
抓進來」，那正是「不相關」按鈕存在的理由。
"""


def normalize(keyword):
    """關鍵字正規化：去頭尾空白 + 轉小寫。None 視為空字串。"""
    return (keyword or "").strip().lower()


def text_matches(needle, text):
    """needle 必須是已 normalize 過的字串。

    空 needle 一律回 False —— 空字串是任何字串的子字串，不擋掉的話一個空關鍵字
    就會把全店資料都收進追蹤表。
    """
    if not needle:
        return False
    return needle in (text or "").lower()


def find_hits(keyword, records):
    """records: (target_type, target_id, texts) 的可迭代物，texts 是字串序列。

    回傳命中的 (target_type, target_id) list，順序與輸入一致。每筆資料最多出現
    一次（多個欄位都命中也只算一次）。
    """
    needle = normalize(keyword)
    if not needle:
        return []
    out = []
    for target_type, target_id, texts in records:
        if any(text_matches(needle, t) for t in texts):
            out.append((target_type, target_id))
    return out
