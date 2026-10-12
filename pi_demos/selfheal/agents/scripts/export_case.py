"""把 web_cases 的「声明式步骤」导出成一个可运行的 Playwright 测试骨架。

这是现状里缺的那一环：平台能存步骤（navigate/input/click/assert），
也能执行步骤，但从来没人把它变成「可执行、可进版本库的脚本」。
本脚本做机械翻译，产出的骨架注定跑不通（定位器/断言都可能不对），
再交给 pi-agent 自愈到通过 —— 这就是本 demo 要演示的闭环。

用法：
    python agents/scripts/export_case.py --case-id <web_case_id> [--user-id <uid>]
    python agents/scripts/export_case.py --from-json agents/fixtures/web-case-001/case.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

AGENTS_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = AGENTS_ROOT.parent
FIXTURE_DIR = AGENTS_ROOT / "fixtures" / "web-case-001"
DEFAULT_DB = PROJECT_ROOT / "lyaitest.db"

HEADER = '''"""由 case.json 自动生成的 Playwright 测试骨架。

机械翻译自声明式步骤，尚未验证 —— 定位器与断言都可能与实际页面不符。
预期行为以 case.json 为准，被测页面是事实基准。
"""
from playwright.sync_api import Page, expect


def test_{func_name}(page: Page, base_url: str):
'''

# 步骤动作 → 代码行的映射；element 若为空则退化为注释，避免生成非法代码
STEP_TEMPLATES = {
    "navigate": '    page.goto(base_url)  # navigate: {raw_element}',
    "input": '    page.fill("{element}", "{value}")',
    "click": '    page.click("{element}")',
    "assert": '    expect(page.locator("{element}")).to_have_text("{expected}")',
    "wait": '    page.wait_for_timeout({value})',
}


def load_from_db(case_id: str, user_id: str | None, db_path: Path) -> dict:
    if not db_path.exists():
        sys.exit(f"数据库不存在：{db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sql = "SELECT id, name, description, steps FROM web_cases WHERE id = ?"
        params: list[str] = [case_id]
        # 按归属过滤，避免跨用户读到别人的用例
        if user_id:
            sql += " AND user_id = ?"
            params.append(user_id)
        row = conn.execute(sql, params).fetchone()
    finally:
        conn.close()
    if row is None:
        hint = "（或该用例不属于当前 user_id）" if user_id else ""
        sys.exit(f"未找到 web_case：{case_id}{hint}")
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"] or "",
        "steps": json.loads(row["steps"] or "[]"),
    }


def render_skeleton(case: dict) -> str:
    func_name = "".join(ch if ch.isalnum() else "_" for ch in str(case["id"])).strip("_")

    lines = [HEADER.format(func_name=func_name)]
    for step in case.get("steps", []):
        action = str(step.get("action", "")).strip().lower()
        element = str(step.get("element", "")).replace('"', '\\"')
        value = str(step.get("value", "")).replace('"', '\\"')
        expected = str(step.get("expected", "")).replace('"', '\\"')

        template = STEP_TEMPLATES.get(action)
        if template is None:
            lines.append(f"    # 未识别的动作，请手工补充：{json.dumps(step, ensure_ascii=False)}")
            continue
        if action in {"input", "click", "assert"} and not element:
            lines.append(f"    # 该步骤缺少 element，请手工补充：{json.dumps(step, ensure_ascii=False)}")
            continue
        lines.append(
            template.format(element=element, value=value, expected=expected, raw_element=element)
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--case-id", help="web_cases 表中的用例 id")
    src.add_argument("--from-json", help="直接读一个 case.json（无需数据库）")
    parser.add_argument("--user-id", help="按归属过滤，防止跨用户读取")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 路径")
    parser.add_argument("--outdir", help="输出目录，默认 agents/workdir/case-<id>")
    args = parser.parse_args()

    if args.from_json:
        case = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        label = Path(args.from_json).parent.name
    else:
        case = load_from_db(args.case_id, args.user_id, Path(args.db))
        label = f"case-{case['id']}"

    workdir = Path(args.outdir) if args.outdir else AGENTS_ROOT / "workdir" / label
    (workdir / "tests").mkdir(parents=True, exist_ok=True)

    # 复用模板里的通用夹具（本地静态站点 + playwright fixture）
    shutil.copy2(FIXTURE_DIR / "conftest.py", workdir / "conftest.py")
    (workdir / "case.json").write_text(
        json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (workdir / "tests" / "test_web_case.py").write_text(
        render_skeleton(case), encoding="utf-8"
    )

    print(f"工作目录已生成：{workdir}")
    print(f"  case.json                ← 用例预期（{case['name']}）")
    print("  conftest.py              ← 本地静态站点 + playwright 夹具")
    print("  tests/test_web_case.py   ← 自动生成的骨架，尚未验证")
    print()
    print("注意：导出的是骨架，还没有对应的被测页面。")
    print(f"请把目标页面放到 {workdir / 'site'} 下（或改 base_url fixture 指向真实站点），")
    print("然后运行：npm run selfheal -- --workdir " + str(workdir))


if __name__ == "__main__":
    main()