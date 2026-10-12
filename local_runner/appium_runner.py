"""LYAITEST 本地 Android Runner。

启动：
    python local_runner/appium_runner.py --host 127.0.0.1 --port 8765

平台后端只通过本文件提供的 HTTP API 调度任务；真正的 Appium、ADB、Pytest
和 Android 设备都留在本地电脑上。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urljoin, urlparse

import requests
from appium.webdriver.common.appiumby import AppiumBy

# 录制转换器与本文件同目录；显式加入 sys.path，兼容 python -m local_runner.appium_runner 之类的启动方式
sys.path.insert(0, str(Path(__file__).resolve().parent))
from record_converter import parse_recorded_script  # noqa: E402


ROOT = Path(os.getenv("RUNNER_WORKSPACE", str(Path(__file__).resolve().parent / "jobs"))).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
RUNNER_TOKEN = os.getenv("RUNNER_TOKEN", "")
_default_allowed = [Path(__file__).resolve().parents[2], ROOT]
ALLOWED_ROOTS = [Path(item).expanduser().resolve() for item in os.getenv(
    "RUNNER_ALLOWED_ROOTS", ";".join(str(item) for item in _default_allowed)
).split(";") if item.strip()]
JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()

# 同一时刻只允许一场录制：codegen 会弹出真实浏览器窗口，并发录制会互相干扰
RECORDING: Dict[str, Any] = {}
RECORDING_LOCK = threading.Lock()
RECORD_TIMEOUT_SEC = int(os.getenv("RUNNER_RECORD_TIMEOUT", "1800"))

# 预登录会话：有头浏览器手动登录后保存 storageState，供无头探索复用。
LOGIN_SESSIONS: Dict[str, Dict[str, Any]] = {}
LOGIN_LOCK = threading.Lock()

# pi-agent 自愈：模型可能静默空转，必须能人工终止，所以保留进程句柄。
# 同一时刻只跑一个自愈任务 —— 多个 GLM 会话抢配额会互相拖死，也会污染同一份记忆文件。
PI_AGENT_LOCK = threading.Lock()
PI_AGENT_ACTIVE: Dict[str, Any] = {"job_id": None}
PI_AGENT_PROCESSES: Dict[str, subprocess.Popen] = {}


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _set_job(job_id: str, **values):
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {}).update(values)


def _job_stop_requested(job_id: str) -> bool:
    with JOBS_LOCK:
        return bool(JOBS.get(job_id, {}).get("stop_requested"))


def _read_json(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length > 2 * 1024 * 1024:
        raise ValueError("请求体过大")
    raw = handler.rfile.read(length) if length else b"{}"
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return value


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    return not RUNNER_TOKEN or handler.headers.get("X-LYAITEST-RUNNER-TOKEN", "") == RUNNER_TOKEN


def _allowed_path(path: Path) -> bool:
    resolved = path.resolve()
    return any(resolved == root or root in resolved.parents for root in ALLOWED_ROOTS)


def _adb(args, timeout=15):
    try:
        result = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def preflight(config: Dict[str, Any]) -> Dict[str, Any]:
    server = str(config.get("server_url", "http://127.0.0.1:4723")).rstrip("/")
    udid = str(config.get("device_udid", ""))
    package = str(config.get("app_package", ""))
    checks: Dict[str, Any] = {}
    try:
        response = requests.get(f"{server}/status", timeout=5)
        checks["appium"] = {"ok": response.ok, "status_code": response.status_code}
    except requests.RequestException as exc:
        checks["appium"] = {"ok": False, "detail": str(exc)}

    code, stdout, stderr = _adb(["devices"])
    devices = [line.split("\t")[0] for line in stdout.splitlines()[1:] if "\tdevice" in line]
    checks["adb"] = {"ok": code == 0, "devices": devices, "detail": stderr}
    checks["device"] = {"ok": udid in devices, "udid": udid}

    if udid and package:
        code, stdout, stderr = _adb(["-s", udid, "shell", "pm", "list", "packages", package])
        checks["app"] = {"ok": code == 0 and package in stdout, "package": package, "detail": stderr}
    else:
        checks["app"] = {"ok": False, "detail": "device_udid 和 app_package 不能为空"}

    ready = all(item.get("ok") for item in checks.values())
    return {"ready": ready, "checks": checks}


def _bounds_center(bounds: str):
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not match:
        return None
    x1, y1, x2, y2 = map(int, match.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def _bounds_tuple(bounds: str):
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    return tuple(map(int, match.groups())) if match else None


def _is_in_view(bounds: str, screen_size: Dict[str, int]) -> bool:
    """只探索当前屏幕可见区域内的控件，避免点击滚动容器外的坐标。"""
    values = _bounds_tuple(bounds)
    if not values:
        return False
    x1, y1, x2, y2 = values
    width = max(1, int(screen_size.get("width", 0)))
    height = max(1, int(screen_size.get("height", 0)))
    return x2 > 0 and y2 > 0 and x1 < width and y1 < height


def _exploration_label(element: Dict[str, Any]) -> str:
    return " ".join(filter(None, [element.get("desc", ""), element.get("text", "")])).strip()


def _short_exploration_label(element: Dict[str, Any]) -> str:
    raw = element.get("desc", "") or element.get("text", "")
    first_line = re.split(r"[\r\n]", raw, maxsplit=1)[0]
    return re.sub(r"\s+", " ", first_line).strip()[:40]


def _is_safe_exploration_element(element: Dict[str, Any], app_package: str, screen_size: Dict[str, int]) -> bool:
    """只返回被测 App 内明确的控件，避免误点根节点或危险操作。"""
    if element.get("package") != app_package or not element.get("clickable"):
        return False
    if not _exploration_label(element) or not _is_in_view(element.get("bounds", ""), screen_size):
        return False
    center = _bounds_center(element.get("bounds", ""))
    if not center:
        return False

    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", element.get("bounds", ""))
    if match:
        x1, y1, x2, y2 = map(int, match.groups())
        width = max(1, int(screen_size.get("width", 0)))
        height = max(1, int(screen_size.get("height", 0)))
        # 全屏的 Compose View 经常被标记为 clickable，但它不是可探索的业务控件。
        # XML 的 bounds 可能按横屏返回，而 get_window_size() 仍按设备方向返回，兼容两种轴顺序。
        fills_screen = (
            x1 <= 1 and y1 <= 1 and (
                (x2 >= width * 0.9 and y2 >= height * 0.9)
                or (x2 >= height * 0.9 and y2 >= width * 0.9)
            )
        )
        if fills_screen:
            return False

    label = _exploration_label(element)
    # 探索默认不执行会退出/删除数据的动作；这些动作仍会保留在页面 XML 中供后续生成用例审核。
    if re.search(r"退出|删除|卸载|注销|清空|恢复出厂|终止应用|结束应用|同意协议|取消", label):
        return False
    return True


def _page_info(driver):
    source = driver.page_source
    root = ET.fromstring(source)
    elements = []
    signature_parts = []
    for elem in root.iter():
        desc = elem.get("content-desc", "")
        text = elem.get("text", "")
        resource_id = elem.get("resource-id", "")
        package = elem.get("package", "")
        if desc or text or elem.get("clickable") == "true":
            elements.append({
                "desc": desc,
                "text": text,
                "resource_id": resource_id,
                "package": package,
                "class": elem.get("class", ""),
                "clickable": elem.get("clickable") == "true",
                "enabled": elem.get("enabled", "true") == "true",
                "checked": elem.get("checked", "false") == "true",
                "hint": elem.get("hint", ""),
                "password": elem.get("password", "false") == "true",
                "bounds": elem.get("bounds", ""),
            })
        # 页面签名只取稳定的描述、文本和资源 ID；忽略 bounds/层级，避免动画和布局变化制造重复页面。
        stable_desc = re.sub(r"\s+", " ", desc).strip().split(" ", 1)[0]
        stable_text = re.sub(r"\s+", " ", text).strip()
        if stable_desc or stable_text or resource_id:
            signature_parts.append("|".join(filter(None, [stable_desc, stable_text, resource_id])))
    signature = "|".join(sorted(set(signature_parts)))[:4000]
    return source, {"signature": signature, "elements": elements}


def _find_page_element(info: Dict[str, Any], keyword: str):
    for item in info.get("elements", []):
        label = " ".join(filter(None, [item.get("desc", ""), item.get("text", ""), item.get("hint", "")]))
        if keyword in label:
            return item
    return None


def _tap_page_element(driver, element: Dict[str, Any]):
    center = _bounds_center(element.get("bounds", ""))
    if not center:
        raise ValueError("页面元素缺少有效 bounds")
    driver.tap([center])


def _native_attribute(element, name: str) -> str:
    """读取原生控件属性；部分 Appium 版本对缺失属性会直接抛异常。"""
    try:
        return str(element.get_attribute(name) or "")
    except Exception:
        return ""


def _native_element_for(driver, page_element: Dict[str, Any]):
    """按 XML 的 class/bounds/text 找到对应的原生 Appium 元素。

    MuMu 多显示环境下，页面 XML 仍可能正确而坐标截图落在 Launcher 上，
    所以登录和探索优先使用原生元素 click/send_keys，坐标仅作为兼容回退。
    """
    class_name = page_element.get("class", "")
    if not class_name:
        return None
    try:
        candidates = driver.find_elements(AppiumBy.CLASS_NAME, class_name)
    except Exception:
        return None
    target_bounds = page_element.get("bounds", "")
    target_text = page_element.get("text", "")
    target_desc = page_element.get("desc", "")
    target_id = page_element.get("resource_id", "")
    for candidate in candidates:
        rect = ""
        try:
            value = candidate.rect
            if value:
                rect = (
                    f"[{int(value.get('x', 0))},{int(value.get('y', 0))}]"
                    f"[{int(value.get('x', 0) + value.get('width', 0))},"
                    f"{int(value.get('y', 0) + value.get('height', 0))}]"
                )
        except Exception:
            pass
        if target_bounds and rect == target_bounds:
            return candidate
        if (
            (not target_text or _native_attribute(candidate, "text") == target_text)
            and (not target_desc or _native_attribute(candidate, "contentDescription") == target_desc)
            and (not target_id or _native_attribute(candidate, "resourceId") == target_id)
        ):
            return candidate
    return candidates[0] if len(candidates) == 1 else None


def _native_elements_for_page(driver, page_elements):
    """一次性建立当前页面的原生控件缓存，避免每个候选项都查一次 Appium。"""
    by_class = {}
    for class_name in {item.get("class", "") for item in page_elements}:
        if not class_name:
            continue
        try:
            by_class[class_name] = driver.find_elements(AppiumBy.CLASS_NAME, class_name)
        except Exception:
            by_class[class_name] = []

    result = []
    for page_element in page_elements:
        target_bounds = page_element.get("bounds", "")
        target_text = page_element.get("text", "")
        target_desc = page_element.get("desc", "")
        target_id = page_element.get("resource_id", "")
        matched = None
        candidates = by_class.get(page_element.get("class", ""), [])
        for candidate in candidates:
            rect = ""
            try:
                value = candidate.rect
                rect = (
                    f"[{int(value.get('x', 0))},{int(value.get('y', 0))}]"
                    f"[{int(value.get('x', 0) + value.get('width', 0))},"
                    f"{int(value.get('y', 0) + value.get('height', 0))}]"
                )
            except Exception:
                pass
            if target_bounds and rect == target_bounds:
                matched = candidate
                break
            if (
                (not target_text or _native_attribute(candidate, "text") == target_text)
                and (not target_desc or _native_attribute(candidate, "contentDescription") == target_desc)
                and (not target_id or _native_attribute(candidate, "resourceId") == target_id)
            ):
                matched = candidate
                break
        if matched is None and len(candidates) == 1:
            matched = candidates[0]
        result.append(matched)
    return result


def _native_bool(element, name: str) -> bool:
    return _native_attribute(element, name).lower() == "true"


def _wait_page_info(driver, package: str, original_signature: str | None = None,
                    timeout: float = 2.0):
    """短暂等待后只重新 dump 一次 XML，避免同页控件为变化轮询到超时。"""
    # 参考速通流程：点击后给 UI 一个很短的渲染时间，然后立即重新获取 XML。
    # timeout 保留为调用方兼容参数；真正的等待由 settle_seconds 控制。
    settle_seconds = min(max(float(os.getenv("APP_EXPLORATION_SETTLE", "0.25")), 0.1), 0.6)
    time.sleep(settle_seconds)
    try:
        if driver.current_package != package:
            return None
        _, latest = _page_info(driver)
        return latest
    except Exception:
        return None


def _login_credentials(config: Dict[str, Any]):
    phone = str(config.get("test_phone") or os.getenv("TEST_PHONE", "")).strip()
    password_env = str(config.get("test_password_env") or "TEST_PASSWORD").strip()
    password = os.getenv(password_env, "").strip()
    return phone, password, password_env


def auto_login(driver, config: Dict[str, Any]) -> Dict[str, Any]:
    """在探索前处理 InnoYolo 登录页；已登录或未配置账号时不阻断探索。"""
    package = config["app_package"]
    phone, password, password_env = _login_credentials(config)
    deadline = time.time() + 8
    info = None
    activated = False
    while time.time() < deadline:
        if driver.current_package != package and not activated:
            driver.activate_app(package)
            time.sleep(1)
            activated = True
        _, info = _page_info(driver)
        labels = [
            " ".join(filter(None, [item.get("desc", ""), item.get("text", ""), item.get("hint", "")]))
            for item in info["elements"]
        ]
        if any("开启不凡" in label for label in labels) or any(
            item.get("class") == "android.widget.EditText" and item.get("package") == package
            for item in info["elements"]
        ):
            break
        time.sleep(0.5)

    if not info:
        return {"status": "not_needed", "message": "未检测到登录页"}
    login_items = [
        item for item in info["elements"]
        if item.get("package") == package and item.get("class") == "android.widget.EditText"
    ]
    if not login_items:
        return {"status": "not_needed", "message": "当前页面不是登录页"}
    if not phone or not password:
        return {
            "status": "skipped",
            "message": f"未自动登录：请设置 TEST_PHONE 和密码环境变量 {password_env}，并在同一终端启动 Runner",
        }

    diagnostics = {
        "login_fields": len(login_items),
        "phone_filled": False,
        "password_filled": False,
        "agreement_before": None,
        "agreement_after": None,
        "login_button_enabled": False,
        "current_package_before_submit": driver.current_package,
        "current_activity_before_submit": driver.current_activity,
    }
    try:
        phone_item = next((item for item in login_items if "手机" in item.get("hint", "")), login_items[0])
        password_item = next(
            (item for item in login_items if item.get("password") or "密码" in item.get("hint", "")),
            login_items[1] if len(login_items) > 1 else None,
        )
        if not password_item:
            return {"status": "failed", "message": "登录页未找到密码输入框", "diagnostics": diagnostics}

        # 优先使用原生元素操作。MuMu 多显示时 XML 的 bounds 可能不在当前截图层，
        # 但 UiAutomator 原生 click/send_keys 仍会作用于正确的 App 窗口。
        phone_index = login_items.index(phone_item)
        password_index = login_items.index(password_item)
        native_edits = driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")
        if len(native_edits) <= max(phone_index, password_index):
            raise RuntimeError("Appium 未找到登录输入框原生控件")
        native_phone = native_edits[phone_index]
        native_password = native_edits[password_index]
        native_phone.click()
        native_phone.clear()
        native_phone.send_keys(phone)
        diagnostics["phone_filled"] = True
        native_password.click()
        native_password.clear()
        native_password.send_keys(password)
        diagnostics["password_filled"] = True

        # 键盘可能遮挡协议复选框，先收起键盘，再只尝试一次勾选。
        try:
            driver.hide_keyboard()
        except Exception:
            pass
        _, before_agreement = _page_info(driver)
        checkbox_item = next(
            (item for item in before_agreement["elements"] if item.get("class") == "android.widget.CheckBox"),
            None,
        )
        checkboxes = driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.CheckBox")
        diagnostics["agreement_before"] = bool(checkbox_item and checkbox_item.get("checked"))
        if checkbox_item and not checkbox_item.get("checked"):
            if not checkboxes:
                raise RuntimeError("Appium 未找到用户协议复选框原生控件")
            checkboxes[0].click()

        # 等待 Flutter/原生页面把 checked 状态和登录按钮状态同步完成。
        agreement_deadline = time.time() + 3
        while time.time() < agreement_deadline:
            checkboxes = driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.CheckBox")
            if not checkbox_item or (checkboxes and _native_bool(checkboxes[0], "checked")):
                break
            time.sleep(0.3)
        diagnostics["agreement_after"] = bool(checkboxes and _native_bool(checkboxes[0], "checked"))

        # Flutter 页面更新有短暂延迟，等待按钮从 disabled 变为可点击，避免误判后反复拉起 App。
        login_button = None
        button_deadline = time.time() + 5
        while time.time() < button_deadline:
            _, after_input = _page_info(driver)
            login_button = _find_page_element(after_input, "开启不凡")
            if login_button and login_button.get("enabled") and login_button.get("clickable"):
                break
            time.sleep(0.5)
        native_login_button = _native_element_for(driver, login_button) if login_button else None
        diagnostics["login_button_enabled"] = bool(
            login_button and login_button.get("enabled") and login_button.get("clickable")
        )
        if not login_button or not diagnostics["login_button_enabled"] or not native_login_button:
            return {
                "status": "failed",
                "message": "登录按钮未启用，请确认手机号、密码和用户协议已填写",
                "diagnostics": diagnostics,
            }
        native_login_button.click()
        time.sleep(4)
        if driver.current_package != package:
            driver.activate_app(package)
        _, after_login = _page_info(driver)
        still_login = any("开启不凡" in " ".join(filter(None, [
            item.get("desc", ""), item.get("text", ""), item.get("hint", "")
        ])) for item in after_login["elements"])
        if still_login:
            return {
                "status": "failed",
                "message": "已提交登录，但页面仍停留在登录页",
                "diagnostics": diagnostics,
            }
        diagnostics["current_package_after_submit"] = driver.current_package
        diagnostics["current_activity_after_submit"] = driver.current_activity
        return {"status": "success", "message": "已自动登录，开始探索", "diagnostics": diagnostics}
    except Exception as exc:
        return {"status": "failed", "message": f"自动登录异常：{exc}", "diagnostics": diagnostics}


def explore_job(job_id: str, config: Dict[str, Any]):
    driver = None
    started_at = time.time()
    output_dir = ROOT / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    max_depth = max(1, min(int(config.get("max_depth", 3)), 8))
    max_actions = max(1, min(int(config.get("max_actions", 100)), 1000))
    visited = set()
    pages = []
    actions = 0

    try:
        from appium import webdriver
        from appium.options.android import UiAutomator2Options

        options = UiAutomator2Options()
        capabilities = {
            "platformName": "Android",
            "deviceName": config["device_udid"],
            "udid": config["device_udid"],
            "automationName": "UiAutomator2",
            "appPackage": config["app_package"],
            "appActivity": config["app_activity"],
            "noReset": True,
            "autoGrantPermissions": True,
            # 与 appium_auto_test/conftest.py 保持一致，降低 MIUI 初始化阶段失败概率。
            "skipDeviceInitialization": True,
            "disableWindowAnimation": True,
            "uiautomator2ServerLaunchTimeout": 60000,
        }
        capabilities.update(config.get("capabilities") or {})
        options.load_capabilities(capabilities)
        driver = webdriver.Remote(config.get("server_url", "http://127.0.0.1:4723"), options=options)
        # 页面结构通过 page_source 获取；原生控件查找只需要很短的兜底等待。
        driver.implicitly_wait(0.2)
        driver.activate_app(config["app_package"])
        time.sleep(1)
        screen_size = driver.get_window_size()
        click_strategy = str(config.get("click_strategy", "auto")).lower()
        if click_strategy not in {"auto", "native", "coordinate"}:
            click_strategy = "auto"

        login_result = auto_login(driver, config)
        if login_result.get("status") == "failed":
            # 登录失败不伪造已登录结果；仍保存当前页面，便于定位和后续生成登录用例。
            pass
        time.sleep(0.3)

        def visit(page_name: str, depth: int):
            nonlocal actions
            if depth > max_depth or actions >= max_actions:
                return
            if driver.current_package != config["app_package"]:
                driver.activate_app(config["app_package"])
                time.sleep(1)
            source, info = _page_info(driver)
            signature = info["signature"]
            if signature in visited:
                return
            visited.add(signature)
            xml_path = output_dir / f"{len(pages):03d}_{page_name}.xml"
            png_path = output_dir / f"{len(pages):03d}_{page_name}.png"
            xml_path.write_text(source, encoding="utf-8")
            driver.save_screenshot(str(png_path))
            page = {
                "name": page_name,
                "depth": depth,
                "package": driver.current_package,
                "xml": str(xml_path),
                "screenshot": str(png_path),
                "clickable_elements": info["elements"],
            }
            pages.append(page)
            original_signature = signature
            # 只点击被测 App 自己的元素；系统弹窗、浏览器、设置等外部包不参与探索。
            candidates = []
            seen_candidate_labels = set()
            for item in info["elements"]:
                if not _is_safe_exploration_element(item, config["app_package"], screen_size):
                    continue
                # Flutter 的 content-desc 常带多行状态文字；按首段描述去重，
                # 避免同一类开关/卡片被重复点击，和 fast_explore.py 保持一致。
                short_label = _short_exploration_label(item)
                if not short_label or short_label in seen_candidate_labels:
                    continue
                seen_candidate_labels.add(short_label)
                candidates.append(item)
            # 登录失败或账号未配置时只保留登录页证据，不继续点击输入框/协议/注册链接。
            if depth == 0 and login_result.get("status") in {"failed", "skipped"}:
                candidates = []
            # auto/native 只在每个页面做一次原生控件查询；coordinate 可用于标准单显示设备，
            # 适合追求极限速度的场景。当前 MuMu 多显示默认 auto 会优先保证可靠性。
            native_cache = (
                _native_elements_for_page(driver, candidates)
                if click_strategy in {"auto", "native"} else [None] * len(candidates)
            )
            for index, element in enumerate(candidates):
                if actions >= max_actions:
                    break
                center = _bounds_center(element.get("bounds"))
                if not center:
                    continue
                actions += 1
                try:
                    native_element = native_cache[index]
                    if native_element is not None:
                        try:
                            native_element.click()
                        except Exception:
                            # 页面刚刚重绘时缓存对象可能短暂失效，只对当前控件重查一次。
                            native_element = _native_element_for(driver, element)
                            if native_element is not None:
                                native_element.click()
                            else:
                                driver.tap([center])
                    else:
                        # 兼容部分非标准控件；标准 Android/Flutter 控件应走上面的原生路径。
                        driver.tap([center])
                    time.sleep(0.12)
                    # 外部跳转只记录为不可探索入口，不递归进入其他 App。
                    if driver.current_package != config["app_package"]:
                        try:
                            driver.back()
                        finally:
                            driver.activate_app(config["app_package"])
                            _wait_page_info(driver, config["app_package"], timeout=1.5)
                        continue
                    after = _wait_page_info(
                        driver, config["app_package"], original_signature, timeout=1.8
                    )
                    changed = bool(after and after["signature"] != original_signature)
                    if changed:
                        visit(f"{page_name}_click_{index}", depth + 1)
                        driver.back()
                        _wait_page_info(
                            driver, config["app_package"], original_signature, timeout=1.5
                        )
                    # 同页开关/滑块不调用 back，避免误退出当前业务页，也省去一次往返。
                except Exception as exc:
                    page.setdefault("errors", []).append(str(exc))
                    try:
                        driver.back()
                    except Exception:
                        pass
                    if driver.current_package != config["app_package"]:
                        driver.activate_app(config["app_package"])
                        _wait_page_info(driver, config["app_package"], timeout=1.5)

        visit("00_initial", 0)
        result = {
            "total_pages": len(pages),
            "actions": actions,
            "elapsed_seconds": round(time.time() - started_at, 2),
            "click_strategy": click_strategy,
            "login": login_result,
            "pages": pages,
            "output_dir": str(output_dir),
        }
        (output_dir / "exploration_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        _set_job(job_id, status="completed", result=result, finished_at=time.time())
    except Exception as exc:
        _set_job(job_id, status="failed", error=f"{exc}\n{traceback.format_exc()}", finished_at=time.time())
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# element 字段的定位器前缀语法：无前缀按 CSS 处理，保证历史用例零改动；
# role= / text= / testid= 用于承载录制产物（Playwright 官方 codegen 的定位器体系）。
_LOCATOR_EXAMPLE = 'role=button[name="登录"]'


def _resolve_locator(page, element: str):
    """把 steps 的 element 字段解析成 Playwright Locator。

    支持的前缀：
        role=<角色>[name="<可访问名称>"]  → page.get_by_role
        text=<文本>                      → page.get_by_text
        testid=<测试 ID>                 → page.get_by_test_id
    没有前缀时按 CSS 选择器处理。格式错误直接抛错，由调用方标记该步失败，
    避免静默地执行到错误元素上。
    """
    raw = (element or "").strip()
    if raw.startswith("role="):
        body = raw[len("role="):].strip()
        match = re.fullmatch(r"([A-Za-z_][\w-]*)(?:\[name=(?:\"([^\"]*)\"|'([^']*)')\])?", body)
        if not match:
            raise ValueError(f"role 定位器格式错误：{raw}（正确写法如 {_LOCATOR_EXAMPLE}）")
        role = match.group(1)
        name = match.group(2) if match.group(2) is not None else match.group(3)
        return page.get_by_role(role) if name is None else page.get_by_role(role, name=name)
    if raw.startswith("text="):
        text = raw[len("text="):]
        if not text:
            raise ValueError(f"text 定位器缺少文本内容：{raw}")
        return page.get_by_text(text)
    if raw.startswith("testid="):
        test_id = raw[len("testid="):]
        if not test_id:
            raise ValueError(f"testid 定位器缺少取值：{raw}")
        return page.get_by_test_id(test_id)
    return page.locator(raw)


def _web_assertion(page, element: str, expected: str, last_status_code):
    """执行一条 Web 断言，返回 (是否通过, 说明)。"""
    key = element.lower()
    if key in {"status", "status_code", "状态码"}:
        if last_status_code is None:
            return False, "断言状态码失败：之前没有 navigate 步骤记录响应"
        ok = str(last_status_code) == expected
        return ok, f"断言状态码 {expected} → {'通过' if ok else f'失败（实际 {last_status_code}）'}"
    if key in {"url", "地址"}:
        actual = page.url
        ok = expected in actual
        return ok, f"断言 URL 包含 {expected} → {'通过' if ok else f'失败（实际 {actual}）'}"
    if key in {"title", "标题"}:
        actual = page.title()
        ok = expected in actual
        return ok, f"断言标题包含 {expected} → {'通过' if ok else f'失败（实际 {actual}）'}"
    actual = _resolve_locator(page, element).inner_text()
    ok = expected in actual
    return ok, f"断言 {element} 文本包含 {expected} → {'通过' if ok else f'失败（实际 {actual[:120]}）'}"


def _run_web_case_steps(page, job_id: str, case_index: int, case: Dict[str, Any], log) -> Dict[str, Any]:
    """在真实浏览器页面上逐步执行单个用例。"""
    name = case.get("name") or "未命名用例"
    steps = case.get("steps") or []
    logs = []
    passed = failed = skipped = 0
    last_status_code = None
    log(f"\n=== 用例: {name}（{len(steps)} 步）===")

    for idx, step in enumerate(steps):
        action = str(step.get("action") or "").strip().lower()
        element = str(step.get("element") or "").strip()
        value = str(step.get("value") or "").strip()
        expected = str(step.get("expected") or "").strip()
        entry = {
            "step_index": idx + 1,
            "action": action,
            "element": element,
            "value": value,
            "expected": expected,
        }
        try:
            if action in {"navigate", "open", "goto"}:
                url = element or value
                if not url:
                    raise ValueError("navigate 步骤缺少 URL")
                response = page.goto(url, wait_until="load")
                last_status_code = response.status if response else None
                entry["status"] = "passed"
                entry["message"] = f"访问 {url} → HTTP {last_status_code}"
            elif action in {"click", "tap"}:
                if not element:
                    raise ValueError("click 步骤缺少元素选择器")
                _resolve_locator(page, element).click()
                entry["status"] = "passed"
                entry["message"] = f"点击 {element}"
            elif action in {"input", "fill", "type", "send_keys"}:
                if not element:
                    raise ValueError("input 步骤缺少元素选择器")
                _resolve_locator(page, element).fill(value)
                entry["status"] = "passed"
                entry["message"] = f"在 {element} 输入 {value}"
            elif action in {"assert", "verify", "check"}:
                if not expected:
                    raise ValueError("assert 步骤缺少 expected")
                ok, message = _web_assertion(page, element, expected, last_status_code)
                entry["status"] = "passed" if ok else "failed"
                entry["message"] = message
            elif action in {"wait", "wait_for"}:
                if element.isdigit():
                    page.wait_for_timeout(int(element))
                    entry["message"] = f"等待 {element} 毫秒"
                elif element:
                    _resolve_locator(page, element).wait_for()
                    entry["message"] = f"等待元素 {element} 出现"
                else:
                    page.wait_for_timeout(1000)
                    entry["message"] = "等待 1000 毫秒"
                entry["status"] = "passed"
            elif action in {"screenshot", "capture"}:
                shot_path = ROOT / job_id / f"case{case_index}_step{idx + 1}.png"
                page.screenshot(path=str(shot_path), full_page=True)
                entry["status"] = "passed"
                entry["message"] = f"截图已保存 {shot_path}"
                entry["screenshot"] = str(shot_path)
            else:
                entry["status"] = "skipped"
                entry["message"] = f"暂不支持的动作「{action or '(空)'}」，未执行"
        except Exception as exc:
            entry["status"] = "failed"
            entry["message"] = f"{action} {element} 执行失败：{exc}"

        if entry["status"] == "passed":
            passed += 1
        elif entry["status"] == "failed":
            failed += 1
        else:
            skipped += 1
        marker = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}[entry["status"]]
        log(f"  [{marker}] 步骤 {idx + 1}: {entry['message']}")
        logs.append(entry)

    return {
        "name": name,
        "total": len(steps),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "logs": logs,
    }


def web_run_job(job_id: str, config: Dict[str, Any]):
    """用 Playwright 真实执行 Web 用例步骤。

    配置：{"cases": [{"name": ..., "steps": [...]}], "headless": true, "step_timeout_ms": 10000}
    不做任何伪造：无法识别的动作标记为 skipped，执行失败标记为 failed。
    """
    started_at = time.time()
    cases = config.get("cases") or []
    headless = bool(config.get("headless", True))
    step_timeout_ms = int(config.get("step_timeout_ms", 10000) or 10000)
    log_path = ROOT / job_id / "web_run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(text: str):
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        _set_job(job_id, status="failed", log_path=str(log_path),
                 error=f"未安装 playwright，无法执行 Web 用例：{exc}", finished_at=time.time())
        return

    _set_job(job_id, log_path=str(log_path))
    log(f"[web-run] 开始执行：用例数={len(cases)} headless={headless}")
    results = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            try:
                for index, case in enumerate(cases):
                    # 内网测试系统普遍是自签证书，不忽略 https 错误会让 navigate 直接失败
                    # （ERR_CERT_AUTHORITY_INVALID），页面加载不出来，后续步骤会连锁超时。
                    # 录制侧用的是 codegen --ignore-https-errors，两侧必须保持一致。
                    context = browser.new_context(ignore_https_errors=True)
                    page = context.new_page()
                    page.set_default_timeout(step_timeout_ms)
                    try:
                        results.append(_run_web_case_steps(page, job_id, index, case, log))
                    finally:
                        context.close()
            finally:
                browser.close()
    except Exception as exc:
        log(f"[web-run] 执行异常：{exc}")
        _set_job(job_id, status="failed", log_path=str(log_path),
                 error=f"{exc}\n{traceback.format_exc()}", finished_at=time.time())
        return

    summary = {
        "cases": results,
        "passed": sum(item["passed"] for item in results),
        "failed": sum(item["failed"] for item in results),
        "skipped": sum(item["skipped"] for item in results),
        "elapsed_seconds": round(time.time() - started_at, 2),
    }
    log(f"\n[web-run] 完成：通过={summary['passed']} 失败={summary['failed']} "
        f"跳过={summary['skipped']} 耗时={summary['elapsed_seconds']}s")
    _set_job(job_id, status="completed", result=summary, log_path=str(log_path), finished_at=time.time())


# ---------------------------------------------------------------------------
# Web 页面自动探索：无头 BFS 点同源可交互元素，产出元素清单与站点图，供生成用例。
# ---------------------------------------------------------------------------

# 探索默认只读：带这些词的元素一律不点击，避免误删数据、误下单、误支付。
_WEB_DANGEROUS_RE = re.compile(
    r"删除|移除|注销|退出|登出|清空|卸载|支付|付款|下单|购买|结算|提交订单|确认支付|取消订单|解绑|"
    r"重置密码|停用|禁用|导出|导入|下载|上传|保存|新增|添加|编辑|审批|发送|发布|批量"
)
_WEB_INTERACTIVE_SELECTOR = (
    "a[href],button,input,select,textarea,[role=button],[role=link],[role=tab],"
    "[role=menuitem],[role=checkbox],[role=radio],[role=switch],[onclick],[data-testid]"
)
# 只有这些控件会被真正点击；纯输入框等只记录不点击，避免浪费动作数。
_WEB_CLICKABLE_TAGS = {"a", "button"}
_WEB_CLICKABLE_ROLES = {"button", "link", "tab", "menuitem", "checkbox", "radio", "switch"}

_WEB_COLLECT_JS = r"""
() => {
  const nodes = Array.from(document.querySelectorAll(__SELECTOR__));
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return !!rect.width && !!rect.height && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const textOf = (el) => clean(el.innerText || el.textContent || el.value || '');
  const submitsForm = (el) => {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const form = el.form || el.closest('form');
    if (!form) { return false; }
    return (tag === 'button' && (type === '' || type === 'submit'))
      || (tag === 'input' && (type === 'submit' || type === 'image'));
  };
  const simple = (el) => ({
    text: textOf(el).slice(0, 100),
    href: el.getAttribute('href') || '',
    tag: el.tagName.toLowerCase(),
    role: (el.getAttribute('role') || '').toLowerCase(),
    testid: clean(el.getAttribute('data-testid')),
  });
  const out = [];
  let expIndex = 0;
  for (const el of nodes) {
    if (!visible(el)) { continue; }
    const rect = el.getBoundingClientRect();
    const expId = `exp-${expIndex++}`;
    el.setAttribute('data-exp-id', expId);
    out.push({
      exp_id: expId,
      x: Math.round(rect.left + rect.width / 2),
      y: Math.round(rect.top + rect.height / 2),
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || '').toLowerCase(),
      role: (el.getAttribute('role') || '').toLowerCase(),
      submits_form: submitsForm(el),
      text: clean(el.innerText).slice(0, 100),
      aria_label: clean(el.getAttribute('aria-label')),
      placeholder: clean(el.getAttribute('placeholder')),
      title: clean(el.getAttribute('title')),
      value: clean(el.value).slice(0, 100),
      testid: clean(el.getAttribute('data-testid')),
      id: el.getAttribute('id') || '',
      name: el.getAttribute('name') || '',
      href: el.getAttribute('href') || '',
      required: !!el.required,
      inputmode: el.getAttribute('inputmode') || '',
      min: el.getAttribute('min') || '',
      max: el.getAttribute('max') || '',
      maxlength: el.getAttribute('maxlength') || '',
      pattern: el.getAttribute('pattern') || '',
    });
  }
  const visibleItems = (selector) => Array.from(document.querySelectorAll(selector)).filter(visible).map(simple).slice(0, 200);
  const headings = Array.from(document.querySelectorAll('h1,h2,h3,[role="heading"]')).filter(visible)
    .map(textOf).filter(Boolean).slice(0, 50);
  const tables = Array.from(document.querySelectorAll('table,[role="table"]')).filter(visible).slice(0, 50).map((table) => ({
    headers: Array.from(table.querySelectorAll('thead th,[role="columnheader"]')).map(textOf).filter(Boolean).slice(0, 50),
    row_count: table.querySelectorAll('tbody tr,[role="row"]:not([role="columnheader"])').length,
    text: textOf(table).slice(0, 500),
  }));
  const pagination = visibleItems('[aria-label*="页" i],[aria-label*="page" i],[class*="pagination" i],[class*="pager" i]');
  return {
    url: location.href,
    title: document.title,
    headings,
    links: visibleItems('a[href]'),
    buttons: visibleItems('button,[role="button"]'),
    inputs: visibleItems('input,select,textarea'),
    tables,
    pagination,
    elements: out,
  };
}
""".replace("__SELECTOR__", repr(_WEB_INTERACTIVE_SELECTOR))


def _safe_name(text: str) -> str:
    value = re.sub(r"[^\w\-]+", "_", (text or "page").strip())
    return (value or "page")[:40]


def _web_implicit_role(tag: str, type_: str, explicit: str) -> str:
    """补全隐式 ARIA 角色，让 role= 定位器尽量贴近 Playwright 的 get_by_role。"""
    if explicit:
        return explicit
    if tag == "a":
        return "link"
    if tag == "button" or (tag == "input" and type_ in {"submit", "button", "reset"}):
        return "button"
    if tag == "input" and type_ in {"checkbox", "radio"}:
        return type_
    if tag == "select":
        return "combobox"
    if tag in {"input", "textarea"}:
        return "searchbox" if type_ == "search" else "textbox"
    return ""


def _web_access_name(item: Dict[str, Any]) -> str:
    for key in ("aria_label", "text", "placeholder", "title", "value"):
        value = str(item.get(key) or "").strip()
        if value:
            return value[:40]
    return ""


def _web_locators(item: Dict[str, Any]) -> list:
    """按稳定性排序产出候选定位器；_resolve_locator 已支持这些前缀，可直接执行。"""
    locators = []
    if item.get("testid"):
        locators.append(f"testid={item['testid']}")
    role = _web_implicit_role(str(item.get("tag") or ""), str(item.get("type") or ""),
                              str(item.get("role") or ""))
    name = _web_access_name(item)
    if role and name:
        locators.append(f'role={role}[name="{name}"]')
    element_id = str(item.get("id") or "").strip()
    if re.fullmatch(r"[A-Za-z][\w-]{0,49}", element_id):
        locators.append(f"#{element_id}")
    text = str(item.get("text") or "").strip()
    if text and len(text) <= 30:
        locators.append(f"text={text}")
    name_attr = str(item.get("name") or "").strip()
    if name_attr and item.get("tag") in {"input", "select", "textarea"}:
        locators.append(f'{item["tag"]}[name="{name_attr}"]')
    return locators


def _web_element_record(item: Dict[str, Any], origin: str) -> Dict[str, Any]:
    """把页面收集到的原始元素归一成探索产物：定位器、类型、是否允许点击。"""
    tag = str(item.get("tag") or "")
    type_ = str(item.get("type") or "")
    role = str(item.get("role") or "")
    locators = _web_locators(item)
    label = _web_access_name(item) or str(item.get("testid") or "") or tag
    href = str(item.get("href") or "")
    external = False
    if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
        external = urlparse(urljoin(origin, href)).netloc != urlparse(origin).netloc
    dangerous = bool(_WEB_DANGEROUS_RE.search(label) or _WEB_DANGEROUS_RE.search(href))
    is_click = (
        tag in _WEB_CLICKABLE_TAGS
        or role in _WEB_CLICKABLE_ROLES
        or (tag == "input" and type_ in {"submit", "button", "reset", "checkbox", "radio"})
    )
    # 只读探索：提交表单算写操作，只记录不点击。
    if dangerous:
        blocked = "dangerous"
    elif item.get("submits_form"):
        blocked = "form_submit"
    elif external:
        blocked = "external"
    else:
        blocked = ""
    if not locators:
        blocked = blocked or "no_locator"
    return {
        "exp_id": str(item.get("exp_id") or ""),
        "point": {"x": item.get("x"), "y": item.get("y")},
        "label": label, "tag": tag, "type": type_, "role": role,
        "required": bool(item.get("required")),
        "inputmode": str(item.get("inputmode") or ""),
        "min": str(item.get("min") or ""),
        "max": str(item.get("max") or ""),
        "maxlength": str(item.get("maxlength") or ""),
        "pattern": str(item.get("pattern") or ""),
        "text": str(item.get("text") or ""), "testid": str(item.get("testid") or ""),
        "href": href, "kind": "click" if is_click else "input",
        "blocked": blocked, "external": external, "dangerous": dangerous,
        "locators": locators,
        "primary_locator": locators[0] if locators else "",
    }


def _web_form_action_allowed(record: Dict[str, Any], allowed_form_actions: list) -> bool:
    """判断表单提交控件是否命中人工配置的低风险白名单。

    空白名单保持旧的 allow_form_submit 兼容行为：允许所有未命中危险词的提交控件。
    一旦配置了白名单，则只按控件的可访问名称/文本命中；危险词仍由
    _web_element_record 提前拦截，不能通过白名单绕过。
    """
    tokens = [str(item).strip().casefold() for item in (allowed_form_actions or []) if str(item).strip()]
    if not tokens:
        return True
    if any(token in {"*", "全部", "所有"} for token in tokens):
        return True
    source = " ".join(str(record.get(key) or "") for key in ("label", "text", "href")).casefold()
    return any(token in source for token in tokens)


def _web_signature(url: str, records: list, title: str = "", headings: list | None = None) -> str:
    """页面签名取 URL、标题、主要 heading 和稳定标签，忽略查询参数与布局。

    路径会把结尾的 /index.html 归一成 /，否则同一页从 "/" 和 "/index.html"
    进来会算出两个签名，导致重复探索并绕出环。
    """
    parsed = urlparse(url)
    path = re.sub(r"/index\.html?$", "/", parsed.path or "/")
    labels = sorted({str(record.get("label") or "") for record in records if record.get("label")})
    heading_values = [str(value).strip() for value in (headings or []) if str(value).strip()]
    signature_parts = [parsed.netloc + path, str(title or "").strip(), *heading_values, *labels]
    return "|".join(signature_parts)[:4000]


def web_login_job(job_id: str, config: Dict[str, Any]):
    """有头浏览器预登录：用户手动登录后保存 storageState，供无头探索复用。

    流程：
    1. 有头浏览器打开 start_url，用户自行完成登录（填账号密码、过验证码 / SSO）
    2. 前端点击「保存登录态」→ save_event 被设置 → 保存 storageState 并关闭
    3. 前端点击「取消」→ cancel_event 被设置 → 直接关闭不保存
    4. 用户直接关浏览器窗口 → 检测到 page 失效 → 自动结束
    """
    start_url = str(config.get("start_url") or "").strip()
    output_dir = ROOT / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "storage_state.json"

    save_event = threading.Event()
    cancel_event = threading.Event()
    with LOGIN_LOCK:
        LOGIN_SESSIONS[job_id] = {
            "save_event": save_event,
            "cancel_event": cancel_event,
            "state_path": str(state_path),
            "started_url": start_url,
        }

    _set_job(job_id, status="login_waiting", started_at=time.time())

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        _set_job(job_id, status="failed",
                 error=f"未安装 playwright：{exc}", finished_at=time.time())
        with LOGIN_LOCK:
            LOGIN_SESSIONS.pop(job_id, None)
        return

    saved = False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            if start_url:
                page.goto(start_url, wait_until="domcontentloaded")

            while True:
                if save_event.is_set():
                    context.storage_state(path=str(state_path))
                    saved = True
                    break
                if cancel_event.is_set():
                    break
                # 用户直接关了浏览器窗口 → page 不可用 → 自动结束
                try:
                    page.evaluate("1")
                except Exception:
                    break
                time.sleep(0.5)

            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
    except Exception as exc:
        _set_job(job_id, status="failed",
                 error=f"{exc}\n{traceback.format_exc()}", finished_at=time.time())
        with LOGIN_LOCK:
            LOGIN_SESSIONS.pop(job_id, None)
        return

    if saved:
        _set_job(job_id, status="completed",
                 result={"storage_state": str(state_path), "start_url": start_url},
                 finished_at=time.time())
    else:
        _set_job(job_id, status="cancelled", finished_at=time.time())
    with LOGIN_LOCK:
        LOGIN_SESSIONS.pop(job_id, None)


def web_explore_job(job_id: str, config: Dict[str, Any]):
    """受控 BFS 探索站点并输出可审计的结构化证据。

    兼容旧配置 max_depth/max_actions，同时提供参考方案中的安全阈值：
    默认深度 2、总页数最多 80、单页最多 25 个点击动作。探索结果同时写入
    新旧两个 JSON 文件名，避免影响已有用例生成和清理逻辑。
    """
    started_at = time.time()
    output_dir = ROOT / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "web_explore.log"

    def log(message: str):
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")

    _set_job(job_id, log_path=str(log_path), started_at=started_at)
    start_url = str(config.get("start_url") or "").strip()
    max_depth = max(1, min(int(config.get("max_depth", 2)), 8))
    max_actions = max(1, min(int(config.get("max_actions", 100)), 1000))
    max_pages = max(1, min(int(config.get("max_pages", 80)), 80))
    max_actions_per_page = max(1, min(int(config.get("max_actions_per_page", 25)), 25))
    headless = bool(config.get("headless", True))
    headed_hold_seconds = max(0, min(int(config.get("headed_hold_seconds", 5) or 5), 30))
    step_timeout_ms = int(config.get("step_timeout_ms", 10000) or 10000)
    # 默认只读：不提交表单。开启后才会点登录等提交按钮，用于越过登录页。
    allow_form_submit = bool(config.get("allow_form_submit", False))
    raw_allowed_form_actions = config.get("allowed_form_actions") or []
    if isinstance(raw_allowed_form_actions, str):
        raw_allowed_form_actions = re.split(r"[,，\n;；]+", raw_allowed_form_actions)
    elif not isinstance(raw_allowed_form_actions, (list, tuple, set)):
        raw_allowed_form_actions = [raw_allowed_form_actions]
    allowed_form_actions = [str(item).strip() for item in raw_allowed_form_actions if str(item).strip()]
    storage_state = str(config.get("storage_state") or "").strip()
    if not start_url:
        _set_job(job_id, status="failed", error="探索缺少起始 URL", finished_at=time.time())
        return
    log(f"开始探索：url={start_url} headless={headless} max_depth={max_depth} "
        f"max_pages={max_pages} max_actions={max_actions}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        _set_job(job_id, status="failed",
                 error=f"未安装 playwright，无法执行 Web 探索：{exc}", finished_at=time.time())
        return

    origin = urlparse(start_url).netloc
    visited = set()
    pending = [(start_url, "00_initial", 0)]
    pages = []
    edges = []
    actions = 0
    console_errors = []
    request_failures = []
    dialog_events = []
    popup_count = 0

    def same_origin(url: str) -> bool:
        return urlparse(url).netloc == origin

    try:
        with sync_playwright() as playwright:
            browser = None
            context = None
            try:
                browser = playwright.chromium.launch(headless=headless)
                ctx_kwargs = {"ignore_https_errors": True}
                if storage_state and Path(storage_state).is_file():
                    ctx_kwargs["storage_state"] = storage_state
                context = browser.new_context(**ctx_kwargs)
                page = context.new_page()
                page.set_default_timeout(step_timeout_ms)

                def on_console(message):
                    if getattr(message, "type", "") == "error":
                        try:
                            location = message.location or {}
                        except Exception:
                            location = {}
                        console_errors.append({
                            "url": page.url,
                            "text": str(message.text or "")[:1000],
                            "location": location,
                        })

                def on_request_failed(request):
                    request_failures.append({
                        "url": page.url,
                        "request_url": str(request.url)[:1000],
                        "method": request.method,
                        "failure": str(request.failure or "")[:500],
                    })

                def on_dialog(dialog):
                    dialog_events.append({
                        "type": dialog.type,
                        "message": str(dialog.message)[:500],
                        "url": page.url,
                    })
                    try:
                        dialog.dismiss()
                    except Exception:
                        pass

                def on_popup(popup):
                    nonlocal popup_count
                    popup_count += 1
                    try:
                        popup.close()
                    except Exception:
                        pass

                page.on("console", on_console)
                page.on("requestfailed", on_request_failed)
                page.on("dialog", on_dialog)
                context.on("page", on_popup)

                while (pending and actions < max_actions and len(pages) < max_pages
                       and not _job_stop_requested(job_id)):
                    url, name, depth = pending.pop(0)
                    if _job_stop_requested(job_id):
                        break
                    if depth > max_depth or not same_origin(url):
                        continue

                    load_error = ""
                    for wait_until in ("load", "domcontentloaded"):
                        try:
                            page.goto(url, wait_until=wait_until)
                            load_error = ""
                            break
                        except Exception as exc:
                            load_error = str(exc)
                    if load_error:
                        log(f"页面打开失败：{url}；{load_error}")
                        if depth == 0:
                            raise RuntimeError(f"起始页面打开失败：{load_error}")
                        continue

                    try:
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_timeout(200)
                        page.evaluate("window.scrollTo(0, 0)")
                    except Exception:
                        pass

                    state = page.evaluate(_WEB_COLLECT_JS)
                    current_url = str(state.get("url") or url)
                    records = [_web_element_record(item, current_url)
                               for item in (state.get("elements") or [])]
                    signature = _web_signature(
                        current_url, records, state.get("title", ""), state.get("headings") or []
                    )
                    if signature in visited:
                        continue
                    visited.add(signature)

                    page_index = len(pages)
                    shot_path = output_dir / f"{page_index:03d}_{_safe_name(name)}.png"
                    html_path = output_dir / f"{page_index:03d}_{_safe_name(name)}.html"
                    try:
                        page.screenshot(path=str(shot_path), full_page=True)
                        screenshot = str(shot_path)
                    except Exception:
                        screenshot = ""
                    try:
                        html_path.write_text(page.content(), encoding="utf-8")
                        html_snapshot = str(html_path)
                    except Exception:
                        html_snapshot = ""

                    page_errors = [item for item in console_errors if item.get("url") == page.url]
                    page_failures = [item for item in request_failures if item.get("url") == page.url]
                    page_record = {
                        "name": name,
                        "url": current_url,
                        "title": str(state.get("title") or ""),
                        "headings": state.get("headings") or [],
                        "links": state.get("links") or [],
                        "buttons": state.get("buttons") or [],
                        "inputs": state.get("inputs") or [],
                        "tables": state.get("tables") or [],
                        "pagination": state.get("pagination") or [],
                        "depth": depth,
                        "signature": signature,
                        "screenshot": screenshot,
                        "html": html_snapshot,
                        "console_errors": page_errors,
                        "request_failures": page_failures,
                        "interactions": [],
                        "elements": records,
                    }
                    pages.append(page_record)

                    page_actions = 0
                    for el_index, record in enumerate(records):
                        if (_job_stop_requested(job_id) or actions >= max_actions
                                or page_actions >= max_actions_per_page):
                            break
                        blocked = record["blocked"]
                        if blocked == "form_submit" and allow_form_submit:
                            blocked = ("" if _web_form_action_allowed(record, allowed_form_actions)
                                       else "form_submit_not_whitelisted")
                        # 将本次实际判定写回产物，便于用例生成和人工审计区分
                        # 已按白名单放行的提交控件与仍被拦截的提交控件。
                        record["blocked"] = blocked
                        if record["kind"] != "click" or blocked or not record["primary_locator"]:
                            continue

                        before_url = page.url
                        actions += 1
                        page_actions += 1
                        try:
                            point = record.get("point") or {}
                            if (isinstance(point.get("x"), (int, float))
                                    and isinstance(point.get("y"), (int, float))):
                                page.mouse.click(float(point["x"]), float(point["y"]))
                            else:
                                page.locator(record["primary_locator"]).first.click(timeout=3000)
                            page.wait_for_timeout(500)
                        except Exception as exc:
                            try:
                                exp_id = record.get("exp_id")
                                if exp_id:
                                    page.locator(f'[data-exp-id="{exp_id}"]').first.click(
                                        timeout=3000, force=True
                                    )
                                    page.wait_for_timeout(300)
                                else:
                                    raise exc
                            except Exception as fallback_exc:
                                record.setdefault("errors", []).append(str(fallback_exc))
                                continue

                        after_url = page.url
                        after_signature = ""
                        after_title = ""
                        after_headings = []
                        try:
                            after_state = page.evaluate(_WEB_COLLECT_JS)
                            after_title = str(after_state.get("title") or "")
                            after_headings = after_state.get("headings") or []
                            after_records = [_web_element_record(item, after_url)
                                             for item in (after_state.get("elements") or [])]
                            after_signature = _web_signature(
                                after_url, after_records, after_title, after_headings
                            )
                        except Exception:
                            pass
                        changed_state = bool(after_signature and after_signature != signature)
                        page_record["interactions"].append({
                            "action": "click",
                            "element": record["label"],
                            "locator": record["primary_locator"],
                            "before_url": before_url,
                            "after_url": after_url,
                            "changed_url": after_url != before_url,
                            "changed_state": changed_state,
                            "after_title": after_title,
                            "after_headings": after_headings[:10],
                            "after_signature": after_signature,
                            "blocked": False,
                        })
                        if (after_url != before_url or changed_state) and same_origin(after_url):
                            edges.append({
                                "from": page_record["url"],
                                "from_page": page_record["name"],
                                "element": record["label"],
                                "locator": record["primary_locator"],
                                "to": after_url,
                            })
                            if len(pages) + len(pending) < max_pages:
                                pending.append((after_url, f"{name}_click_{el_index}", depth + 1))
                        try:
                            page.goto(current_url, wait_until="domcontentloaded")
                        except Exception:
                            break

                if not headless and headed_hold_seconds and not _job_stop_requested(job_id):
                    log(f"有头探索已完成，浏览器保留 {headed_hold_seconds} 秒供观察")
                    deadline = time.time() + headed_hold_seconds
                    while time.time() < deadline and not _job_stop_requested(job_id):
                        time.sleep(0.2)

                storage_state_path = output_dir / "storage_state.json"
                try:
                    context.storage_state(path=str(storage_state_path))
                except Exception:
                    storage_state_path = None
            finally:
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass
    except Exception as exc:
        log(f"探索异常：{exc}\n{traceback.format_exc()}")
        if _job_stop_requested(job_id):
            _set_job(job_id, status="stopped", finished_at=time.time())
        else:
            _set_job(job_id, status="failed",
                     error=f"{exc}\n{traceback.format_exc()}", finished_at=time.time())
        return

    stopped = _job_stop_requested(job_id)
    result = {
        "start_url": start_url,
        "origin": origin,
        "same_origin_only": True,
        "read_only": not allow_form_submit,
        "allow_form_submit": allow_form_submit,
        "allowed_form_actions": allowed_form_actions,
        "has_login_state": bool(storage_state and Path(storage_state).is_file()),
        "max_depth": max_depth,
        "max_actions": max_actions,
        "max_pages": max_pages,
        "max_actions_per_page": max_actions_per_page,
        "headless": headless,
        "headed_hold_seconds": headed_hold_seconds,
        "stopped": stopped,
        "total_pages": len(pages),
        "actions": actions,
        "console_errors": console_errors,
        "request_failures": request_failures,
        "dialog_events": dialog_events,
        "popup_count": popup_count,
        "elapsed_seconds": round(time.time() - started_at, 2),
        "pages": pages,
        "edges": edges,
        "storage_state": str(storage_state_path) if storage_state_path else "",
        "artifacts": {
            "exploration_json": str(output_dir / "exploration_result_web.json"),
            "legacy_exploration_json": str(output_dir / "web_exploration_result.json"),
            "storage_state": str(storage_state_path) if storage_state_path else "",
            "log": str(log_path),
        },
        "output_dir": str(output_dir),
    }
    result_json = json.dumps(result, ensure_ascii=False, indent=2)
    (output_dir / "exploration_result_web.json").write_text(result_json, encoding="utf-8")
    # 旧文件名继续保留，兼容已有删除、查看和生成用例逻辑。
    (output_dir / "web_exploration_result.json").write_text(result_json, encoding="utf-8")
    _set_job(job_id, status="stopped" if stopped else "completed",
             result=result, finished_at=time.time())


# ---------------------------------------------------------------------------
# pi-agent 自愈：Runner 只负责铺工件与起子进程，真正的修复由 pi-agent 完成。
#
# 语言边界：修复逻辑是 TypeScript（pi_demos/selfheal/agents），Python 不 import 它，
# 两侧只通过「命令行参数 + 工作目录里的文件 + stdout 日志 + 退出码」交互。
# ---------------------------------------------------------------------------

# pi-agent demo 目录（TypeScript 工程）
PI_AGENT_DIR = Path(os.getenv(
    "PI_AGENT_DIR", str(Path(__file__).resolve().parents[1] / "pi_demos" / "selfheal" / "agents")
)).expanduser().resolve()
PI_AGENT_NODE = os.getenv("PI_AGENT_NODE", "node")
PI_AGENT_TIMEOUT_SEC = int(os.getenv("RUNNER_PI_AGENT_TIMEOUT", "1800"))

# 前端要看的是可读日志，ANSI 色码在 Runner 侧就剥掉，避免各处重复处理
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# 工作区 conftest：被测对象是远端真实站点，不再起本地静态服务器
_PI_AGENT_CONFTEST = '''"""pi-agent 自愈工作区夹具（Runner 生成）。

base_url 直接指向 Runner 预取页面时确认的最终地址；
STORAGE_STATE 非空时复用前端预登录保存的登录态。
"""
import pytest

BASE_URL = {base_url!r}
STORAGE_STATE = {storage_state!r}


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


@pytest.fixture()
def page(browser):
    kwargs = dict(ignore_https_errors=True)
    if STORAGE_STATE:
        kwargs["storage_state"] = STORAGE_STATE
    context = browser.new_context(**kwargs)
    pg = context.new_page()
    pg.set_default_timeout({timeout_ms})
    try:
        yield pg
    finally:
        context.close()
'''


def _render_skeleton(case: Dict[str, Any], default_timeout_ms: int) -> str:
    """由声明式步骤生成 pytest 骨架。

    定位器刻意保留成字面量（page.locator('#x')），这样 pi-agent 的 heal_selector
    才能按字面量精确替换 —— 与「声明式步骤是唯一事实来源」的约束保持一致：
    Runner 只翻译，不替用例做判断。
    """
    name = str(case.get("name") or case.get("id") or "web case")
    lines = [
        f'"""{name} —— pi-agent 自愈的起点骨架（Runner 生成）。',
        "",
        "用例预期见同目录的 case.json。",
        '"""',
        "from playwright.sync_api import Page, expect",
        "",
        "",
        "def test_web_case(page: Page, base_url: str):",
        "    page.goto(base_url)",
        "",
    ]
    steps = case.get("steps") or []
    if not steps:
        lines.append("    # case.json 里没有任何步骤")
        return "\n".join(lines) + "\n"

    for index, step in enumerate(steps):
        action = str(step.get("action") or "").strip().lower()
        element = str(step.get("element") or "")
        value = str(step.get("value") or "")
        expected = str(step.get("expected") or "")
        if action == "navigate":
            if index > 0:
                lines.append("    page.goto(base_url)")
        elif action == "input":
            lines.append(f"    page.fill({element!r}, {value!r})")
        elif action == "click":
            lines.append(f"    page.click({element!r})")
        elif action == "assert":
            lines.append(f"    expect(page.locator({element!r})).to_have_text({(expected or value)!r})")
        else:
            lines.append(f"    # TODO: 未识别的动作 {action or '(空)'}（元素 {element or '-'}）")
        lines.append("")
    return "\n".join(lines) + "\n"


def _pi_agent_prefetch(job_dir: Path, start_url: str, storage_state: str) -> Dict[str, Any]:
    """用 Playwright 预取真实页面，落盘 site/index.html。

    pi-agent 的 inspect_dom 只读本地文件，所以 SPA / 需登录的页面必须在
    Runner 侧先渲染好再交给它 —— 这是「能修真实站点」的前提。
    """
    from playwright.sync_api import sync_playwright

    site_dir = job_dir / "site"
    site_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            ctx_kwargs: Dict[str, Any] = {"ignore_https_errors": True}
            if storage_state and Path(storage_state).is_file():
                ctx_kwargs["storage_state"] = storage_state
            context = browser.new_context(**ctx_kwargs)
            page = context.new_page()
            page.set_default_timeout(15000)
            page.goto(start_url, wait_until="load")
            final_url = page.url
            html = page.content()
            title = page.title()
            (site_dir / "index.html").write_text(html, encoding="utf-8")
            try:
                page.screenshot(path=str(site_dir / "page.png"), full_page=True)
            except Exception:
                pass
            context.close()
        finally:
            browser.close()

    return {"final_url": final_url, "title": title, "html_chars": len(html)}


def _pi_agent_worker(job_id: str, config: Dict[str, Any]):
    """串行闸门的持有者：无论成功失败，结束都要把 PI_AGENT_ACTIVE 释放掉。"""
    try:
        pi_agent_job(job_id, config)
    finally:
        with PI_AGENT_LOCK:
            if PI_AGENT_ACTIVE.get("job_id") == job_id:
                PI_AGENT_ACTIVE["job_id"] = None


def pi_agent_job(job_id: str, config: Dict[str, Any]):
    """pi-agent 自愈任务。

    配置：{"case": {...}, "start_url": ..., "storage_state": ..., "step_timeout_ms": 10000}
    流程：写 case.json → 预取真实页面 → 写 conftest.py → 生成测试骨架 →
          起 pi-agent 子进程 → 把 stdout 落到 pi-agent.log → 回收修复产物。
    """
    started_at = time.time()
    job_dir = ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir / "pi-agent.log"
    case = config.get("case") or {}
    start_url = str(config.get("start_url") or "").strip()
    storage_state = str(config.get("storage_state") or "").strip()
    default_timeout_ms = int(config.get("step_timeout_ms", 10000) or 10000)

    log_path.write_text("[pi-agent] 自愈任务启动\n", encoding="utf-8")
    _set_job(job_id, log_path=str(log_path), started_at=started_at)

    def log(text: str):
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")

    if not start_url:
        _set_job(job_id, status="failed", error="缺少 start_url", finished_at=time.time())
        log("[pi-agent] 缺少 start_url，终止")
        return
    if not case.get("steps"):
        _set_job(job_id, status="failed", error="case.steps 为空，没有可自愈的用例",
                 finished_at=time.time())
        log("[pi-agent] case.steps 为空，终止")
        return

    # 1. case.json：声明式步骤，是本次修复的预期基准
    (job_dir / "case.json").write_text(
        json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2. 预取真实页面 → site/index.html
    try:
        prefetch = _pi_agent_prefetch(job_dir, start_url, storage_state)
    except Exception as exc:
        log(f"[pi-agent] 页面预取失败：{exc}")
        _set_job(job_id, status="failed", error=f"页面预取失败：{exc}", finished_at=time.time())
        return
    log(f"[pi-agent] 页面预取完成：{prefetch['final_url']} — {prefetch['title']}"
        f"（{prefetch['html_chars']} 字符）")

    # 3. conftest.py
    (job_dir / "conftest.py").write_text(
        _PI_AGENT_CONFTEST.format(
            base_url=prefetch["final_url"],
            storage_state=storage_state if storage_state and Path(storage_state).is_file() else "",
            timeout_ms=default_timeout_ms,
        ),
        encoding="utf-8",
    )

    # 4. tests/test_web_case.py 骨架
    tests_dir = job_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_web_case.py").write_text(
        _render_skeleton(case, default_timeout_ms), encoding="utf-8")

    # 5. 起 pi-agent（TypeScript 工程，走本地 tsx 直跑，不预编译）
    cli = PI_AGENT_DIR / "node_modules" / "tsx" / "dist" / "cli.mjs"
    if not cli.is_file():
        message = (f"pi-agent 依赖缺失：{cli} 不存在。"
                   f"请在 {PI_AGENT_DIR} 执行 npm install（需要 Node ≥ 22.19）。")
        log(f"[pi-agent] {message}")
        _set_job(job_id, status="failed", error=message, finished_at=time.time())
        return

    command = [PI_AGENT_NODE, str(cli), "src/cli.ts", "selfheal", "--workdir", str(job_dir)]
    log(f"[pi-agent] 启动：{' '.join(command)}")
    log("─" * 64)
    try:
        process = subprocess.Popen(
            command, cwd=str(PI_AGENT_DIR), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        )
    except Exception as exc:
        log(f"[pi-agent] 无法启动：{exc}")
        _set_job(job_id, status="failed", error=f"无法启动 pi-agent：{exc}", finished_at=time.time())
        return

    with PI_AGENT_LOCK:
        PI_AGENT_PROCESSES[job_id] = process

    # 看门狗：模型静默空转是已知失败模式，超时直接整棵树结束，避免任务永久挂住
    watchdog = threading.Timer(PI_AGENT_TIMEOUT_SEC, lambda: _terminate_process_tree(process))
    watchdog.daemon = True
    watchdog.start()

    try:
        with log_path.open("a", encoding="utf-8") as handle:
            for line in process.stdout or []:
                handle.write(_ANSI_RE.sub("", line))
                handle.flush()
        code = process.wait()
    finally:
        watchdog.cancel()
        with PI_AGENT_LOCK:
            PI_AGENT_PROCESSES.pop(job_id, None)

    stopped = bool(JOBS.get(job_id, {}).get("stopped"))
    fixed_path = tests_dir / "test_web_case.py"
    memory_path = PI_AGENT_DIR / ".pi-agent" / "memory.md"
    result = {
        "exit_code": code,
        "stopped": stopped,
        "final_url": prefetch["final_url"],
        "title": prefetch["title"],
        "workdir": str(job_dir),
        "fixed_test": fixed_path.read_text(encoding="utf-8") if fixed_path.is_file() else "",
        "memory": memory_path.read_text(encoding="utf-8") if memory_path.is_file() else "",
    }
    log("─" * 64)
    log(f"[pi-agent] 结束：exit_code={code}{'（人工终止）' if stopped else ''}")
    if stopped:
        _set_job(job_id, status="cancelled", exit_code=code, result=result, finished_at=time.time())
    elif code == 0:
        _set_job(job_id, status="completed", exit_code=code, result=result, finished_at=time.time())
    else:
        reason = _pi_agent_failure_reason(log_path)
        _set_job(job_id, status="failed", exit_code=code, result=result,
                 error=reason or f"pi-agent 退出码 {code}，详见 pi-agent.log", finished_at=time.time())


# pi-agent 失败时会在日志里打一行 [自愈失败]，作为给页面直接展示的原因
_PI_AGENT_FAIL_RE = re.compile(r"\[自愈失败\]\s*(.+)")


def _pi_agent_failure_reason(log_path: Path) -> str:
    """从 pi-agent 日志尾部提取失败原因。

    失败后页面就不再刷新日志了，只回传「退出码」用户无法判断该怎么处理，
    所以把 CLI 打出的 [自愈失败] 摘要行带回去。
    """
    match = _PI_AGENT_FAIL_RE.search(_tail_text(log_path, 4000))
    return match.group(1).strip() if match else ""


def _tail_text(path: Path, limit: int = 2000) -> str:
    """读取文本文件尾部，用于把 codegen 的原始报错带回给前端。"""
    try:
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[-limit:].strip()
    except OSError:
        return ""


# codegen 页面加载失败时的输出形如：[PlaywrightError: net::ERR_CERT_AUTHORITY_INVALID at https://x/
_CODEGEN_ERROR_RE = re.compile(r"([A-Za-z]*Error):\s*([^\n\]]+)")


def _codegen_error(path: Path) -> str:
    """从 codegen 输出里提取页面加载类的失败原因，例如 net::ERR_CERT_AUTHORITY_INVALID。"""
    match = _CODEGEN_ERROR_RE.search(_tail_text(path, 4000))
    if not match:
        return ""
    return f"{match.group(1)}: {match.group(2).strip()}"


def _terminate_process_tree(process: subprocess.Popen):
    """连同子进程一起结束录制进程。

    codegen 是 python -> node 驱动 -> 浏览器 的三层进程，只 terminate 最上层
    会留下孤儿浏览器窗口挂在用户桌面上，所以 Windows 下用 taskkill /T 整棵树结束。
    """
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, timeout=20)
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def record_job(job_id: str, config: Dict[str, Any]):
    """用 playwright codegen 录制真实操作，结束后把脚本转成 steps。

    配置：{"url": "https://..."}

    录制必须有头运行（codegen 不支持无头），所以它只能在有桌面会话的本机使用，
    不能放进无人值守的 CI。结束方式是关闭 codegen 打开的浏览器窗口；只有正常
    关闭时 codegen 才会写出脚本，因此中途 terminate 会拿不到录制内容。
    """
    started_at = time.time()
    url = str(config.get("url") or "").strip()
    workdir = ROOT / job_id
    log_path = workdir / "record.log"
    codegen_log_path = workdir / "codegen.log"
    script_path = workdir / "recorded.py"
    workdir.mkdir(parents=True, exist_ok=True)

    def log(text: str):
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")

    def fail(message: str):
        detail = _tail_text(codegen_log_path)
        if detail:
            message = f"{message}\ncodegen 输出：{detail}"
        log(f"[record] 失败：{message}")
        _set_job(job_id, status="failed", log_path=str(log_path),
                 error=message, finished_at=time.time())

    _set_job(job_id, log_path=str(log_path))
    if not url:
        fail("录制缺少起始 URL")
        return

    command = [
        sys.executable, "-m", "playwright", "codegen",
        f"--output={script_path}", "--target=python",
        # 内网测试系统大量使用自签证书，不忽略 https 错误会导致页面打不开、录制直接退出
        "--ignore-https-errors", url,
    ]
    log(f"[record] 启动 codegen：{' '.join(command)}")

    timed_out = False
    try:
        with codegen_log_path.open("ab") as output:
            process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
            with RECORDING_LOCK:
                RECORDING["process"] = process
            log("[record] 已打开浏览器，请在窗口中操作；关闭窗口即结束录制")
            try:
                process.wait(timeout=RECORD_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                timed_out = True
                log(f"[record] 超过 {RECORD_TIMEOUT_SEC} 秒未结束，自动终止录制")
                # 必须结束整棵进程树，否则 node 驱动和浏览器窗口会变成孤儿进程留在桌面上
                _terminate_process_tree(process)
    except Exception as exc:
        fail(f"启动录制失败：{exc}")
        return
    finally:
        with RECORDING_LOCK:
            RECORDING.clear()

    if not script_path.is_file():
        reason = "录制超时，已自动结束" if timed_out else "录制被终止或异常退出，codegen 没有写出脚本"
        fail(f"{reason}。codegen 只在浏览器窗口被正常关闭时保存脚本，请重新录制并关闭窗口结束。")
        return

    try:
        steps, warnings = parse_recorded_script(script_path)
    except Exception as exc:
        fail(f"解析录制脚本失败：{exc}")
        return

    # codegen 打不开页面时会异常退出，但仍会留下一个只有骨架的脚本文件；
    # 必须按失败上报，否则前端会显示成「录制完成、0 个动作」，把真实原因藏起来。
    codegen_error = _codegen_error(codegen_log_path)
    if codegen_error and not steps:
        fail(f"起始页面没有打开成功，录制已中断（{codegen_error}），请确认 URL 可访问")
        return
    if codegen_error:
        warnings.append(f"codegen 中途报错（{codegen_error}），录到的动作可能不完整")

    for item in warnings:
        log(f"[record][warn] {item}")
    log(f"[record] 完成：识别 {len(steps)} 步，{len(warnings)} 处需要人工确认，"
        f"耗时 {round(time.time() - started_at, 2)}s")
    _set_job(
        job_id, status="completed", log_path=str(log_path), finished_at=time.time(),
        result={
            "steps": steps,
            "warnings": warnings,
            "recorded_script": str(script_path),
            "start_url": url,
        },
    )


def pytest_job(job_id: str, config: Dict[str, Any]):
    try:
        test_dir = Path(str(config.get("test_dir", ""))).expanduser().resolve()
        if not test_dir.exists() or not test_dir.is_dir():
            raise ValueError("test_dir 不存在或不是目录")
        if not _allowed_path(test_dir):
            raise ValueError("test_dir 不在 Runner 允许的工作区内")
        report_path = ROOT / job_id / "pytest.log"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "pytest", str(test_dir), "-v"]
        if config.get("marker"):
            command.extend(["-m", str(config["marker"])])
        if config.get("module"):
            command.append(str(config["module"]))
        with report_path.open("w", encoding="utf-8") as output:
            process = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, timeout=3600)
        _set_job(job_id, status="completed" if process.returncode == 0 else "failed",
                 exit_code=process.returncode, report_path=str(report_path), finished_at=time.time())
    except Exception as exc:
        _set_job(job_id, status="failed", error=str(exc), finished_at=time.time())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[runner] {self.address_string()} - {fmt % args}")

    def do_GET(self):
        if not _authorized(self):
            _json_response(self, 401, {"detail": "Runner token 无效"})
            return
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/health":
            _json_response(self, 200, {"status": "ok", "runner": "android-appium", "workspace": str(ROOT)})
            return
        if path == "/record":
            with RECORDING_LOCK:
                active_job = RECORDING.get("job_id")
            with JOBS_LOCK:
                job = dict(JOBS.get(active_job, {})) if active_job else {}
            payload_out = {"job_id": active_job}
            payload_out.update(job)
            _json_response(self, 200, payload_out)
            return
        match = re.fullmatch(r"/jobs/([A-Za-z0-9_-]+)", path)
        if match:
            with JOBS_LOCK:
                job = dict(JOBS.get(match.group(1), {}))
            if not job:
                _json_response(self, 404, {"detail": "任务不存在"})
            else:
                _json_response(self, 200, {"job_id": match.group(1), **job})
            return
        _json_response(self, 404, {"detail": "路径不存在"})

    def do_POST(self):
        if not _authorized(self):
            _json_response(self, 401, {"detail": "Runner token 无效"})
            return
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            payload = _read_json(self)
            if path == "/preflight":
                _json_response(self, 200, preflight(payload))
                return
            if path in {"/explore", "/run"}:
                job_id = str(payload.get("job_id") or uuid.uuid4())[:32]
                with JOBS_LOCK:
                    JOBS[job_id] = {"status": "running", "created_at": time.time()}
                worker = threading.Thread(
                    target=explore_job if path == "/explore" else pytest_job,
                    args=(job_id, payload), daemon=True,
                )
                worker.start()
                _json_response(self, 202, {"job_id": job_id, "status": "running"})
                return
            if path == "/pi-agent":
                with PI_AGENT_LOCK:
                    busy_job = PI_AGENT_ACTIVE.get("job_id")
                if busy_job:
                    _json_response(self, 409, {
                        "detail": f"已有自愈任务在运行（{busy_job}），请先等待结束或终止它",
                    })
                    return
                job_id = str(payload.get("job_id") or uuid.uuid4())[:32]
                with JOBS_LOCK:
                    JOBS[job_id] = {"status": "running", "created_at": time.time()}
                with PI_AGENT_LOCK:
                    PI_AGENT_ACTIVE["job_id"] = job_id
                threading.Thread(target=_pi_agent_worker, args=(job_id, payload), daemon=True).start()
                _json_response(self, 202, {"job_id": job_id, "status": "running"})
                return
            pi_stop = re.fullmatch(r"/pi-agent/([A-Za-z0-9_-]+)/stop", path)
            if pi_stop:
                job_id = pi_stop.group(1)
                with PI_AGENT_LOCK:
                    process = PI_AGENT_PROCESSES.get(job_id)
                if process is None:
                    _json_response(self, 404, {"detail": "该自愈任务不存在或已经结束"})
                    return
                _set_job(job_id, stopped=True, status="stopping")
                try:
                    # node → tsx → python → 浏览器 是多层进程，必须整棵树结束
                    _terminate_process_tree(process)
                except Exception as exc:
                    _json_response(self, 400, {"detail": f"终止自愈任务失败：{exc}"})
                    return
                _json_response(self, 200, {"job_id": job_id, "status": "stopping"})
                return
            if path == "/web-run":
                job_id = str(payload.get("job_id") or uuid.uuid4())[:32]
                with JOBS_LOCK:
                    JOBS[job_id] = {"status": "running", "created_at": time.time()}
                threading.Thread(target=web_run_job, args=(job_id, payload), daemon=True).start()
                _json_response(self, 202, {"job_id": job_id, "status": "running"})
                return
            if path == "/web-explore":
                job_id = str(payload.get("job_id") or uuid.uuid4())[:32]
                with JOBS_LOCK:
                    JOBS[job_id] = {
                        "status": "running", "created_at": time.time(), "stop_requested": False,
                    }
                threading.Thread(target=web_explore_job, args=(job_id, payload), daemon=True).start()
                _json_response(self, 202, {"job_id": job_id, "status": "running"})
                return
            web_explore_stop = re.fullmatch(r"/web-explore/([A-Za-z0-9_-]+)/stop", path)
            if web_explore_stop:
                job_id = web_explore_stop.group(1)
                with JOBS_LOCK:
                    job = dict(JOBS.get(job_id, {}))
                if not job:
                    _json_response(self, 404, {"detail": "探索任务不存在或已经结束"})
                    return
                if job.get("status") in {"completed", "stopped", "failed"}:
                    _json_response(self, 200, {"job_id": job_id, "status": job.get("status")})
                    return
                _set_job(job_id, stop_requested=True, status="stopping")
                _json_response(self, 200, {"job_id": job_id, "status": "stopping"})
                return
            if path == "/web-login":
                job_id = str(payload.get("job_id") or uuid.uuid4())[:32]
                with JOBS_LOCK:
                    JOBS[job_id] = {"status": "login_waiting", "created_at": time.time()}
                threading.Thread(target=web_login_job, args=(job_id, payload), daemon=True).start()
                _json_response(self, 202, {"job_id": job_id, "status": "login_waiting"})
                return
            login_save = re.fullmatch(r"/web-login/([A-Za-z0-9_-]+)/save", path)
            if login_save:
                job_id = login_save.group(1)
                with LOGIN_LOCK:
                    session = LOGIN_SESSIONS.get(job_id)
                if not session:
                    _json_response(self, 404, {"detail": "预登录会话不存在或已结束"})
                    return
                session["save_event"].set()
                _json_response(self, 200, {"job_id": job_id, "status": "saving"})
                return
            login_cancel = re.fullmatch(r"/web-login/([A-Za-z0-9_-]+)/cancel", path)
            if login_cancel:
                job_id = login_cancel.group(1)
                with LOGIN_LOCK:
                    session = LOGIN_SESSIONS.get(job_id)
                if not session:
                    _json_response(self, 404, {"detail": "预登录会话不存在或已结束"})
                    return
                session["cancel_event"].set()
                _json_response(self, 200, {"job_id": job_id, "status": "cancelling"})
                return
            if path == "/record":
                with RECORDING_LOCK:
                    busy_job = RECORDING.get("job_id")
                if busy_job:
                    _json_response(self, 409, {"detail": f"已有录制在进行中（{busy_job}），请先结束当前录制"})
                    return
                job_id = str(payload.get("job_id") or uuid.uuid4())[:32]
                with JOBS_LOCK:
                    JOBS[job_id] = {"status": "recording", "created_at": time.time()}
                with RECORDING_LOCK:
                    RECORDING["job_id"] = job_id
                threading.Thread(target=record_job, args=(job_id, payload), daemon=True).start()
                _json_response(self, 202, {"job_id": job_id, "status": "recording"})
                return
            match = re.fullmatch(r"/record/([A-Za-z0-9_-]+)/stop", path)
            if match:
                job_id = match.group(1)
                with RECORDING_LOCK:
                    process = RECORDING.get("process") if RECORDING.get("job_id") == job_id else None
                if process is None:
                    _json_response(self, 404, {"detail": "该录制不存在或已经结束"})
                    return
                try:
                    # 同样要连子进程一起结束，只 terminate 最上层会残留孤儿浏览器窗口
                    _terminate_process_tree(process)
                except Exception as exc:
                    _json_response(self, 400, {"detail": f"终止录制失败：{exc}"})
                    return
                _json_response(self, 200, {"job_id": job_id, "status": "stopping"})
                return
            _json_response(self, 404, {"detail": "路径不存在"})
        except Exception as exc:
            _json_response(self, 400, {"detail": str(exc)})

    def do_DELETE(self):
        if not _authorized(self):
            _json_response(self, 401, {"detail": "Runner token 无效"})
            return
        path = urlparse(self.path).path.rstrip("/") or "/"
        match = re.fullmatch(r"/jobs/([A-Za-z0-9_-]+)", path)
        if not match:
            _json_response(self, 404, {"detail": "路径不存在"})
            return

        job_id = match.group(1)
        with JOBS_LOCK:
            job = dict(JOBS.get(job_id, {}))
        if job.get("status") in {"starting", "running", "recording"}:
            _json_response(self, 409, {"detail": "任务仍在运行，完成后才能删除"})
            return

        # job_id 已通过正则限制为单层目录名，并再次确认目标是 ROOT 的直接子目录。
        output_dir = (ROOT / job_id).resolve()
        if output_dir.parent != ROOT:
            _json_response(self, 400, {"detail": "非法任务目录"})
            return
        if not job and not output_dir.exists():
            _json_response(self, 404, {"detail": "任务不存在"})
            return
        if output_dir.exists():
            shutil.rmtree(output_dir)
        with JOBS_LOCK:
            JOBS.pop(job_id, None)
        _json_response(self, 200, {"job_id": job_id, "message": "Runner 探索结果已删除"})


def main():
    import argparse

    parser = argparse.ArgumentParser(description="LYAITEST Android Appium 本地 Runner")
    parser.add_argument("--host", default=os.getenv("RUNNER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("RUNNER_PORT", "8765")))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"LYAITEST Android Runner listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
