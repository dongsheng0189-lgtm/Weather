"""monitor 自己的資料表。

寫在 app-unified 既有的 Postgres 裡，但用 mon_ 前綴獨立成兩張表，
app-unified 從不讀寫它們。這是「最小可用版」的取捨：省掉另建一套 DB 的
運維成本；完整隔離版見 2026-05-08 monitor spec。

不做欄位加密：本服務只收 hub_block 這類診斷事件（阻塞秒數 + 程式碼堆疊），
結構上不含個資。日後若要擴大收一般 app log，**必須先補加密**。
"""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class ProbeSample(db.Model):
    __tablename__ = "mon_probe_samples"

    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    target = db.Column(db.String(16), nullable=False, index=True)  # public / origin
    verdict = db.Column(db.String(20), nullable=False, index=True)
    code = db.Column(db.Integer)
    t_dns = db.Column(db.Float)
    t_conn = db.Column(db.Float)
    t_tls = db.Column(db.Float)
    t_ttfb = db.Column(db.Float)
    t_total = db.Column(db.Float)
    cf_ray = db.Column(db.String(40))
    app_pid = db.Column(db.Integer)          # pid 變動 = worker 被重啟
    app_uptime_s = db.Column(db.Float)
    err = db.Column(db.Text)


class Event(db.Model):
    __tablename__ = "mon_events"

    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    source = db.Column(db.String(32), nullable=False)            # app-unified
    kind = db.Column(db.String(32), nullable=False, index=True)  # hub_block
    severity = db.Column(db.String(16), nullable=False)
    summary = db.Column(db.String(500), nullable=False)
    detail = db.Column(db.Text)              # 完整堆疊，不截斷
