"""電話白名單 — DB 存取 + 比對。

號碼加密存（EncryptedText），故無法 WHERE phone=? 查；名單小 → 載入全部解密、
正規化後在記憶體比對。social.py 維持純函數無 DB，白名單 DB 邏輯集中此處。
"""
from datetime import datetime

from extensions import db
from models import VettingWhitelistPhone
from vetting.social import normalize_phone


def _canonical_map():
    """{正規化號碼: row} —— 解密全部、跳過解不出/非法的。"""
    out = {}
    for r in VettingWhitelistPhone.query.all():
        c = normalize_phone(r.phone or "")
        if c:
            out[c] = r
    return out


def is_whitelisted(phone) -> bool:
    canonical = normalize_phone(phone or "")
    if not canonical:
        return False
    return canonical in _canonical_map()


def add(phone, *, user_id=None, user_name=None) -> str:
    canonical = normalize_phone(phone or "")
    if not canonical:
        return "invalid"
    if canonical in _canonical_map():
        return "exists"
    db.session.add(VettingWhitelistPhone(
        phone=canonical, created_by=user_id, created_by_name=user_name,
        created_at=datetime.utcnow(),
    ))
    db.session.commit()
    return "ok"


def list_all() -> list:
    rows = (VettingWhitelistPhone.query
            .order_by(VettingWhitelistPhone.created_at.desc())
            .all())
    return [{
        "id": r.id,
        "phone": normalize_phone(r.phone or "") or (r.phone or ""),
        "created_by_name": r.created_by_name or "",
        "created_at": r.created_at.isoformat() if r.created_at else "",
    } for r in rows]


def update(entry_id, phone) -> str:
    canonical = normalize_phone(phone or "")
    if not canonical:
        return "invalid"
    row = VettingWhitelistPhone.query.get(entry_id)
    if not row:
        return "not_found"
    others = {normalize_phone(r.phone or "")
              for r in VettingWhitelistPhone.query.all() if r.id != entry_id}
    if canonical in others:
        return "exists"
    row.phone = canonical
    db.session.commit()
    return "ok"


def delete(entry_id) -> bool:
    row = VettingWhitelistPhone.query.get(entry_id)
    if not row:
        return False
    db.session.delete(row)
    db.session.commit()
    return True
