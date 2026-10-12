"""与本机 Appium Runner 通信的轻量客户端。

云端部署时 FastAPI 不直接接触 Android 设备，而是调用用户本机运行的
local_runner/appium_runner.py。开发环境下默认地址为 127.0.0.1:8765。
"""
import os
from typing import Any, Dict

import requests


RUNNER_URL = os.getenv("LOCAL_RUNNER_URL", "http://127.0.0.1:8765").rstrip("/")
RUNNER_TOKEN = os.getenv("LOCAL_RUNNER_TOKEN", "")


class RunnerUnavailable(Exception):
    pass


def runner_request(method: str, path: str, payload: Dict[str, Any] | None = None,
                   timeout: int = 15) -> Dict[str, Any]:
    try:
        headers = {"X-LYAITEST-RUNNER-TOKEN": RUNNER_TOKEN} if RUNNER_TOKEN else {}
        response = requests.request(
            method,
            f"{RUNNER_URL}{path}",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise RunnerUnavailable(f"本地 Runner 不可用: {RUNNER_URL} ({exc})") from exc

    try:
        data = response.json()
    except ValueError:
        data = {"detail": response.text[:500]}
    if response.status_code >= 400:
        raise RunnerUnavailable(data.get("detail", f"Runner 返回 HTTP {response.status_code}"))
    return data


def runner_health() -> Dict[str, Any]:
    return runner_request("GET", "/health", timeout=5)


def runner_preflight(config: Dict[str, Any]) -> Dict[str, Any]:
    return runner_request("POST", "/preflight", config, timeout=20)


def runner_start_exploration(config: Dict[str, Any]) -> Dict[str, Any]:
    return runner_request("POST", "/explore", config, timeout=20)


def runner_get_job(job_id: str) -> Dict[str, Any]:
    return runner_request("GET", f"/jobs/{job_id}", timeout=10)


def runner_delete_job(job_id: str) -> Dict[str, Any]:
    return runner_request("DELETE", f"/jobs/{job_id}", timeout=20)


def runner_start_pytest(config: Dict[str, Any]) -> Dict[str, Any]:
    return runner_request("POST", "/run", config, timeout=20)


def runner_start_web_run(config: Dict[str, Any]) -> Dict[str, Any]:
    return runner_request("POST", "/web-run", config, timeout=20)


def runner_start_web_explore(config: Dict[str, Any]) -> Dict[str, Any]:
    """启动网页自动探索：Runner 按配置使用有头/无头浏览器。"""
    return runner_request("POST", "/web-explore", config, timeout=20)


def runner_stop_web_explore(job_id: str) -> Dict[str, Any]:
    """请求停止网页探索；Runner 会保留已生成的中间产物。"""
    return runner_request("POST", f"/web-explore/{job_id}/stop", {}, timeout=20)


def runner_start_web_login(config: Dict[str, Any]) -> Dict[str, Any]:
    """启动预登录：Runner 有头浏览器打开站点，用户手动登录后保存 storageState。"""
    return runner_request("POST", "/web-login", config, timeout=20)


def runner_save_web_login(job_id: str) -> Dict[str, Any]:
    """保存预登录态：Runner 把 cookie + localStorage 写入 storage_state.json。"""
    return runner_request("POST", f"/web-login/{job_id}/save", {}, timeout=20)


def runner_cancel_web_login(job_id: str) -> Dict[str, Any]:
    """取消预登录：直接关闭浏览器，不保存任何状态。"""
    return runner_request("POST", f"/web-login/{job_id}/cancel", {}, timeout=20)


def runner_start_record(config: Dict[str, Any]) -> Dict[str, Any]:
    return runner_request("POST", "/record", config, timeout=20)


def runner_get_active_record() -> Dict[str, Any]:
    return runner_request("GET", "/record", timeout=10)


def runner_stop_record(job_id: str) -> Dict[str, Any]:
    return runner_request("POST", f"/record/{job_id}/stop", {}, timeout=20)


def runner_start_pi_agent(config: Dict[str, Any]) -> Dict[str, Any]:
    """启动 pi-agent 自愈：Runner 预取真实页面 + 生成骨架，再由 pi-agent 修到通过。

    Runner 同一时刻只接受一个自愈任务，重复提交会返回 409。
    """
    return runner_request("POST", "/pi-agent", config, timeout=20)


def runner_stop_pi_agent(job_id: str) -> Dict[str, Any]:
    """终止自愈：模型可能静默空转，需要人工叫停。"""
    return runner_request("POST", f"/pi-agent/{job_id}/stop", {}, timeout=20)
