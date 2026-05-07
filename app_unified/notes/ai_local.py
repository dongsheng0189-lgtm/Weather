"""Local Python AI 摘要 pipeline — 取代 Gemini 雲端呼叫，避免敏感資料外流。

策略（無 LLM）：
1. notes / checklist 已有 category 標籤 → 直接分桶（4 類：員工/客人/機台/雜項）
2. 每桶內按 status × 時間區間分到 3 段：✅ 已完成 / ❌ 區間內未完成 / 📜 歷史未完成
3. 段內用 sentence-transformers (multilingual MiniLM) embedding + DBSCAN 聚類同一事件
4. 每 cluster 取最詳細那筆作代表 + 標狀態詞（✓ 已完成 / 🟡 跟進中）

無「店長視角總結」段（extractive 做不到 LLM 級重組）。
"""
import logging
import re
from collections import defaultdict
from datetime import timezone

logger = logging.getLogger("ai_local")

CATEGORY_TO_BUCKET = {
    "employee": ("👥 員工", 1),
    "customer": ("🧑‍🤝‍🧑 客人", 2),
    "machine": ("🔧 機台", 3),
    "misc": ("📋 雜項", 4),
}

# 狀態關鍵字（句子內偵測）
DONE_PAT = re.compile(r"已完成|已解決|已處理|處理完成|搞定|結案|done|finished")
PROGRESS_PAT = re.compile(r"跟進中|處理中|觀察中|追蹤中|待追蹤|持續觀察|持續處理|進行中")

_model = None


def _get_model():
    """Lazy load — only first call. Subsequent calls reuse cached instance."""
    global _model
    if _model is None:
        logger.warning("[ai_local] loading sentence-transformer model (first call, ~5-15 sec)...")
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        logger.warning("[ai_local] model loaded.")
    return _model


def _utc_naive(dt):
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _categorize_notes(notes, since, until):
    """一般筆記分桶 + 分時段（同 _build_categorized_notes 邏輯）"""
    buckets = defaultdict(lambda: {"done": [], "undone": [], "hist": []})

    for n in notes:
        cat = getattr(n, "category", None) or "misc"
        if cat not in CATEGORY_TO_BUCKET:
            cat = "misc"
        is_resolved = (getattr(n, "status", None) == "resolved")

        ra = _utc_naive(getattr(n, "resolved_at", None))
        ua = _utc_naive(getattr(n, "updated_at", None))

        if since is None:
            target = "done" if is_resolved else "undone"
        else:
            resolved_in_range = (is_resolved and ra is not None and ra >= since
                                 and (until is None or ra < until))
            note_in_range = (ua is not None and ua >= since
                             and (until is None or ua < until))
            if resolved_in_range:
                target = "done"
            elif is_resolved:
                continue  # 區間外已解決 — 不顯示
            elif note_in_range:
                target = "undone"
            else:
                target = "hist"

        author = n.author_name or (n.author.username if n.author else "?")
        buckets[cat][target].append({
            "title": n.title or "",
            "content": n.content or "",
            "author": author,
        })

    return buckets


def _categorize_checklists(notes, since, until):
    """確認表單（note_type='checklist'）item 分桶 + 分時段"""
    buckets = defaultdict(lambda: {"done": [], "undone": [], "hist": []})

    for n in notes:
        if (n.note_type or "note") != "checklist":
            continue
        cat = getattr(n, "category", None) or "misc"
        if cat not in CATEGORY_TO_BUCKET:
            cat = "misc"
        note_title = n.title or ""

        for item in (n.checklist_items or []):
            text = (item.text or "").strip()
            if not text:
                continue

            ca = _utc_naive(getattr(item, "checked_at", None))
            ua = _utc_naive(getattr(item, "updated_at", None))
            is_checked = bool(item.is_checked)

            if since is None:
                target = "done" if is_checked else "undone"
            else:
                checked_in_range = (is_checked and ca is not None and ca >= since
                                    and (until is None or ca < until))
                item_in_range = (ua is not None and ua >= since
                                 and (until is None or ua < until))
                if checked_in_range:
                    target = "done"
                elif is_checked:
                    continue
                elif item_in_range:
                    target = "undone"
                else:
                    target = "hist"

            who = (item.checked_by_name if is_checked else "") or "?"
            buckets[cat][target].append({
                "title": f"[{note_title}] {text}" if note_title else text,
                "content": "",
                "author": who,
            })

    return buckets


def _merge_buckets(b1, b2):
    out = defaultdict(lambda: {"done": [], "undone": [], "hist": []})
    for cat in CATEGORY_TO_BUCKET:
        out[cat]["done"] = b1.get(cat, {}).get("done", []) + b2.get(cat, {}).get("done", [])
        out[cat]["undone"] = b1.get(cat, {}).get("undone", []) + b2.get(cat, {}).get("undone", [])
        out[cat]["hist"] = b1.get(cat, {}).get("hist", []) + b2.get(cat, {}).get("hist", [])
    return out


def _embed_text(item):
    return (item["title"] + " " + item["content"]).strip() or item["title"]


def _cluster_items(items, eps=0.4):
    """同一事件聚類。回傳 list[list[item]]，每子 list 是一群。"""
    if len(items) <= 1:
        return [[it] for it in items]

    import numpy as np
    from sklearn.cluster import DBSCAN

    texts = [_embed_text(it) for it in items]
    model = _get_model()
    embs = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    dist = 1 - embs @ embs.T
    np.fill_diagonal(dist, 0)
    dist = np.maximum(dist, 0)

    labels = DBSCAN(eps=eps, min_samples=1, metric="precomputed").fit(dist).labels_

    cluster_to_items = defaultdict(list)
    for it, lbl in zip(items, labels):
        cluster_to_items[lbl].append(it)
    return list(cluster_to_items.values())


def _format_cluster(cluster):
    """從 cluster 抽代表 + 狀態標記，回傳 bullet 字串"""
    rep = max(cluster, key=lambda x: len(x["title"]) + len(x["content"]))
    title = rep["title"]
    content = rep["content"]
    author = rep["author"]

    line = f"【{author}】{title}"

    if content:
        first = content.strip().split("\n")[0].strip()
        if first and first != title:
            if len(first) > 100:
                first = first[:100] + "…"
            line += f" — {first}"

    full = (title or "") + " " + (content or "")
    if DONE_PAT.search(full):
        line += " ✓"
    elif PROGRESS_PAT.search(full):
        line += " 🟡"

    if len(cluster) > 1:
        names = sorted({c["author"] for c in cluster if c.get("author")})
        line += f"（{len(cluster)} 筆"
        if len(names) > 1:
            line += f" / {', '.join(names)}"
        line += "）"

    return line


def summarize_local(notes, since, until, store_label, range_label, inrange_label):
    """主入口：notes (含 checklist) → markdown 摘要字串"""
    logger.warning("[ai_local] === starting LOCAL python pipeline (no cloud LLM) ===")
    logger.warning("[ai_local] notes count=%d since=%s until=%s", len(notes), since, until)

    note_buckets = _categorize_notes(
        [n for n in notes if (n.note_type or "note") != "checklist"], since, until
    )
    cl_buckets = _categorize_checklists(notes, since, until)
    buckets = _merge_buckets(note_buckets, cl_buckets)

    lines = [f"# {store_label}{range_label} 摘要", "", "【摘要開始】", ""]

    has_any = False
    for cat_key, (header, _order) in sorted(CATEGORY_TO_BUCKET.items(), key=lambda x: x[1][1]):
        cat_data = buckets.get(cat_key, {"done": [], "undone": [], "hist": []})
        if not (cat_data["done"] or cat_data["undone"] or cat_data["hist"]):
            continue
        has_any = True
        lines.append(f"## {header}")
        sections = [
            ("done", "### ✅ 已完成"),
            ("undone", f"### ❌ {inrange_label}未完成"),
            ("hist", "### 📜 歷史未完成"),
        ]
        for skey, sheader in sections:
            items = cat_data[skey]
            if not items:
                continue
            lines.append(sheader)
            clusters = _cluster_items(items)
            for cl in clusters:
                lines.append(f"- {_format_cluster(cl)}")
            lines.append("")
        lines.append("")

    if not has_any:
        lines.append("（區間內無筆記）")
        lines.append("")

    lines.append("【摘要結束】")

    out = "\n".join(lines).rstrip() + "\n"
    logger.warning("[ai_local] === done, output %d chars ===", len(out))
    return out
