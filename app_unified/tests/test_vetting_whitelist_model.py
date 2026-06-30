"""VettingWhitelistPhone model — 建立 + 號碼加密落地。"""
from datetime import datetime
from extensions import db
from models import VettingWhitelistPhone


def test_create_and_query(app, db_session):
    row = VettingWhitelistPhone(
        phone="0912345678", created_by=None,
        created_by_name="alice", created_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.commit()
    got = VettingWhitelistPhone.query.filter_by(id=row.id).first()
    assert got is not None
    assert got.phone == "0912345678"          # ORM 端解密回明文
    assert got.created_by_name == "alice"


def test_phone_encrypted_at_rest(app, db_session):
    row = VettingWhitelistPhone(
        phone="0912345678", created_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.commit()
    raw = db_session.execute(
        db.text("SELECT phone FROM vetting_whitelist_phones WHERE id=:i"),
        {"i": row.id},
    ).scalar()
    assert raw.startswith("enc:v1:")          # DB 端是密文
