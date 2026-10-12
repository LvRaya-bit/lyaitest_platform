"""
LYAITEST Agent 核心模块
基于 LangGraph 构建，支持意图识别 + 工具调用，打通知识库/接口测试/Web自动化/报告
"""

from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional
from langchain_openai import ChatOpenAI
import os
import time
import requests
import json
from dotenv import load_dotenv
from app.database import get_connection
from app.services.report_service import save_report
from app.services.local_runner_client import RunnerUnavailable
from app.services.web_run_service import (
    collect_web_run,
    create_running_report,
    submit_web_run,
)
import uuid
from datetime import datetime

load_dotenv()

llm = ChatOpenAI(
    model="glm-4",
    openai_api_key=os.getenv("ZHIPU_API_KEY"),
    openai_api_base="https://open.bigmodel.cn/api/paas/v4/",
    temperature=0.3
)


class AgentState(TypedDict):
    messages: list
    next_step: str
    session_id: str
    knowledge_base_id: Optional[str]
    interface_id: Optional[str]
    web_case_id: Optional[str]
    user_id: Optional[str]


# ============================================
# 知识库检索工具
# ============================================
def search_knowledge_base(kb_id: str, query: str, max_docs: int = 3, user_id: str = None) -> str:
    if not kb_id or not user_id:
        return ""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM documents WHERE knowledge_base_id = ? AND user_id = ?", (kb_id, user_id))
    docs = cursor.fetchall()
    conn.close()
    if not docs:
        return ""
    context_parts = []
    for doc in docs[:max_docs]:
        doc_dict = dict(doc)
        if doc_dict.get("content"):
            context_parts.append(f"【文档: {doc_dict['filename']}】\n{doc_dict['content'][:2000]}")
    return "\n\n".join(context_parts)


# ============================================
# 节点1：意图识别
# ============================================
def identify_intent(state: AgentState):
    user_message = state["messages"][-1]["content"]

    # 如果前端传了 interface_id 或 web_case_id，直接走对应执行节点
    if state.get("interface_id"):
        state["next_step"] = "run_interface"
        return state
    if state.get("web_case_id"):
        state["next_step"] = "run_web_case"
        return state

    user_id = state.get("user_id")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM api_interfaces WHERE user_id = ?", (user_id,))
    interfaces = cursor.fetchall()
    cursor.execute("SELECT id, name FROM web_cases WHERE user_id = ?", (user_id,))
    cases = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) FROM reports WHERE user_id = ?", (user_id,))
    report_count = cursor.fetchone()[0]
    conn.close()

    if_list = ", ".join([f"{i['name']}" for i in interfaces]) or "无"
    case_list = ", ".join([f"{c['name']}" for c in cases]) or "无"

    prompt = f"""
你是LYAITEST测试平台的AI助手。判断用户意图，只输出以下选项之一，不要输出其他内容：

- generate_case: 生成测试用例、生成自动化步骤
- run_api_test: 执行接口测试、测试API、运行接口
- run_web_test: 执行Web自动化、运行用例、UI自动化
- query_report: 查询测试报告、查看执行结果
- chat: 普通对话

平台已有资源：
- 接口: {if_list}
- Web用例: {case_list}
- 测试报告数: {report_count}

用户说：{user_message}

只输出一个选项：
"""
    try:
        response = llm.invoke(prompt)
        intent = response.content.strip().lower()
        if "generate_case" in intent:
            state["next_step"] = "generate_case"
        elif "run_api_test" in intent or "api" in intent or "接口" in intent:
            state["next_step"] = "run_api_test"
        elif "run_web_test" in intent or "web" in intent or "浏览器" in intent or "自动化" in intent:
            state["next_step"] = "run_web_test"
        elif "query_report" in intent or "报告" in intent or "结果" in intent:
            state["next_step"] = "query_report"
        else:
            state["next_step"] = "chat"
    except:
        state["next_step"] = "chat"
    return state


# ============================================
# 节点2：生成测试用例（联动知识库）
# ============================================
def generate_case(state: AgentState):
    user_message = state["messages"][-1]["content"]
    kb_id = state.get("knowledge_base_id")
    user_id = state.get("user_id")
    kb_context = search_knowledge_base(kb_id, user_message, user_id=user_id) if kb_id else ""

    system_prompt = "你是LYAITEST平台的测试专家。"
    if kb_context:
        system_prompt += f"\n以下是项目知识库中的业务资料，请基于这些内容生成测试用例：\n{kb_context}"
    else:
        system_prompt += "\n当前未绑定知识库，请根据用户描述通用生成。"

    # 获取已录入的接口和Web用例作为参考
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, url, method FROM api_interfaces WHERE user_id = ?", (user_id,))
    interfaces = cursor.fetchall()
    cursor.execute("SELECT name, steps FROM web_cases WHERE user_id = ?", (user_id,))
    cases = cursor.fetchall()
    conn.close()

    ref_info = ""
    if interfaces:
        ref_info += "\n已录入的接口：\n" + "\n".join([f"  - {i['name']} [{i['method']}] {i['url']}" for i in interfaces])
    if cases:
        ref_info += "\n已录入的Web用例：\n" + "\n".join([f"  - {c['name']}" for c in cases])

    full_prompt = f"""{system_prompt}{ref_info}

用户请求：{user_message}

请输出结构化的测试用例，包含：
1. 用例名称
2. 前置条件
3. 测试步骤（编号）
4. 预期结果

如果是接口测试用例，请包含请求方法、URL、参数。
如果是Web自动化用例，请用 navigate/click/input/assert/wait 描述每个步骤。
"""
    try:
        response = llm.invoke(full_prompt)
        reply = response.content
    except Exception as e:
        reply = f"生成失败: {e}"

    state["messages"].append({"role": "assistant", "content": reply})
    state["next_step"] = "end"
    return state


# ============================================
# 节点3：执行单个接口（前端选择器触发）
# ============================================
def run_interface(state: AgentState):
    interface_id = state.get("interface_id")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM api_interfaces WHERE id = ? AND user_id = ?", (interface_id, state.get("user_id")))
    row = cursor.fetchone()
    if not row:
        conn.close()
        state["messages"].append({"role": "assistant", "content": "⚠️ 未找到选中的接口。"})
        state["next_step"] = "end"
        return state

    interface = dict(row)
    headers = json.loads(interface.get("headers", "{}"))
    params = json.loads(interface.get("params", "{}"))
    body = json.loads(interface.get("body", "{}"))

    try:
        response = requests.request(
            method=interface["method"],
            url=interface["url"],
            headers=headers,
            params=params,
            json=body,
            timeout=30
        )
        passed = response.status_code < 400

        report_id = str(uuid.uuid4())[:8]
        save_report({
            "id": report_id,
            "user_id": state.get("user_id", "unknown"),
            "session_id": state.get("session_id", "unknown"),
            "test_type": "api",
            "test_name": interface["name"],
            "url": interface["url"],
            "status_code": response.status_code,
            "response_time": response.elapsed.total_seconds(),
            "status": "completed",
            "total_cases": 1,
            "passed_cases": 1 if passed else 0,
            "failed_cases": 0 if passed else 1,
        })

        try:
            resp_body = response.json()
            resp_preview = json.dumps(resp_body, ensure_ascii=False, indent=2)[:500]
        except:
            resp_preview = response.text[:500]

        reply = f"""🧪 接口执行完成
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 接口: {interface['name']}
📌 方法: {interface['method']}
📍 URL: {interface['url']}
📊 状态码: {response.status_code}
{'✅ 通过' if passed else '❌ 失败'}
⏱️ 响应时间: {response.elapsed.total_seconds():.2f}s
📄 报告ID: {report_id}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
响应预览:
{resp_preview}"""
    except Exception as e:
        report_id = str(uuid.uuid4())[:8]
        save_report({
            "id": report_id,
            "user_id": state.get("user_id", "unknown"),
            "session_id": state.get("session_id", "unknown"),
            "test_type": "api",
            "test_name": interface["name"],
            "url": interface["url"],
            "status": "completed",
            "total_cases": 1,
            "passed_cases": 0,
            "failed_cases": 1,
            "error": str(e),
        })
        reply = f"❌ 接口执行失败: {e}\n📄 报告ID: {report_id}"

    conn.close()
    state["messages"].append({"role": "assistant", "content": reply})
    state["next_step"] = "end"
    return state


# ============================================
# 节点4：执行接口测试（自然语言触发，匹配已录入接口）
# ============================================
def run_api_test(state: AgentState):
    user_message = state["messages"][-1]["content"]
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM api_interfaces WHERE user_id = ? ORDER BY created_at DESC", (state.get("user_id"),))
    interfaces = cursor.fetchall()

    target = None
    for i in interfaces:
        i_dict = dict(i)
        if i_dict["name"] in user_message or i_dict["id"] in user_message:
            target = i_dict
            break
    if not target and interfaces:
        target = dict(interfaces[0])

    if not target:
        conn.close()
        state["messages"].append({
            "role": "assistant",
            "content": "⚠️ 平台中没有已录入的接口。请先在【接口测试】页面添加接口。"
        })
        state["next_step"] = "end"
        return state

    headers = json.loads(target.get("headers", "{}"))
    params = json.loads(target.get("params", "{}"))
    body = json.loads(target.get("body", "{}"))

    try:
        response = requests.request(
            method=target["method"], url=target["url"],
            headers=headers, params=params, json=body, timeout=30
        )
        passed = response.status_code < 400
        report_id = str(uuid.uuid4())[:8]
        save_report({
            "id": report_id, "user_id": state.get("user_id", "unknown"), "session_id": state.get("session_id", "unknown"),
            "test_type": "api", "test_name": target["name"], "url": target["url"],
            "status_code": response.status_code,
            "response_time": response.elapsed.total_seconds(),
            "status": "completed", "total_cases": 1,
            "passed_cases": 1 if passed else 0, "failed_cases": 0 if passed else 1,
        })
        reply = f"""🧪 接口执行完成
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 接口: {target['name']}
📌 方法: {target['method']}
📍 URL: {target['url']}
📊 状态码: {response.status_code}
{'✅ 通过' if passed else '❌ 失败'}
⏱️ 响应时间: {response.elapsed.total_seconds():.2f}s
📄 报告ID: {report_id}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
    except Exception as e:
        report_id = str(uuid.uuid4())[:8]
        save_report({
            "id": report_id, "user_id": state.get("user_id", "unknown"), "session_id": state.get("session_id", "unknown"),
            "test_type": "api", "test_name": target["name"], "url": target["url"],
            "status": "completed", "total_cases": 1, "passed_cases": 0,
            "failed_cases": 1, "error": str(e),
        })
        reply = f"❌ 接口执行失败: {e}\n📄 报告ID: {report_id}"

    conn.close()
    state["messages"].append({"role": "assistant", "content": reply})
    state["next_step"] = "end"
    return state


# ============================================
# 节点5：执行单个Web用例（前端选择器触发）
# 提交给本机 Runner 用 Playwright 打开浏览器真实执行，再轮询回读结果
# ============================================
_WEB_RUN_POLL_INTERVAL = 2
_WEB_RUN_POLL_TIMEOUT = 120


def _execute_web_steps(state, test_type, test_name, steps):
    """把用例提交给本机 Runner 真实执行，并轮询回读结果。

    返回 (passed_count, failed_count, logs, report_id)。Runner 不可用时
    report_id 为 None，failed_count 记为总步骤数，logs 里带失败原因。
    """
    user_id = state.get("user_id") or "unknown"
    report_id = str(uuid.uuid4())[:8]

    try:
        submit_web_run(report_id, [{"name": test_name, "steps": steps}])
    except RunnerUnavailable as exc:
        return 0, len(steps), [{"status": "failed", "message": f"无法提交执行：{exc}"}], None

    create_running_report(
        user_id, test_type, test_name, len(steps),
        session_id=state.get("session_id"), report_id=report_id,
    )

    deadline = time.time() + _WEB_RUN_POLL_TIMEOUT
    result = collect_web_run(report_id, user_id)
    while result.get("status") == "running" and time.time() < deadline:
        time.sleep(_WEB_RUN_POLL_INTERVAL)
        result = collect_web_run(report_id, user_id)

    logs = list(result.get("logs") or [])
    if result.get("status") == "running":
        logs.append({
            "status": "running",
            "message": f"执行超过 {_WEB_RUN_POLL_TIMEOUT} 秒仍未结束，可稍后在【测试报告】查看最终结果。",
        })

    return (
        int(result.get("passed_cases") or 0),
        int(result.get("failed_cases") or 0),
        logs,
        report_id,
    )


def run_web_case(state: AgentState):
    case_id = state.get("web_case_id")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM web_cases WHERE id = ? AND user_id = ?", (case_id, state.get("user_id")))
    row = cursor.fetchone()
    conn.close()
    if not row:
        state["messages"].append({"role": "assistant", "content": "⚠️ 未找到选中的Web用例。"})
        state["next_step"] = "end"
        return state

    case = dict(row)
    steps = json.loads(case.get("steps", "[]"))

    passed_count, failed_count, logs, report_id = _execute_web_steps(state, "web", case["name"], steps)

    reply = f"""🌐 Web自动化执行完成
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 用例: {case['name']}
📊 总步骤: {len(steps)}
✅ 通过: {passed_count}
❌ 失败: {failed_count}
📄 报告ID: {report_id or '未创建'}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
执行日志:
""" + "\n".join([f"  {l.get('message', '')}" for l in logs])

    state["messages"].append({"role": "assistant", "content": reply})
    state["next_step"] = "end"
    return state


# ============================================
# 节点6：执行Web测试（自然语言触发，匹配已录入用例）
# ============================================
def run_web_test(state: AgentState):
    user_message = state["messages"][-1]["content"]
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM web_cases WHERE user_id = ? ORDER BY created_at DESC", (state.get("user_id"),))
    cases = cursor.fetchall()

    target = None
    for c in cases:
        c_dict = dict(c)
        if c_dict["name"] in user_message or c_dict["id"] in user_message:
            target = c_dict
            break
    if not target and cases:
        target = dict(cases[0])
    conn.close()

    if not target:
        state["messages"].append({
            "role": "assistant",
            "content": "⚠️ 平台中没有已录入的Web用例。请先在【Web自动化】页面添加用例。"
        })
        state["next_step"] = "end"
        return state

    steps = json.loads(target.get("steps", "[]"))
    passed_count, failed_count, logs, report_id = _execute_web_steps(state, "web", target["name"], steps)

    reply = f"""🌐 Web自动化执行完成
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 用例: {target['name']}
📊 总步骤: {len(steps)}
✅ 通过: {passed_count}
❌ 失败: {failed_count}
📄 报告ID: {report_id or '未创建'}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
执行日志:
""" + "\n".join([f"  {l.get('message', '')}" for l in logs])

    state["messages"].append({"role": "assistant", "content": reply})
    state["next_step"] = "end"
    return state


# ============================================
# 节点7：查询测试报告
# ============================================
def query_report(state: AgentState):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reports WHERE user_id = ? ORDER BY created_at DESC LIMIT 10", (state.get("user_id"),))
    reports = cursor.fetchall()
    conn.close()

    if not reports:
        state["messages"].append({
            "role": "assistant",
            "content": "📊 当前没有任何测试报告。在对话中执行接口或Web用例后会自动生成报告。"
        })
        state["next_step"] = "end"
        return state

    total = len(reports)
    completed = len([r for r in reports if r["status"] == "completed"])
    total_passed = sum(r["passed_cases"] for r in reports)
    total_failed = sum(r["failed_cases"] for r in reports)
    total_cases = sum(r["total_cases"] for r in reports) or 1
    pass_rate = round((total_passed / total_cases) * 100, 1)

    lines = [f"""📊 测试报告汇总
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 总报告数: {total}
✅ 已完成: {completed}
📊 总用例: {total_cases}
✅ 通过: {total_passed}
❌ 失败: {total_failed}
📈 通过率: {pass_rate}%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
最近报告:"""]

    for r in reports[:5]:
        r_dict = dict(r)
        lines.append(
            f"  • [{r_dict['test_type']}] {r_dict.get('test_name','')} | "
            f"{r_dict['passed_cases']}/{r_dict['total_cases']}通过 | "
            f"{r_dict['status']} | {r_dict['created_at'][:19]}"
        )

    state["messages"].append({"role": "assistant", "content": "\n".join(lines)})
    state["next_step"] = "end"
    return state


# ============================================
# 节点8：普通聊天（联动知识库）
# ============================================
def chat(state: AgentState):
    user_message = state["messages"][-1]["content"]
    kb_id = state.get("knowledge_base_id")
    user_id = state.get("user_id")
    kb_context = search_knowledge_base(kb_id, user_message, user_id=user_id) if kb_id else ""

    system_prompt = "你是LYAITEST AI测试平台的助手，可以帮助用户进行测试相关工作。"
    if kb_context:
        system_prompt += f"\n\n以下是当前会话绑定的知识库内容，回答时请参考：\n{kb_context}"

    full_prompt = f"{system_prompt}\n\n用户: {user_message}"
    try:
        response = llm.invoke(full_prompt)
        reply = response.content
    except Exception as e:
        reply = f"抱歉，处理失败: {e}"

    state["messages"].append({"role": "assistant", "content": reply})
    state["next_step"] = "end"
    return state


# ============================================
# 构建图
# ============================================
def build_agent():
    workflow = StateGraph(AgentState)
    workflow.add_node("identify_intent", identify_intent)
    workflow.add_node("generate_case", generate_case)
    workflow.add_node("run_interface", run_interface)
    workflow.add_node("run_api_test", run_api_test)
    workflow.add_node("run_web_case", run_web_case)
    workflow.add_node("run_web_test", run_web_test)
    workflow.add_node("query_report", query_report)
    workflow.add_node("chat", chat)

    workflow.set_entry_point("identify_intent")
    workflow.add_conditional_edges(
        "identify_intent",
        lambda s: s["next_step"],
        {
            "generate_case": "generate_case",
            "run_interface": "run_interface",
            "run_api_test": "run_api_test",
            "run_web_case": "run_web_case",
            "run_web_test": "run_web_test",
            "query_report": "query_report",
            "chat": "chat",
            "end": END
        }
    )
    for node in ["generate_case", "run_interface", "run_api_test", "run_web_case", "run_web_test", "query_report", "chat"]:
        workflow.add_edge(node, END)

    return workflow.compile()


agent = build_agent()


def run_agent(user_message: str, session_id: str = "unknown",
              knowledge_base_id: str = None,
              interface_id: str = None,
              web_case_id: str = None,
              user_id: str = None) -> str:
    initial_state = {
        "messages": [{"role": "user", "content": user_message}],
        "next_step": "",
        "session_id": session_id,
        "knowledge_base_id": knowledge_base_id,
        "interface_id": interface_id,
        "web_case_id": web_case_id,
        "user_id": user_id
    }
    result = agent.invoke(initial_state)

    # 持久化消息
    try:
        conn = get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(
            "INSERT INTO messages (user_id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, session_id, "user", user_message, now)
        )
        for msg in reversed(result["messages"]):
            if msg["role"] == "assistant":
                cursor.execute(
                    "INSERT INTO messages (user_id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, session_id, "assistant", msg["content"], now)
                )
                break
        conn.commit()
        conn.close()
    except:
        pass

    for msg in reversed(result["messages"]):
        if msg["role"] == "assistant":
            return msg["content"]
    return "处理失败"
