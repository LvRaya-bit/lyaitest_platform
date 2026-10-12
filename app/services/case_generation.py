"""APP/接口测试用例生成服务。

生成采用"规则优先、AI 二次优化"的方式：即使没有配置模型 Key，也能先生成
可审核的基础用例；配置 ZHIPU_API_KEY 后，模型会按 APP/Web Prompt 逐条审校
标题、模块、优先级、类型、步骤和预期结果，并可继续补充证据驱动场景。

规则层按「测试用例生成Prompt模板.md」的八类覆盖规则生成：
  功能测试（正常流/异常流/边界值/业务规则）、性能测试、易用性测试、
  稳定性测试、安全性测试、兼容性测试、接口测试（探索无接口证据时跳过）、自动化测试。
"""
import csv
import io
import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse


ALLOWED_PRIORITIES = {"P0", "P1", "P2", "P3"}
ALLOWED_TEST_TYPES = {
    "功能测试", "性能测试", "兼容性测试", "易用性测试", "安全性测试",
    "稳定性测试", "接口测试", "自动化测试",
}


def _now() -> str:
    return datetime.now().isoformat()


def _case(title: str, module: str, priority: str, test_type: str,
          steps: Iterable[str], expected: str, source_ref: str = "",
          preconditions: str = "测试环境可用，已安装并启动被测 App",
          metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4())[:8],
        "title": title,
        "module": module or "未分类",
        "priority": priority,
        "test_type": test_type,
        "preconditions": preconditions,
        "steps": list(steps),
        "expected": expected,
        "source_ref": source_ref,
        "automation_status": "draft",
        "metadata": metadata or {},
    }


def _llm_json_list(prompt: str, max_cases: int = 20, timeout: int = 30) -> List[Dict[str, Any]]:
    """调用可选的大模型通道并返回原始 JSON 数组；无 Key 或输出异常时返回空列表。

    只负责"拿到模型给的 JSON 对象列表"，不假设字段含义，便于不同生成场景
    （APP 探索 / Web 探索）各自定义自己的字段与校验规则。
    """
    if os.getenv("CASE_GENERATION_USE_LLM", "true").lower() in {"0", "false", "no"}:
        return []
    api_key = os.getenv("ZHIPU_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return []
    try:
        from langchain_openai import ChatOpenAI

        if os.getenv("ZHIPU_API_KEY"):
            model = ChatOpenAI(
                model=os.getenv("CASE_GENERATION_MODEL", "glm-4"),
                openai_api_key=api_key,
                openai_api_base="https://open.bigmodel.cn/api/paas/v4/",
                temperature=0.2,
                timeout=timeout,
                max_retries=0,
            )
        else:
            model = ChatOpenAI(
                model=os.getenv("CASE_GENERATION_MODEL", "deepseek-chat"),
                openai_api_key=api_key,
                openai_api_base="https://api.deepseek.com",
                temperature=0.2,
                timeout=timeout,
                max_retries=0,
            )
        response = model.invoke(prompt)
        text = response.content.strip()
        fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
        payload = fenced.group(1) if fenced else text
        result = json.loads(payload)
        if not isinstance(result, list):
            return []
        return [item for item in result[:max_cases] if isinstance(item, dict) and item.get("title")]
    except Exception:
        return []


def _try_ai_cases(prompt: str, max_cases: int = 20) -> List[Dict[str, Any]]:
    """可选的 AI 增强层；无 Key 或模型输出异常时返回空列表。"""
    normalized = []
    for item in _llm_json_list(prompt, max_cases):
        raw_steps = item.get("steps", [])
        if isinstance(raw_steps, str):
            raw_steps = [line.strip() for line in raw_steps.splitlines() if line.strip()]
        if not isinstance(raw_steps, list):
            raw_steps = [str(raw_steps)]
        priority = str(item.get("priority", "P2")).strip()
        test_type = str(item.get("test_type", "功能测试")).strip()
        normalized.append({
            "id": str(uuid.uuid4())[:8],
            "title": str(item["title"]),
            "module": str(item.get("module", "AI生成")),
            "priority": priority if priority in ALLOWED_PRIORITIES else "P2",
            "test_type": test_type if test_type in ALLOWED_TEST_TYPES else "功能测试",
            "preconditions": str(item.get("preconditions", "测试环境可用")),
            "steps": raw_steps,
            "expected": str(item.get("expected", "以需求文档为准")),
            "source_ref": "AI增强",
            "automation_status": "draft",
            "metadata": {"generated_by": "llm", "prompt_version": "exploration-v2"},
        })
    return normalized


_AI_OPTIMIZE_CHUNK_SIZE = 40
_AI_PROMPT_VERSION = "template-v3-rule-ai-optimize"


def _number_case_steps(raw_steps: Any) -> List[str]:
    """把 AI 返回的步骤统一成可读的数字步骤，异常时返回空列表。"""
    if isinstance(raw_steps, str):
        raw_steps = [line.strip() for line in raw_steps.splitlines() if line.strip()]
    if not isinstance(raw_steps, list):
        return []
    result = []
    for index, value in enumerate(raw_steps, 1):
        text = str(value or "").strip()
        if not text:
            continue
        text = re.sub(r"^\s*\d+[.)、]\s*", "", text)
        result.append(f"{len(result) + 1}. {text}")
    return result


def _compact_cases_for_ai(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """只把用例评审所需字段交给模型，避免把内部 ID 和无关元数据扩散出去。"""
    return [{
        "case_id": case.get("id"),
        "title": case.get("title", ""),
        "module": case.get("module", ""),
        "priority": case.get("priority", "P2"),
        "test_type": case.get("test_type", "功能测试"),
        "preconditions": case.get("preconditions", ""),
        "steps": case.get("steps", []),
        "expected": case.get("expected", ""),
        "source_ref": case.get("source_ref", ""),
    } for case in cases]


def _case_optimization_prompt(platform: str, evidence: Any,
                              cases: List[Dict[str, Any]]) -> str:
    """基于两份参考模板生成统一的 APP/Web 二次优化 Prompt。"""
    is_web = platform.lower() == "web"
    platform_rules = """
【Web 专项约束】
1. 只能使用探索证据出现的 URL、页面标题、菜单、表单和 locator，不得编造页面或业务字段。
2. Web 用例的 executable_steps 由规则层固定生成，AI 不输出也不修改它；只优化人读的步骤和预期。
3. 对按钮、输入框、表格、分页、弹窗、加载/错误状态分别覆盖正常、异常、边界和稳定性场景。
4. 删除、退出、导出、上传、支付、审批等危险操作不得被改写为可执行操作。
""" if is_web else """
【APP 专项约束】
1. 只能使用 App 探索证据中出现的页面、控件和状态；content-desc 有换行时只取第一行。
2. 每个页面关注正常流、异常流、边界值、业务规则、性能、易用性、稳定性和安全性。
3. 没有接口证据时不得编造接口测试；不确定的业务规则写“需结合需求确认”。
4. 全局补充兼容性和自动化回归，但不要在每个页面重复相同的全局用例。
"""
    return f"""
你是资深{platform}测试负责人，负责对“规则层已经生成的测试用例初稿”进行二次评审和优化。
参考《测试用例生成Prompt模板》及 Web 版模板执行，目标是提升可执行性、可验证性和覆盖质量，
不是凭空重新编造测试范围。

【任务】
1. 必须对输入的每一条用例一一返回，保持 case_id 不变；不要合并、删除或新增用例。
2. 优化标题为“对象 + 动作 + 可验证预期”，目录使用真实页面/模块名称。
3. 检查 P0-P3 是否合理：P0 仅用于核心阻塞/登录/主流程，且不超过总数 10%；其余按影响降级。
4. 类型只能是：功能测试、性能测试、兼容性测试、易用性测试、安全性测试、稳定性测试、接口测试、自动化测试。
5. 步骤必须是数字编号、可执行；预期必须包含可观察的验证点，禁止“显示正常”“符合需求”等空话。
6. 去除重复和泛化表述；规则证据不足时保留“需结合需求确认”，不得编造接口、账号、数值、权限或页面。
7. 若原用例已有明确的页面、控件或定位信息，优化时必须保留其语义，不得换成不存在的对象。

{platform_rules}

【严格输出】只输出 JSON 数组，不要 Markdown、解释、注释或代码围栏。
每项只能包含：case_id、title、module、priority、test_type、preconditions、steps、expected。
case_id 必须与输入完全一致，steps 必须是字符串数组。

【探索/文档证据】
{json.dumps(evidence, ensure_ascii=False)[:60000]}

【规则层初稿】
{json.dumps(_compact_cases_for_ai(cases), ensure_ascii=False)}
""".strip()


def _merge_ai_optimized_cases(cases: List[Dict[str, Any]],
                              ai_items: List[Dict[str, Any]],
                              platform: str) -> List[Dict[str, Any]]:
    """只接受能和规则初稿一一对应的 AI 结果，避免模型幻觉污染用例库。"""
    by_id = {str(item.get("case_id")): item for item in ai_items if item.get("case_id")}
    by_title = {str(item.get("title", "")).strip(): item for item in ai_items if item.get("title")}
    merged = []
    for base in cases:
        item = by_id.get(str(base.get("id"))) or by_title.get(str(base.get("title", "")).strip())
        if not item:
            merged.append(base)
            continue
        updated = dict(base)
        for field in ("title", "module", "preconditions", "expected"):
            value = str(item.get(field) or "").strip()
            if value:
                updated[field] = value
        priority = str(item.get("priority") or "").strip()
        if priority in ALLOWED_PRIORITIES:
            updated["priority"] = priority
        test_type = str(item.get("test_type") or "").strip()
        if test_type in ALLOWED_TEST_TYPES:
            updated["test_type"] = test_type
        steps = _number_case_steps(item.get("steps"))
        if steps:
            updated["steps"] = steps
        metadata = dict(base.get("metadata") or {})
        metadata.update({
            "generated_by": "llm",
            "rule_generated": True,
            "ai_optimized": True,
            "prompt_version": _AI_PROMPT_VERSION,
            "optimization_platform": platform,
        })
        updated["metadata"] = metadata
        merged.append(updated)

    return merged


def _optimize_cases_with_ai(cases: List[Dict[str, Any]], evidence: Any,
                            platform: str) -> List[Dict[str, Any]]:
    """规则初稿 -> AI 按模板逐条优化；无 Key、超时或格式错误时完整回退规则结果。"""
    if not cases:
        return cases
    optimized = []
    for start in range(0, len(cases), _AI_OPTIMIZE_CHUNK_SIZE):
        chunk = cases[start:start + _AI_OPTIMIZE_CHUNK_SIZE]
        prompt = _case_optimization_prompt(platform, evidence, chunk)
        items = _llm_json_list(prompt, max_cases=len(chunk), timeout=90)
        optimized.extend(_merge_ai_optimized_cases(chunk, items, platform))
    # AI 不得把 P0 泛化成大量高优先级；按完整结果而不是分块结果限额。
    p0_limit = max(1, int(len(optimized) * 0.1))
    p0_seen = 0
    for item in optimized:
        if item.get("priority") != "P0":
            continue
        p0_seen += 1
        if p0_seen > p0_limit:
            item["priority"] = "P1"
    return optimized


def generate_app_cases_from_doc(content: str) -> List[Dict[str, Any]]:
    """从 Markdown 功能文档生成可审核的基础用例。

    标题作为模块，列表项/段落作为功能点；每个功能点生成正常、异常、
    稳定性三类最小用例，避免把没有证据的业务断言写死。
    """
    lines = content.splitlines()
    module = "功能概览"
    cases: List[Dict[str, Any]] = []
    seen = set()
    for index, raw in enumerate(lines):
        line = raw.strip()
        heading = re.match(r"^#{1,4}\s+(.+)$", line)
        if heading:
            module = re.sub(r"[`*_]", "", heading.group(1)).strip()
            continue
        item = re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)(.+)$", line)
        if not item:
            continue
        feature = re.sub(r"[`*_]", "", item.group(1)).strip()
        if len(feature) < 2 or feature in seen:
            continue
        seen.add(feature)
        ref = f"第{index + 1}行"
        cases.extend([
            _case(f"{feature}正常流程", module, "P0", "功能测试",
                  [f"进入{module}", f"执行：{feature}"],
                  f"{feature}正常完成，页面状态和提示符合文档要求。", ref),
            _case(f"{feature}异常流程", module, "P1", "功能测试",
                  [f"进入{module}", f"在{feature}场景制造无效输入或异常条件"],
                  "系统阻止无效操作，并给出明确错误提示；不得崩溃。", ref),
            _case(f"{feature}后台恢复与稳定性", module, "P2", "稳定性测试",
                  [f"进入{module}", f"执行{feature}", "将 App 切到后台后重新打开"],
                  "App 能恢复到合理状态，数据和页面不出现异常。", ref),
        ])
    if not cases and content.strip():
        cases.append(_case("功能文档基础冒烟", "功能概览", "P0", "功能测试",
                           ["启动 App", "按功能文档执行主流程"],
                           "主流程可完成，未出现崩溃。", "文档正文"))

    # 先保留规则层完整覆盖，再让 AI 对每条初稿做一一对应的质量审校。
    cases = _optimize_cases_with_ai(
        cases,
        {"source": "APP 功能文档", "content": content[:40000]},
        "APP",
    )
    ai_cases = _try_ai_cases(
        "你是 Android 测试专家。请根据以下功能文档补充最多 20 条可审核的测试用例，"
        "只输出 JSON 数组，每项包含 title/module/priority/test_type/preconditions/steps/expected。"
        "不要编造文档中不存在的具体接口或数值。\n\n" + content[:24000]
    )
    existing = {item["title"] for item in cases}
    cases.extend(item for item in ai_cases if item["title"] not in existing)
    return cases


def _first_line(value: Any) -> str:
    text = str(value or "")
    return re.sub(r"\s+", " ", text.splitlines()[0] if text.splitlines() else text).strip()


def _exploration_module(page_name: str) -> str:
    """将 Runner 的页面名转换成可读目录，同时保留探索路径追溯性。"""
    module = re.sub(r"^\d+_", "", str(page_name or "未命名页面"))
    module = re.sub(r"_click_(\d+)", r"-点击\1", module)
    return module.replace("_", " ").strip() or "探索页面"


def _meaningful_exploration_elements(page: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从 XML 元素中提取有描述的代表性入口，避免空 ImageView 造成冗余用例。"""
    elements = page.get("clickable_elements", page.get("elements", [])) or []
    result = []
    seen = set()
    for element in elements:
        if not isinstance(element, dict):
            continue
        label = _first_line(
            element.get("desc") or element.get("content_desc") or element.get("text")
        )
        if not label:
            continue
        if re.search(r"退出|删除|卸载|注销|清空|恢复出厂|同意协议|取消", label):
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "label": label[:80],
            "class": element.get("class", ""),
            "bounds": element.get("bounds", ""),
            "enabled": element.get("enabled", True),
        })
        if len(result) >= 20:
            break
    return result


def _exploration_prompt(exploration: Dict[str, Any], evidence: List[Dict[str, Any]], max_cases: int) -> str:
    """将参考 Markdown 模板转换成可被程序解析的 JSON Prompt。"""
    payload = {
        "login": exploration.get("login", {}),
        "total_pages": exploration.get("total_pages", len(evidence)),
        "actions": exploration.get("actions", 0),
        "pages": evidence,
    }
    return f"""
你是资深 Android 测试工程师。请根据 Appium 探索证据生成最多 {max_cases} 条可审核测试用例。

【严格输出】只输出 JSON 数组，不要 Markdown、解释或代码围栏。每项必须包含：
title、module、priority、test_type、preconditions、steps、expected。
priority 只能是 P0/P1/P2/P3；test_type 只能是：功能测试、性能测试、兼容性测试、易用性测试、
安全性测试、稳定性测试、接口测试、自动化测试。

【覆盖规则（参考测试用例生成Prompt模板.md）】
1. 按页面拆分 module；页面元素的 content-desc 可能包含换行，只取第一行作为控件名称。
2. 功能测试必须覆盖四个子类：正常流（主流程+入口点击）、异常流（空值/特殊字符/重复提交/网络异常）、
   边界值（列表为空/1条/超一页/数值上下限）、业务规则（状态流转/权限控制/数据一致性）。
3. 每个页面至少补充 1 条性能测试（加载时间、响应时间）和 1 条易用性测试（空状态、错误提示、操作反馈）。
4. 每个模块至少 1 条稳定性测试：断网/弱网、切后台恢复、重复进入或杀进程恢复。
5. 每个模块 0-1 条安全性测试：越权访问、敏感信息加密、权限控制。
6. 全局补充兼容性用例 3-5 条（Android 版本、屏幕尺寸/分辨率）和自动化回归用例 2-3 条。
7. 有登录证据时补充登录成功/失败、退出后访问保护页面等用例；没有接口证据时不要生成接口测试。
8. 只能使用下方探索证据中出现的页面、控件和状态。无法确认的业务规则写成"需结合需求确认"，不得编造接口、数值或页面。
9. P0 只给登录和核心主流程，控制在总量 10% 以内；步骤必须是可执行的数字步骤，预期结果必须可验证。
10. 同类入口只保留代表性用例；所有用例总数建议 60-100 条。

【探索证据】
{json.dumps(payload, ensure_ascii=False)}
""".strip()


def case_matrix(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_priority = {key: 0 for key in ("P0", "P1", "P2", "P3")}
    by_type: Dict[str, int] = {}
    for case in cases:
        priority = case.get("priority", "P2")
        test_type = case.get("test_type", "功能测试")
        by_priority[priority] = by_priority.get(priority, 0) + 1
        by_type[test_type] = by_type.get(test_type, 0) + 1
    return {"total": len(cases), "by_priority": by_priority, "by_type": by_type}


# APP 规则用例产出上限与每页配额。
# 模板建议总量 60-100 条；末尾固定全局用例占用配额，页面循环按剩余额度收口。
_APP_MAX_RULE_CASES = 60
_APP_GLOBAL_CASES = 6
_APP_MAX_ELEMENTS_PER_PAGE = 3  # 同类入口只保留代表性用例，腾出额度给异常/边界/业务规则


def generate_app_cases_from_exploration(exploration: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按 Prompt 模板生成探索用例：规则覆盖八类测试维度，AI 负责补充场景。

    每页生成结构（受 page_limit 约束）：
      功能-正常流：页面加载 + 前3个代表性入口点击
      功能-异常流：异常输入与错误处理
      功能-边界值：列表/数据边界场景
      功能-业务规则：状态/权限业务规则
      性能/易用性/稳定性/安全性：各1条
    全局：兼容性x2 + 自动化x2 + 安全性x1 + 稳定性x1
    """
    cases: List[Dict[str, Any]] = []
    pages = exploration.get("pages", []) if isinstance(exploration, dict) else []
    evidence: List[Dict[str, Any]] = []
    page_limit = _APP_MAX_RULE_CASES - _APP_GLOBAL_CASES

    for page_index, page in enumerate(pages):
        if len(cases) >= page_limit:
            break
        page_name = str(page.get("name", f"探索页面{page_index + 1}"))
        module = _exploration_module(page_name)
        elements = _meaningful_exploration_elements(page)
        evidence.append({
            "page": page_name,
            "module": module,
            "depth": page.get("depth", 0),
            "package": page.get("package", ""),
            "elements": elements,
        })
        page_priority = "P0" if page_index == 0 else "P1"

        # -- 功能测试·正常流 --
        cases.append(_case(
            f"{module}页面正常加载", module, page_priority, "功能测试",
            [f"1. 启动 App", f"2. 导航到页面：{module}"],
            "页面加载完成，主要元素可见，未出现崩溃或空白。", page_name,
            metadata={"page": page_name, "depth": page.get("depth", 0), "category": "normal"},
        ))
        page_elements = 0
        for element in elements:
            if len(cases) >= page_limit or page_elements >= _APP_MAX_ELEMENTS_PER_PAGE:
                break
            page_elements += 1
            label = element["label"]
            cases.append(_case(
                f"{module}-{label}入口可用", module, "P1", "功能测试",
                [f"1. 导航到页面：{module}", f"2. 点击\u201c{label}\u201d"],
                "入口响应正常，进入对应页面或展示与当前状态相符的反馈；无崩溃、卡死。",
                page_name, metadata={"page": page_name, "element": element, "category": "normal"},
            ))

        # -- 功能测试·异常流 --
        if len(cases) < page_limit:
            cases.append(_case(
                f"{module}异常输入与错误处理", module,
                "P1" if page_index == 0 else "P2", "功能测试",
                [f"1. 进入页面：{module}",
                 "2. 在输入区域填入空值、特殊字符、超长文本或非法格式",
                 "3. 触发提交或相关操作"],
                "系统阻止无效操作，给出明确的错误提示；不崩溃、不卡死、不提交错误数据。",
                page_name, metadata={"page": page_name, "category": "abnormal"},
            ))

        # -- 功能测试·边界值 --
        if len(cases) < page_limit:
            cases.append(_case(
                f"{module}列表与数据边界场景", module, "P2", "功能测试",
                [f"1. 进入页面：{module}",
                 "2. 检查列表为空、仅1条数据、超过一页等边界场景",
                 "3. 验证分页、排序和空状态展示"],
                "各边界场景下页面展示正确，分页逻辑正常，空状态有友好提示；不出现数据错乱。",
                page_name, metadata={"page": page_name, "category": "boundary"},
            ))

        # -- 功能测试·业务规则 --
        if len(cases) < page_limit:
            cases.append(_case(
                f"{module}状态与权限业务规则", module, "P2", "功能测试",
                [f"1. 进入页面：{module}",
                 "2. 验证不同状态（离线/未登录/权限不足）下的页面表现",
                 "3. 检查状态切换后的数据一致性"],
                "状态流转正确，权限控制生效，数据展示与当前状态一致；具体规则需结合需求确认。",
                page_name, metadata={"page": page_name, "category": "business_rule"},
            ))

        # -- 性能测试 --
        if len(cases) < page_limit:
            cases.append(_case(
                f"{module}页面加载与响应性能", module,
                "P1" if page_index == 0 else "P2", "性能测试",
                [f"1. 冷启动或进入页面：{module}", "2. 记录主要内容出现和操作响应耗时"],
                "页面和主要内容在目标性能基线内展示，点击后有明确响应；具体阈值需结合需求确认。",
                page_name, metadata={"page": page_name, "category": "performance"},
            ))

        # -- 易用性测试 --
        if len(cases) < page_limit:
            cases.append(_case(
                f"{module}加载、空状态和错误提示", module, "P2", "易用性测试",
                [f"1. 进入页面：{module}", "2. 观察加载中、无数据、异常或网络失败场景的页面反馈"],
                "有清晰的加载、空状态和错误提示，按钮状态及操作反馈明确，不出现遮挡或死循环。",
                page_name, metadata={"page": page_name, "category": "usability"},
            ))

        # -- 稳定性测试 --
        if len(cases) < page_limit:
            cases.append(_case(
                f"{module}切后台恢复", module, "P2", "稳定性测试",
                [f"1. 进入页面：{module}", "2. 执行一次页面操作", "3. 将 App 切到后台后重新打开"],
                "页面恢复到合理状态，已完成操作和数据不出现异常；如需重新加载应有明确提示。",
                page_name, metadata={"page": page_name, "category": "stability"},
            ))

        # -- 安全性测试（每模块0-1条）--
        if len(cases) < page_limit:
            cases.append(_case(
                f"{module}敏感信息与权限控制", module, "P2", "安全性测试",
                [f"1. 进入页面：{module}",
                 "2. 检查是否存在敏感信息明文展示",
                 "3. 验证页面数据是否受权限控制保护"],
                "敏感信息不明文展示，页面内容受权限控制保护；越权访问应被拦截或提示。",
                page_name, metadata={"page": page_name, "category": "security"},
            ))

    if pages:
        # -- 全局用例：兼容性 + 自动化 + 安全性 + 稳定性 --
        cases.extend([
            _case("退出登录后访问受保护页面", "登录", "P1", "安全性测试",
                  ["1. 登录 App", "2. 退出登录", "3. 返回或直接访问需要登录的页面"],
                  "退出后不能继续访问受保护数据，页面跳转到登录页或提示重新登录。", "全局安全性"),
            _case("不同 Android 版本运行核心流程", "兼容性", "P1", "兼容性测试",
                  ["1. 在至少两个 Android 版本设备上安装 App", "2. 执行登录和核心页面流程"],
                  "核心流程、布局和权限行为一致，无闪退；差异需记录并评估。", "全局兼容性"),
            _case("不同屏幕尺寸运行核心页面", "兼容性", "P2", "兼容性测试",
                  ["1. 在不同分辨率/屏幕尺寸设备上启动 App", "2. 检查首页和主要操作页面"],
                  "控件、文字和弹窗不重叠、不裁切，关键操作区域可正常点击。", "全局兼容性"),
            _case("探索结果可重复执行", "自动化回归", "P1", "自动化测试",
                  ["1. 使用同一设备配置重复执行 Appium 探索", "2. 对比页面签名、关键元素和失败日志"],
                  "脚本可重复运行，页面去重稳定，失败时保留 XML、截图和可定位错误。", "全局自动化"),
            _case("关键页面元素定位稳定", "自动化回归", "P2", "自动化测试",
                  ["1. 执行登录和核心页面自动化流程", "2. 检查控件定位是否依赖稳定描述/资源信息"],
                  "控件定位可跨重复运行复用，不因布局轻微变化频繁失效。", "全局自动化"),
            _case("长时间运行内存与稳定性", "全局稳定性", "P2", "稳定性测试",
                  ["1. 启动 App 并持续操作 30 分钟以上", "2. 监控内存占用和页面响应",
                   "3. 检查是否存在内存泄漏或卡顿加剧"],
                  "长时间运行后内存占用稳定，无明显泄漏，页面响应无明显劣化。",
                   "全局稳定性"),
        ])

        # 规则层先保证模板覆盖，再按 App Prompt 对每条用例做质量优化。
        cases = _optimize_cases_with_ai(cases, evidence, "APP")
        prompt = _exploration_prompt(exploration, evidence, max_cases=40)
        ai_cases = _try_ai_cases(prompt, max_cases=40)
        existing = {item["title"] for item in cases}
        cases.extend(item for item in ai_cases if item["title"] not in existing)
    return cases


# Web 探索用例：产物直接写 web_cases 表，steps 必须是 Runner 能执行的动作字典。
WEB_ALLOWED_ACTIONS = {"navigate", "click", "input", "assert", "wait", "screenshot"}
_WEB_ACTION_ALIASES = {
    "open": "navigate", "goto": "navigate", "visit": "navigate", "url": "navigate",
    "tap": "click", "press": "click",
    "fill": "input", "type": "input", "send_keys": "input", "input_text": "input",
    "verify": "assert", "check": "assert", "expect": "assert", "assert_text": "assert",
    "wait_for": "wait", "sleep": "wait",
    "capture": "screenshot",
}
_WEB_ASSERT_KEYWORDS = {"url", "地址", "title", "标题", "status", "status_code", "状态码"}


def _web_step(action: str, element: str = "", value: str = "", expected: str = "") -> Dict[str, str]:
    return {"action": action, "element": element, "value": value, "expected": expected}


def _normalize_web_executable(raw: Any) -> List[Dict[str, str]]:
    """把模型给出的 executable_steps 收敛成 Runner 支持的步骤，非法动作直接丢弃。

    Runner 对不支持的动作只会记 SKIP，不会报错，所以这里必须先把动作和必填字段
    校验干净，避免生成一堆"看起来有步骤、实际全跳过"的用例。
    """
    if not isinstance(raw, list):
        return []
    steps: List[Dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip().lower()
        action = action if action in WEB_ALLOWED_ACTIONS else _WEB_ACTION_ALIASES.get(action, "")
        if not action:
            continue
        step = _web_step(
            action,
            str(item.get("element") or "").strip(),
            str(item.get("value") or "").strip(),
            str(item.get("expected") or "").strip(),
        )
        # 模型常把 URL / 等待毫秒数写在 value 里，而 Runner 只认 element，这里做一次归一。
        if action in {"navigate", "wait"} and not step["element"] and step["value"]:
            if action == "navigate" or step["value"].isdigit():
                step["element"], step["value"] = step["value"], ""
        if action == "navigate" and not (step["element"] or step["value"]):
            continue
        if action in {"click", "input"} and not step["element"]:
            continue
        if action == "assert" and not step["expected"]:
            continue
        steps.append(step)
    return steps


def _web_module(page: Dict[str, Any]) -> str:
    """Web 用例目录优先用页面标题，可读性远好于 BFS 生成的页面名。"""
    title = _first_line(page.get("title"))
    if title:
        return title[:40]
    module = re.sub(r"^\d+_", "", str(page.get("name") or "未命名页面"))
    module = re.sub(r"_click_(\d+)", r"-点击\1", module)
    return module.replace("_", " ").strip() or "探索页面"


def _web_page_label(page: Dict[str, Any], index: int) -> str:
    title = _first_line(page.get("title"))
    if title:
        return title[:60]
    path = urlparse(str(page.get("url") or "")).path or "/"
    return path[:60] or f"探索页面{index + 1}"


def _web_clickable(element: Dict[str, Any], allow_submit: bool) -> bool:
    """判断该元素在生成用例时是否可以点击：与 Runner 的探索拦截规则保持一致。"""
    if element.get("kind") != "click" or not element.get("primary_locator"):
        return False
    blocked = element.get("blocked")
    if blocked == "form_submit":
        return allow_submit
    return not blocked


def _web_case(title: str, module: str, priority: str, test_type: str, steps: List[str],
              expected: str, source_ref: str, executable: List[Dict[str, str]],
              preconditions: str = "测试环境可访问被测站点，测试数据已准备",
              metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
    case = _case(title, module, priority, test_type, steps, expected, source_ref,
                 preconditions, metadata)
    case["executable"] = executable
    return case


# 规则用例产出上限：大站的导航/页脚动辄数百个可点击链接，不封顶会一次生成上千条
# 无法维护的用例（曾出现单次生成 1136 条）。
_WEB_MAX_RULE_CASES = 100
_WEB_MAX_ELEMENTS_PER_PAGE = 3  # 同类入口只保留代表性用例，腾出额度给其他测试维度


def _web_rule_cases(exploration: Dict[str, Any]) -> List[Dict[str, Any]]:
    """规则层：按模板八类覆盖，每个探索到的页面生成多条可执行用例。

    每页生成结构（受 _WEB_MAX_RULE_CASES 约束）：
      功能-正常流：页面可访问 + 前3个入口可点击 + 导航往返一致性
      功能-异常流：非法输入不崩溃
      稳定性：页面重载一致性
      性能：页面加载性能
    """
    cases: List[Dict[str, Any]] = []
    pages = exploration.get("pages") or []
    edges = exploration.get("edges") or []
    allow_submit = bool(exploration.get("allow_form_submit"))

    for index, page in enumerate(pages):
        if len(cases) >= _WEB_MAX_RULE_CASES:
            break
        page_url = str(page.get("url") or "").strip()
        if not page_url:
            continue
        module = _web_module(page)
        label = _web_page_label(page, index)
        page_name = str(page.get("name") or "")
        priority = "P0" if index == 0 else "P1"

        title_expect = _first_line(page.get("title"))[:60]
        if title_expect:
            last_step = _web_step("assert", "title", expected=title_expect)
            assert_desc = f"页面标题包含「{title_expect}」"
        else:
            path = urlparse(page_url).path or "/"
            last_step = _web_step("assert", "url", expected=path)
            assert_desc = f"页面地址包含 {path}"

        # -- 功能测试·正常流：页面可访问 --
        cases.append(_web_case(
            f"{label}页面可访问", module, priority, "功能测试",
            [f"1. 打开 {page_url}", f"2. 校验{assert_desc}"],
            "页面正常加载，未出现空白页或错误提示，断言项与探索记录一致。",
            page_name,
            [_web_step("navigate", page_url), last_step, _web_step("screenshot")],
            metadata={"page": page_name, "url": page_url, "depth": page.get("depth", 0),
                      "generated_by": "rule", "category": "normal"},
        ))

        # -- 功能测试·正常流：前3个入口可点击 --
        page_elements = 0
        first_clickable_locator = None
        for element in page.get("elements") or []:
            if len(cases) >= _WEB_MAX_RULE_CASES or page_elements >= _WEB_MAX_ELEMENTS_PER_PAGE:
                break
            if not _web_clickable(element, allow_submit):
                continue
            page_elements += 1
            locator = str(element["primary_locator"])
            if first_clickable_locator is None:
                first_clickable_locator = locator
            element_label = _first_line(element.get("label"))[:60] or locator
            target = next((edge for edge in edges
                           if edge.get("from_page") == page_name and edge.get("locator") == locator), None)
            executable = [_web_step("navigate", page_url), _web_step("click", locator),
                          _web_step("wait", "800")]
            if target:
                target_path = urlparse(str(target.get("to") or "")).path or "/"
                executable.append(_web_step("assert", "url", expected=target_path))
                expected = f"点击后跳转到 {target_path}，页面内容正常展示。"
            else:
                expected = "入口响应正常，页面出现符合预期的变化，无报错或卡死。"
            cases.append(_web_case(
                f"{module}-{element_label}入口可点击", module, "P1", "功能测试",
                [f"1. 打开 {page_url}", f"2. 点击「{element_label}」（{locator}）",
                 "3. 观察页面跳转与响应"],
                expected, page_name, executable,
                metadata={"page": page_name, "element": element_label, "locator": locator,
                          "generated_by": "rule", "category": "normal"},
            ))

        # -- 功能测试·正常流：导航往返一致性 --
        if len(cases) < _WEB_MAX_RULE_CASES and first_clickable_locator:
            executable = [_web_step("navigate", page_url),
                          _web_step("click", first_clickable_locator),
                          _web_step("wait", "1000"),
                          _web_step("navigate", page_url)]
            if title_expect:
                executable.append(_web_step("assert", "title", expected=title_expect))
            else:
                path = urlparse(page_url).path or "/"
                executable.append(_web_step("assert", "url", expected=path))
            cases.append(_web_case(
                f"{module}导航往返一致性", module, "P2", "功能测试",
                [f"1. 打开 {page_url}", "2. 点击任一入口", "3. 返回原页面", "4. 校验页面恢复"],
                "返回后页面状态恢复正确，无重复加载或数据残留。",
                page_name, executable,
                metadata={"page": page_name, "generated_by": "rule", "category": "normal"},
            ))

        # -- 功能测试·异常流：非法输入不崩溃 --
        input_elements = [e for e in (page.get("elements") or [])
                         if e.get("kind") == "input" and e.get("primary_locator")]
        if len(cases) < _WEB_MAX_RULE_CASES and input_elements:
            input_locator = str(input_elements[0]["primary_locator"])
            executable = [_web_step("navigate", page_url),
                          _web_step("input", input_locator, value="<script>alert(1)</script>"),
                          _web_step("screenshot")]
            if title_expect:
                executable.append(_web_step("assert", "title", expected=title_expect))
            cases.append(_web_case(
                f"{module}非法输入不崩溃", module, "P2", "功能测试",
                [f"1. 打开 {page_url}", "2. 在输入框中输入特殊字符和超长文本",
                 "3. 检查页面是否正常响应"],
                "页面处理非法输入 gracefully，不崩溃、不执行注入脚本；有合理反馈。",
                page_name, executable,
                metadata={"page": page_name, "generated_by": "rule", "category": "abnormal"},
            ))

        # -- 表单测试：必填/格式/边界证据 --
        if len(cases) < _WEB_MAX_RULE_CASES and input_elements:
            form_input = input_elements[0]
            input_locator = str(form_input["primary_locator"])
            label_text = _first_line(form_input.get("label"))[:40] or input_locator
            required_hint = "必填字段" if form_input.get("required") else "输入字段"
            executable = [
                _web_step("navigate", page_url),
                _web_step("input", input_locator, value=""),
                _web_step("screenshot"),
            ]
            if title_expect:
                executable.append(_web_step("assert", "title", expected=title_expect))
            cases.append(_web_case(
                f"{module}{label_text}{required_hint}校验", module, "P1", "功能测试",
                [f"1. 打开 {page_url}", f"2. 清空「{label_text}」输入框", "3. 触发表单校验"],
                "表单对空值或必填项给出明确提示，不提交无效数据，页面不崩溃。",
                page_name, executable,
                metadata={"page": page_name, "element": label_text, "locator": input_locator,
                          "generated_by": "rule", "category": "form_validation"},
            ))

        # -- 表格/分页测试：仅在探索证据中存在时生成 --
        if len(cases) < _WEB_MAX_RULE_CASES and (page.get("tables") or page.get("pagination")):
            executable = [_web_step("navigate", page_url), _web_step("wait", "500"),
                          _web_step("screenshot")]
            if title_expect:
                executable.append(_web_step("assert", "title", expected=title_expect))
            cases.append(_web_case(
                f"{module}表格与分页状态", module, "P1", "功能测试",
                [f"1. 打开 {page_url}", "2. 检查表头、数据行、分页控件和空状态", "3. 执行翻页或筛选后观察列表变化"],
                "表格结构、分页边界、空状态和数据加载状态展示正确，操作后列表与页码保持一致。",
                page_name, executable,
                metadata={"page": page_name, "generated_by": "rule", "category": "table_pagination"},
            ))

        # -- 探索证据中的前端错误必须转为缺陷倾向用例 --
        if len(cases) < _WEB_MAX_RULE_CASES and (page.get("console_errors") or page.get("request_failures")):
            executable = [_web_step("navigate", page_url), _web_step("screenshot")]
            if title_expect:
                executable.append(_web_step("assert", "title", expected=title_expect))
            cases.append(_web_case(
                f"{module}页面错误采集与稳定性", module, "P1", "稳定性测试",
                [f"1. 打开 {page_url}", "2. 检查控制台错误和网络请求失败", "3. 确认页面主要功能仍可操作"],
                "页面不应出现未处理的前端异常或关键资源加载失败；错误需可定位并记录。",
                page_name, executable,
                metadata={"page": page_name, "generated_by": "rule", "category": "runtime_error",
                          "console_errors": len(page.get("console_errors") or []),
                          "request_failures": len(page.get("request_failures") or [])},
            ))

        # -- 稳定性测试：页面重载一致性 --
        if len(cases) < _WEB_MAX_RULE_CASES:
            executable = [_web_step("navigate", page_url)]
            if title_expect:
                executable.append(_web_step("assert", "title", expected=title_expect))
                executable.append(_web_step("navigate", page_url))
                executable.append(_web_step("assert", "title", expected=title_expect))
            else:
                path = urlparse(page_url).path or "/"
                executable.append(_web_step("assert", "url", expected=path))
                executable.append(_web_step("navigate", page_url))
                executable.append(_web_step("assert", "url", expected=path))
            executable.append(_web_step("screenshot"))
            cases.append(_web_case(
                f"{module}页面重载一致性", module, "P2", "稳定性测试",
                [f"1. 打开 {page_url}", "2. 重新加载页面", "3. 校验页面内容一致"],
                "重载后页面内容与首次加载一致，无数据丢失或布局错乱。",
                page_name, executable,
                metadata={"page": page_name, "generated_by": "rule", "category": "stability"},
            ))

        # -- 性能测试：页面加载性能 --
        if len(cases) < _WEB_MAX_RULE_CASES:
            executable = [_web_step("navigate", page_url), _web_step("wait", "2000"),
                          _web_step("screenshot")]
            cases.append(_web_case(
                f"{module}页面加载性能", module, "P2", "性能测试",
                [f"1. 打开 {page_url}", "2. 记录页面完全加载耗时"],
                "页面在合理时间内完成加载；具体阈值需结合需求确认。",
                page_name, executable,
                metadata={"page": page_name, "generated_by": "rule", "category": "performance"},
            ))

        # -- 功能测试·边界值：列表与数据边界 --
        if len(cases) < _WEB_MAX_RULE_CASES:
            executable = [_web_step("navigate", page_url), _web_step("wait", "1000"),
                          _web_step("screenshot")]
            if title_expect:
                executable.append(_web_step("assert", "title", expected=title_expect))
            cases.append(_web_case(
                f"{module}列表与数据边界场景", module, "P2", "功能测试",
                [f"1. 打开 {page_url}", "2. 检查列表为空、仅1条数据、超过一页等边界场景",
                 "3. 验证分页、排序和空状态展示"],
                "各边界场景下页面展示正确，分页逻辑正常，空状态有友好提示；不出现数据错乱。",
                page_name, executable,
                metadata={"page": page_name, "generated_by": "rule", "category": "boundary"},
            ))

        # -- 功能测试·业务规则：状态与权限 --
        if len(cases) < _WEB_MAX_RULE_CASES:
            executable = [_web_step("navigate", page_url), _web_step("wait", "1000"),
                          _web_step("screenshot")]
            cases.append(_web_case(
                f"{module}状态与权限业务规则", module, "P2", "功能测试",
                [f"1. 打开 {page_url}", "2. 验证不同状态（未登录/权限不足）下的页面表现",
                 "3. 检查状态切换后的数据一致性"],
                "状态流转正确，权限控制生效，数据展示与当前状态一致；具体规则需结合需求确认。",
                page_name, executable,
                metadata={"page": page_name, "generated_by": "rule", "category": "business_rule"},
            ))

        # -- 易用性测试：空状态与错误提示 --
        if len(cases) < _WEB_MAX_RULE_CASES:
            executable = [_web_step("navigate", page_url), _web_step("screenshot")]
            cases.append(_web_case(
                f"{module}空状态与错误提示", module, "P2", "易用性测试",
                [f"1. 打开 {page_url}", "2. 观察加载中、无数据、异常场景的页面反馈"],
                "有清晰的加载、空状态和错误提示，不出现遮挡或死循环。",
                page_name, executable,
                metadata={"page": page_name, "generated_by": "rule", "category": "usability"},
            ))

    # -- 全局用例 --
    for gcase in _web_global_cases(pages, exploration):
        if len(cases) < _WEB_MAX_RULE_CASES:
            cases.append(gcase)

    return cases


def _web_global_cases(pages: List[Dict[str, Any]], exploration: Dict[str, Any]) -> List[Dict[str, Any]]:
    """全局维度用例：兼容性 + 自动化 + 安全性。"""
    result: List[Dict[str, Any]] = []
    first_page = pages[0] if pages else {}
    first_url = str(first_page.get("url") or "") or "https://example.com"
    first_title = _first_line(first_page.get("title"))[:60]
    nav_step = _web_step("navigate", first_url)
    assert_title = _web_step("assert", "title", expected=first_title) if first_title else \
                   _web_step("assert", "url", expected=urlparse(first_url).path or "/")

    # 兼容性：不同浏览器/视口尺寸
    result.append(_web_case(
        "不同视口尺寸下核心页面布局", "兼容性", "P2", "兼容性测试",
        [f"1. 打开 {first_url}", "2. 在不同视口尺寸下检查布局"],
        "控件、文字和弹窗不重叠、不裁切，关键操作区域可正常点击。",
        "全局兼容性",
        [nav_step, _web_step("screenshot")],
        metadata={"generated_by": "rule", "category": "compatibility"},
    ))

    # 自动化：探索结果可重复执行
    result.append(_web_case(
        "探索结果可重复执行", "自动化回归", "P1", "自动化测试",
        ["1. 重复执行 Playwright 探索脚本", "2. 对比页面签名和关键元素"],
        "脚本可重复运行，页面去重稳定，失败时保留截图和可定位错误。",
        "全局自动化",
        [nav_step, assert_title, _web_step("screenshot")],
        metadata={"generated_by": "rule", "category": "automation"},
    ))

    # 安全性：未登录访问保护页面
    result.append(_web_case(
        "未登录访问保护页面", "安全性", "P1", "安全性测试",
        [f"1. 清除登录状态", f"2. 打开 {first_url}", "3. 检查是否跳转到登录页"],
        "未登录时不能访问受保护内容，页面跳转到登录页或提示需要登录。",
        "全局安全性",
        [nav_step, _web_step("screenshot")],
        metadata={"generated_by": "rule", "category": "security"},
    ))
    return result


def _web_evidence(exploration: Dict[str, Any]) -> List[Dict[str, Any]]:
    """给模型的探索证据：只暴露可安全使用的定位器，避免它编造选择器。"""
    allow_submit = bool(exploration.get("allow_form_submit"))
    evidence = []
    for page in exploration.get("pages") or []:
        elements = []
        for element in page.get("elements") or []:
            if element.get("blocked") == "dangerous" or element.get("blocked") == "external":
                continue
            if element.get("blocked") == "form_submit" and not allow_submit:
                continue
            locator = str(element.get("primary_locator") or "")
            if not locator:
                continue
            elements.append({
                "label": _first_line(element.get("label"))[:60],
                "kind": element.get("kind"),
                "locator": locator,
                "locators": element.get("locators") or [],
                "required": bool(element.get("required")),
                "type": element.get("type", ""),
                "pattern": element.get("pattern", ""),
            })
        evidence.append({
            "page": page.get("name"),
            "module": _web_module(page),
            "url": page.get("url"),
            "title": _first_line(page.get("title"))[:80],
            "depth": page.get("depth", 0),
            "headings": page.get("headings") or [],
            "tables": page.get("tables") or [],
            "pagination": page.get("pagination") or [],
            "console_errors": page.get("console_errors") or [],
            "request_failures": page.get("request_failures") or [],
            "elements": elements[:30],
        })
    return evidence


def _web_exploration_prompt(exploration: Dict[str, Any], evidence: List[Dict[str, Any]],
                            max_cases: int) -> str:
    payload = {
        "start_url": exploration.get("start_url"),
        "read_only": exploration.get("read_only", True),
        "allow_form_submit": exploration.get("allow_form_submit", False),
        "allowed_form_actions": exploration.get("allowed_form_actions") or [],
        "pages": evidence,
        "edges": (exploration.get("edges") or [])[:200],
    }
    return f"""
你是资深 Web 自动化测试工程师。下面是 Playwright 对被测站点的真实探索证据，
请据此生成最多 {max_cases} 条**可直接自动执行**的测试用例。

【严格输出】只输出 JSON 数组，不要 Markdown、解释、注释或代码围栏，不要输出多余字段。
每项只包含以下 7 个字段：
title、module、priority、test_type、preconditions、expected、executable_steps。
priority 只能是 P0/P1/P2/P3；test_type 只能是：功能测试、性能测试、兼容性测试、
易用性测试、安全性测试、稳定性测试、接口测试、自动化测试。
executable_steps 是给 Playwright 执行的步骤数组（人看的步骤由系统自动生成，不用你写）。

【executable_steps 硬性规则】
1. 每个步骤是对象，字段固定为 action/element/value/expected。
2. action 只能是：navigate、click、input、assert、wait、screenshot。
3. navigate 的 element 必须是探索证据里的完整 URL；第一条步骤必须是 navigate。
4. click / input 的 element 必须**原样复制**探索证据 elements 里的 locator 字段，
   不允许自行改写、拼接或发明 CSS 选择器、XPath。
5. assert 的 element 只允许用 title、url 或探索证据里的 locator；
   expected 为要包含的文本片段。assert 一定要能在被测页面上被验证。
6. 只有当 allow_form_submit=true 时，才允许生成探索中真实执行过的低风险表单操作；
   删除、退出、导出、上传、支付、审批等危险操作永远禁止。

【用例设计规则（参考测试用例生成Prompt模板.md）】
1. 功能测试必须覆盖四个子类：正常流（导航+点击+断言）、异常流（非法输入+不崩溃）、
   边界值（空列表/单条数据/超长内容）、业务规则（状态/权限/数据一致性）。
2. 每页至少 1 条性能测试和 1 条易用性测试；每模块至少 1 条稳定性测试。
3. 全局补充兼容性 3-5 条、安全性 1-2 条、自动化回归 2-3 条。
4. 优先设计多步流程：navigate -> click -> （必要的 wait）-> assert，验证页面跳转或可见文本。
5. 只使用探索证据中出现的页面、URL 和 locator；不要编造页面、接口、账号或数据。
6. 步骤要能独立执行：每条用例都必须自己先 navigate 起始页面。
7. 不要重复规则层已经覆盖的"单页可访问""单个入口可点击"用例，聚焦组合场景。
8. 预期结果必须可验证，不要写"符合需求"这类无法判断的描述。
9. P0 只给核心主流程，控制在总量 10% 以内；同类入口只保留代表性用例。
10. 输出务必精简：步骤字段留空时写空字符串，不要写 null，不要重复解释。

【探索证据】
{json.dumps(payload, ensure_ascii=False)}
""".strip()


def _web_ai_case_safe(executable: List[Dict[str, str]], locators: set,
                      input_locators: set) -> bool:
    """模型给的步骤必须只用证据里出现过的定位器，防止误点到写操作元素。"""
    for step in executable:
        action = step["action"]
        if action in {"click", "input"}:
            if step["element"] not in locators:
                return False
        elif action == "assert":
            if step["element"].lower() in _WEB_ASSERT_KEYWORDS:
                continue
            if step["element"] not in locators:
                return False
            # 输入框没有 inner text，Runner 的文本断言必然失败，这类断言直接判不可用。
            if step["element"] in input_locators:
                return False
    return True


def generate_web_cases_from_exploration(exploration: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把 Web 探索产物转成可直接执行的 web_cases：规则用例打底，AI 补充组合场景。

    只有能产出合法 executable_steps 的用例才会返回，保证"生成了就能跑"。
    """
    cases = _web_rule_cases(exploration)
    if not cases:
        return []

    evidence = _web_evidence(exploration)
    locators = {element["locator"] for page in evidence for element in page["elements"]}
    input_locators = {element["locator"] for page in evidence for element in page["elements"]
                      if element.get("kind") == "input"}
    # 先由规则层生成可执行基线，再让 AI 按 Web 版 Prompt 逐条优化人读内容；
    # executable 保持规则层的合法 locator，不允许模型改写成幻觉选择器。
    cases = _optimize_cases_with_ai(cases, evidence, "Web")
    # GLM 生成带 executable_steps 的 JSON 较慢，控制条数并把超时放宽到 60 秒，
    # 否则 30 秒必定超时、整批 AI 用例被静默丢弃。
    prompt = _web_exploration_prompt(exploration, evidence, max_cases=8)
    existing = {case["title"] for case in cases}

    for item in _llm_json_list(prompt, max_cases=8, timeout=60):
        title = str(item.get("title") or "").strip()
        if not title or title in existing:
            continue
        executable = _normalize_web_executable(item.get("executable_steps"))
        # 一条断言都没有的用例等于没验证任何东西，不要写进用例库。
        if not executable or not any(step["action"] == "assert" for step in executable):
            continue
        if not _web_ai_case_safe(executable, locators, input_locators):
            continue
        raw_steps = item.get("steps", [])
        if isinstance(raw_steps, str):
            raw_steps = [line.strip() for line in raw_steps.splitlines() if line.strip()]
        if not isinstance(raw_steps, list) or not raw_steps:
            raw_steps = [f"{index + 1}. {step['action']} {step['element']}".strip()
                         for index, step in enumerate(executable)]
        raw_steps = [str(step) for step in raw_steps]
        priority = str(item.get("priority", "P2")).strip()
        test_type = str(item.get("test_type", "功能测试")).strip()
        existing.add(title)
        cases.append(_web_case(
            title, str(item.get("module") or "AI生成"),
            priority if priority in ALLOWED_PRIORITIES else "P2",
            test_type if test_type in ALLOWED_TEST_TYPES else "功能测试",
            raw_steps, str(item.get("expected") or "以需求文档为准"),
            str(item.get("source_ref") or "Web探索"),
            executable,
            preconditions=str(item.get("preconditions") or "测试环境可访问被测站点，测试数据已准备"),
            metadata={"generated_by": "llm", "prompt_version": "web-exploration-v2"},
        ))
    return cases


def _endpoint_anchors(content: str):
    lines = content.splitlines()
    pattern = re.compile(r"^`(POST|GET|PUT|DELETE|PATCH)\s+(\S+)`\s*$", re.I)
    last_heading = "接口"
    for index, line in enumerate(lines):
        heading = re.match(r"^#{2,4}\s+(.+)$", line.strip())
        if heading:
            last_heading = heading.group(1).strip()
        match = pattern.match(line.strip())
        if match:
            yield {
                "section": last_heading,
                "method": match.group(1).upper(),
                "path": match.group(2),
                "line": index + 1,
            }


def parse_api_inventory(content: str) -> Dict[str, Any]:
    endpoints = list(_endpoint_anchors(content))
    return {
        "source": "markdown",
        "counts": {"total": len(endpoints)},
        "endpoints": endpoints,
        "raw_length": len(content),
    }


def generate_api_cases(content: str) -> Dict[str, Any]:
    """生成接口文档的可审核用例集，保留文档章节和行号追溯信息。"""
    inventory = parse_api_inventory(content)
    cases: List[Dict[str, Any]] = []
    for endpoint in inventory["endpoints"]:
        label = f"[{endpoint['section']}] {endpoint['method']} {endpoint['path']}"
        ref = f"第{endpoint['line']}行"
        cases.append(_case(f"{label}正常流程", endpoint["section"], "P0", "功能测试",
                           [f"准备接口环境", f"发送 {endpoint['method']} {endpoint['path']} 请求"],
                           "HTTP 响应正常，业务 code 和 data 结构符合接口文档。", ref))
        cases.append(_case(f"{label}必填参数缺失", endpoint["section"], "P1", "功能测试",
                           [f"发送 {endpoint['method']} {endpoint['path']} 请求", "逐一移除一个必填参数"],
                           "服务拒绝请求并返回明确业务错误，不返回错误业务数据。", ref))
        cases.append(_case(f"{label}未登录访问", endpoint["section"], "P1", "安全性测试",
                           ["移除登录令牌", f"发送 {endpoint['method']} {endpoint['path']} 请求"],
                           "鉴权失败，返回未登录或令牌无效提示。", ref))
        cases.append(_case(f"{label}资源不存在", endpoint["section"], "P2", "功能测试",
                           [f"构造 {endpoint['path']} 请求", "填入不存在的资源标识"],
                           "返回明确的资源不存在错误，不产生 5xx 异常。", ref))
    return {"inventory": inventory, "cases": cases}


def cases_to_csv(cases: List[Dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=[
        "标题", "目录", "负责人", "前置条件", "步骤描述", "预期结果",
        "关联工作项", "优先级", "类型"
    ], lineterminator="\n")
    writer.writeheader()
    for item in cases:
        writer.writerow({
            "标题": item["title"], "目录": item["module"],
            "负责人": item.get("owner", ""), "前置条件": item["preconditions"],
            "步骤描述": "\n".join(item["steps"]), "预期结果": item["expected"],
            "关联工作项": item.get("related_work_item", ""),
            "优先级": item["priority"], "类型": item["test_type"],
        })
    return output.getvalue()


def cases_to_markdown(cases: List[Dict[str, Any]]) -> str:
    """导出与云效一致的 9 列 Markdown 表格，便于评审和复制。"""
    headers = ["标题", "目录", "负责人", "前置条件", "步骤描述", "预期结果", "关联工作项", "优先级", "类型"]
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    for item in cases:
        values = [
            item.get("title", ""), item.get("module", ""), item.get("owner", ""),
            item.get("preconditions", ""), "<br>".join(str(step) for step in item.get("steps", [])),
            item.get("expected", ""), item.get("related_work_item", ""),
            item.get("priority", "P2"), item.get("test_type", "功能测试"),
        ]
        escaped = [str(value).replace("|", "\\|").replace("\n", "<br>") for value in values]
        lines.append("| " + " | ".join(escaped) + " |")
    return "\n".join(lines) + "\n"


def inventory_to_openapi(inventory: Dict[str, Any]) -> Dict[str, Any]:
    """生成可导入工具的 OpenAPI 3.0 基础模型。"""
    paths: Dict[str, Any] = {}
    for endpoint in inventory.get("endpoints", []):
        path = endpoint["path"]
        method = endpoint["method"].lower()
        paths.setdefault(path, {})[method] = {
            "summary": endpoint.get("section", "接口"),
            "description": f"来源文档第 {endpoint.get('line', '-')} 行",
            "responses": {"200": {"description": "业务响应，具体断言以接口文档为准"}},
        }
    return {
        "openapi": "3.0.3",
        "info": {"title": "LYAITEST 生成接口", "version": "1.0.0"},
        "servers": [{"url": "{{baseUrl}}"}],
        "paths": paths,
    }


def cases_to_postman(cases: List[Dict[str, Any]], inventory: Dict[str, Any]) -> Dict[str, Any]:
    """生成 Postman Collection v2.1 基础集合，供 Apifox 导入后继续完善。"""
    endpoints = {(item["method"], item["path"]): item for item in inventory.get("endpoints", [])}
    items = []
    for case in cases:
        source = case.get("source_ref", "")
        endpoint = next((item for item in endpoints.values() if str(item.get("line")) in source), None)
        method = endpoint.get("method", "GET") if endpoint else "GET"
        path = endpoint.get("path", "/") if endpoint else "/"
        path_parts = [part for part in path.strip("/").split("/") if part]
        items.append({
            "name": case["title"],
            "request": {
                "method": method,
                "header": [],
                "url": {"raw": "{{baseUrl}}" + path, "host": ["{{baseUrl}}"], "path": path_parts},
            },
            "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": [
                "pm.test('HTTP 请求已返回', function () { pm.expect(pm.response.code).to.be.below(600); });"
            ]}}],
        })
    return {
        "info": {
            "name": "LYAITEST 接口测试用例集合",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "variable": [{"key": "baseUrl", "value": "http://localhost:8000"}],
        "item": items,
    }


def json_default(value):
    return str(value)
