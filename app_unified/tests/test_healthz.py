"""/healthz 同時是 K8s liveness probe 與外部斷線探針的取樣點。

pid + uptime 是用來分辨「worker 卡住」和「worker 被 SIGKILL 重開」的唯一依據 ——
從外部看這兩件事一模一樣，少了 pid 就查不下去（2026-08 尖峰斷線調查）。
"""
import json


def test_healthz_is_200_and_unauthenticated(client):
    """liveness probe 不帶 cookie，必須 200；擋掉就會被誤判成不健康而重啟。"""
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_healthz_reports_pid_and_uptime(client):
    import os

    resp = client.get("/healthz")
    payload = json.loads(resp.data)

    assert payload["status"] == "ok"
    assert payload["pid"] == os.getpid(), "pid 必須是本 worker 的，否則偵測不到重啟"
    assert isinstance(payload["uptime_s"], (int, float))
    assert payload["uptime_s"] >= 0
