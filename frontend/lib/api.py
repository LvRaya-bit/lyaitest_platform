"""API 层：封装与后端交互的最小单元。

包含：
- API_BASE / get_headers：读取配置与会话 token
- _cached_get / fetch_data / call_api：HTTP 调用（含 30s 缓存、401 自动重登）
- clear_user_cache：清理本机会话状态与缓存（转发到 frontend.lib.state）

注意：
1) `_cached_get` 用 `@st.cache_data`，必须由 Streamlit 主线程调用，
   子线程会丢失 ScriptRunContext 导致 401 与缓存失效（已回退过一次）。
2) 写操作（POST/PUT/DELETE/PATCH）成功后必须 `_cached_get.clear()`，
   否则列表页会继续显示旧数据。
3) `clear_user_cache` 的实现已转到 frontend.lib.state.clear_user_selections，
   本函数仅转发以保持调用方零改动。
4) 身份读取（token）统一走 frontend.lib.state，避免散落的 session_state 访问。
"""

import os

import requests
import streamlit as st

from frontend.lib import state


def _resolve_api_base() -> str:
    """从 st.secrets 或环境变量读取后端 base url，缺失时回退到本地默认值。"""
    try:
        return st.secrets.get("api_base", os.getenv("API_BASE", "http://localhost:8001/api/v1"))
    except Exception:
        return os.getenv("API_BASE", "http://localhost:8001/api/v1")


API_BASE = _resolve_api_base()


def get_headers():
    """读取当前会话 token 生成 Authorization 头；未登录返回空 dict。"""
    tok = state.token()
    if tok:
        return {"Authorization": f"Bearer {tok}"}
    return {}


def _api_detail(response) -> str:
    """从 requests 响应提取错误 detail 文本，供 st.error 展示。

    被多个页面（web/api_test/report/case/pi）共用，故放公共 api 层。
    """
    try:
        return response.json().get("detail", "") or response.text
    except Exception:
        return response.text


@st.cache_data(ttl=30, show_spinner=False)
def _cached_get(url, token):
    """纯 HTTP GET，带 30s TTL 缓存。仅缓存 200 成功响应；非 200 抛异常避免缓存错误。"""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.get(url, headers=headers, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.json()


def fetch_data(endpoint):
    """GET 请求并返回 JSON；遇 401 清会话并 rerun，其它异常返回空 list。"""
    tok = state.token()
    try:
        return _cached_get(f"{API_BASE}{endpoint}", tok)
    except RuntimeError as e:
        if "401" in str(e):
            state.logout()
            st.rerun()
        return []
    except Exception:
        return []


def call_api(method, endpoint, **kwargs):
    """通用 HTTP 请求；401 清会话重登；写操作成功后清 GET 缓存。"""
    try:
        r = requests.request(method, f"{API_BASE}{endpoint}", headers=get_headers(), **kwargs)
        if r.status_code == 401:
            state.logout()
            st.rerun()
        # 任何写操作成功后清空 GET 缓存，避免列表显示旧数据
        if method.upper() in ("POST", "PUT", "DELETE", "PATCH") and r.status_code < 400:
            _cached_get.clear()
        return r
    except Exception as e:
        st.error(f"请求失败: {str(e)}")
        return None


def clear_user_cache():
    """清空 selected_* 等 UI 选择状态；不动 token，避免误退出登录。

    实现已转到 frontend.lib.state.clear_user_selections，这里转发以保持调用方零改动。
    """
    state.clear_user_selections()
