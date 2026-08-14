"""DATABASE_URL 的解析。

Zeabur 的 ${VAR} 引用只在該 service 作用域內有那個變數時才展開。從別的
service 複製連線字串過來時引用不會被解析 —— SQLAlchemy 只會丟
"invalid literal for int() with base 10: '${POSTGRESQL_PORT}'"，
完全看不出真正原因（2026-08-14 建 monitor 時實際踩到）。
"""
import pytest


def test_rejects_unexpanded_variable_reference(monkeypatch):
    import app as monitor_app
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://root:pw@${POSTGRESQL_HOST}:${POSTGRESQL_PORT}/zeabur")

    with pytest.raises(RuntimeError) as e:
        monitor_app._database_url()
    assert "未展開" in str(e.value), "錯誤訊息要說清楚是變數沒展開"


def test_missing_url_is_explicit(monkeypatch):
    import app as monitor_app
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="未設定"):
        monitor_app._database_url()


def test_normalises_postgres_scheme(monkeypatch):
    """Zeabur 給 postgres://，SQLAlchemy 2.x 只認 postgresql://。"""
    import app as monitor_app
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/db")
    assert monitor_app._database_url() == "postgresql://u:p@host:5432/db"


def test_accepts_normal_url(monkeypatch):
    import app as monitor_app
    url = "postgresql://u:p@postgresql.zeabur.internal:5432/zeabur"
    monkeypatch.setenv("DATABASE_URL", url)
    assert monitor_app._database_url() == url
