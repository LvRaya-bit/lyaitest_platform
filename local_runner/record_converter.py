"""把 playwright codegen 录制的 Python 脚本转换成平台 steps 数组。

平台里 steps 的 JSON 数组是唯一真相，录制脚本只是它的一条来源；
所以这里只做单向转换（Python 源码 → steps），不反向生成脚本。

识别范围与 Runner 的执行能力严格对齐（Playwright 1.61 codegen 输出格式）：

    page.goto("url")                        → navigate
    <locator>.click()                       → click
    <locator>.fill("value")                 → input
    expect(<locator>).to_contain_text("v")  → assert（element=locator）
    expect(<locator>).to_have_text("v")     → assert（element=locator）
    expect(page).to_have_url("v")           → assert（element=url）
    expect(page).to_have_title("v")         → assert（element=title）
    expect(<locator>).to_be_visible()       → wait（等待元素可见）
    page.wait_for_timeout(1000)             → wait（等待毫秒数）

其余动作（press / hover / select_option / check / .nth() / get_by_label() 等）
不静默丢弃，统一收进 warnings 交给前端明确提示，避免出现"录了却没生效"的幻觉。
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PAGE_ONLY_METHODS = {"goto", "wait_for_timeout"}
_LOCATOR_RELATIVE_METHODS = {"nth", "first", "last"}


def _const_str(node: Any) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _const_int(node: Any) -> Optional[int]:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    return None


def _chain_root(node: Any) -> Optional[str]:
    """一路向下找到调用链最内层的变量名，例如 page.locator("#a").click() → "page"。"""
    current = node
    while True:
        if isinstance(current, ast.Call):
            current = current.func
        elif isinstance(current, ast.Attribute):
            current = current.value
        elif isinstance(current, ast.Name):
            return current.id
        else:
            return None


def _is_page(node: Any) -> bool:
    return isinstance(node, ast.Name) and node.id == "page"


def _keyword_str(call: ast.Call, name: str) -> Optional[str]:
    for keyword in call.keywords:
        if keyword.arg == name:
            return _const_str(keyword.value)
    return None


def _locator_from_call(call: ast.Call) -> Tuple[Optional[str], Optional[str]]:
    """把 codegen 生成的定位器表达式转成平台 element 字符串。

    返回 (element, 不支持原因)，两者必有且仅有一个为 None。
    """
    if not isinstance(call.func, ast.Attribute):
        return None, "不是可识别的定位器表达式"
    method = call.func.attr

    if method in _LOCATOR_RELATIVE_METHODS:
        return None, f"暂不支持在定位器上使用 .{method}()，请人工确认该元素"
    if isinstance(call.func.value, ast.Call):
        return None, "暂不支持链式嵌套定位器，请人工确认该元素"
    if not _is_page(call.func.value):
        return None, "不是 page 上的定位器调用"

    if method == "locator":
        selector = _const_str(call.args[0]) if call.args else None
        if selector is None:
            return None, "locator() 使用了非字面量选择器"
        return selector, None

    if method == "get_by_role":
        role = _const_str(call.args[0]) if call.args else None
        if not role:
            return None, "get_by_role() 缺少角色字面量"
        name = _keyword_str(call, "name")
        if name is None:
            return f"role={role}", None
        return f'role={role}[name="{name}"]', None

    if method == "get_by_text":
        text = _const_str(call.args[0]) if call.args else None
        if text is None:
            return None, "get_by_text() 使用了非字面量文本"
        return f"text={text}", None

    if method == "get_by_test_id":
        test_id = _const_str(call.args[0]) if call.args else None
        if test_id is None:
            return None, "get_by_test_id() 使用了非字面量取值"
        return f"testid={test_id}", None

    return None, f"暂不支持的定位器 {method}()，请人工换成 CSS 或 role 定位"


def _is_expect_call(node: Any) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "expect"
    )


def _step_from_expect(call: ast.Call) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """处理 expect(...).to_xxx(...) 断言族（codegen 断言按钮的产物）。"""
    inner = call.func.value
    if not inner.args:
        return None, "expect() 缺少断言目标"

    matcher = call.func.attr
    target = inner.args[0]
    expected = _const_str(call.args[0]) if call.args else None

    if matcher == "to_be_visible":
        element, reason = _locator_from_call(target)
        if element is None:
            return None, reason
        return {"action": "wait", "element": element, "value": "", "expected": ""}, None

    if matcher in {"to_have_url", "to_have_title"}:
        if expected is None:
            return None, f"{matcher}() 使用了非字面量期望值"
        element = "url" if matcher == "to_have_url" else "title"
        return {"action": "assert", "element": element, "value": "", "expected": expected}, None

    if matcher in {"to_contain_text", "to_have_text"}:
        element, reason = _locator_from_call(target)
        if element is None:
            return None, reason
        if expected is None:
            return None, f"{matcher}() 使用了非字面量期望值"
        return {"action": "assert", "element": element, "value": "", "expected": expected}, None

    return None, f"暂不支持的断言 {matcher}()，已跳过"


def _step_from_statement(node: ast.stmt) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return None, None
    call = node.value

    if isinstance(call.func, ast.Name) and call.func.id == "expect":
        return None, "expect() 缺少断言方法调用（如 to_contain_text）"
    if not isinstance(call.func, ast.Attribute):
        return None, None

    if _is_expect_call(call.func.value):
        return _step_from_expect(call)

    action = call.func.attr
    owner = call.func.value

    if action == "goto" and _is_page(owner):
        url = _const_str(call.args[0]) if call.args else None
        if url is None:
            return None, "goto() 使用了非字面量 URL"
        return {"action": "navigate", "element": url, "value": "", "expected": ""}, None

    if action == "wait_for_timeout" and _is_page(owner):
        milliseconds = _const_int(call.args[0]) if call.args else None
        if milliseconds is None:
            return None, "wait_for_timeout() 使用了非字面量毫秒数"
        return {"action": "wait", "element": str(milliseconds), "value": "", "expected": ""}, None

    if action in {"click", "fill"} and isinstance(owner, ast.Call):
        element, reason = _locator_from_call(owner)
        if element is None:
            return None, reason
        if action == "click":
            return {"action": "click", "element": element, "value": "", "expected": ""}, None
        value = _const_str(call.args[0]) if call.args else ""
        if value is None:
            return None, "fill() 使用了非字面量输入值"
        return {"action": "input", "element": element, "value": value, "expected": ""}, None

    if _chain_root(call) == "page" and action not in _PAGE_ONLY_METHODS:
        return None, f"暂不支持的动作 {action}()，已跳过"
    return None, None


def parse_recorded_script(path: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    """解析 codegen 产物，返回 (steps, warnings)。

    脚本里只有 `run()` 函数体是业务动作，按源码行号排序即可还原操作顺序。
    """
    source = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(source)

    statements = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    ]
    steps: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for node in sorted(statements, key=lambda item: item.lineno):
        call = node.value
        # codegen 收尾的 page.close() / context.close() 不是业务动作
        if isinstance(call.func, ast.Attribute) and call.func.attr == "close":
            continue
        step, reason = _step_from_statement(node)
        if step:
            steps.append(step)
        elif reason:
            warnings.append(f"第 {node.lineno} 行：{reason}")
    return steps, warnings