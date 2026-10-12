"""APP 功能文档、App 探索结果、接口文档的测试用例生成 API。"""
import json
import uuid
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from app.database import get_connection
from app.dependencies.auth import get_current_user
from app.services.case_generation import (
    cases_to_csv,
    cases_to_markdown,
    case_matrix,
    generate_api_cases,
    generate_app_cases_from_doc,
    generate_app_cases_from_exploration,
    generate_web_cases_from_exploration,
    cases_to_postman,
    inventory_to_openapi,
)
from app.services.local_runner_client import RunnerUnavailable, runner_get_job
from app.services.web_codegen import generate_pytest_bundle
from app.services.web_run_service import create_running_report, submit_web_run


router = APIRouter(prefix="/api/v1/case-generation", tags=["测试用例生成"])


def _now() -> str:
    return datetime.now().isoformat()


def _json(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _save_case_set(user_id: str, name: str, source_type: str, source_ref: str,
                   cases: List[Dict[str, Any]], metadata: Dict[str, Any] | None = None):
    set_id = str(uuid.uuid4())[:8]
    now = _now()
    conn = get_connection()
    conn.execute(
        """INSERT INTO generated_case_sets
        (id, user_id, name, source_type, source_ref, status, case_count, metadata, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (set_id, user_id, name, source_type, source_ref, "draft", len(cases),
         json.dumps(metadata or {}, ensure_ascii=False), now, now),
    )
    for case in cases:
        conn.execute(
            """INSERT INTO generated_test_cases
            (id, user_id, set_id, title, module, priority, test_type, preconditions,
             steps, expected, source_ref, automation_status, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                case.get("id", str(uuid.uuid4())[:8]), user_id, set_id,
                case.get("title", "未命名用例"), case.get("module", "未分类"),
                case.get("priority", "P1"), case.get("test_type", "功能测试"),
                case.get("preconditions", ""), json.dumps(case.get("steps", []), ensure_ascii=False),
                case.get("expected", ""), case.get("source_ref", source_ref),
                case.get("automation_status", "draft"),
                json.dumps(case.get("metadata", {}), ensure_ascii=False), now, now,
            ),
        )
    conn.commit()
    conn.close()
    return set_id


@router.post("/function-document")
async def generate_from_function_document(
    file: UploadFile = File(...),
    name: str = Form("APP功能文档用例集"),
    current_user: dict = Depends(get_current_user),
):
    filename = (file.filename or "").lower()
    if not filename.endswith((".md", ".markdown", ".txt")):
        raise HTTPException(status_code=400, detail="第一版仅支持 Markdown/TXT 文档")
    content = (await file.read()).decode("utf-8", errors="replace")
    cases = generate_app_cases_from_doc(content)
    set_id = _save_case_set(
        current_user["id"], name, "app_function_doc", file.filename or "", cases,
        {
            "filename": file.filename,
            "prompt_version": "template-v3-rule-ai-optimize",
            "generation_method": "规则覆盖 + AI逐条优化 + AI补充（可选）",
        },
    )
    return {"id": set_id, "source_type": "app_function_doc", "case_count": len(cases)}


@router.post("/exploration/{exploration_id}")
def generate_from_exploration(exploration_id: str, name: str = "APP探索用例集", current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM app_exploration_tasks WHERE id = ? AND user_id = ?",
        (exploration_id, current_user["id"]),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="探索任务不存在")
    task = dict(row)
    if task.get("runner_task_id") and task.get("status") in {"starting", "running"}:
        try:
            remote = runner_get_job(task["runner_task_id"])
            if remote.get("status") != "completed":
                raise HTTPException(status_code=409, detail="探索任务尚未完成")
            exploration = remote.get("result", {})
        except RunnerUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    else:
        exploration = _json(task.get("summary"), {})
    cases = generate_app_cases_from_exploration(exploration)
    matrix = case_matrix(cases)
    # 同一探索任务重复点「生成用例」时覆盖上一次结果，避免用例集不断累积。
    replaced = _purge_app_exploration_artifacts(current_user["id"], exploration_id)
    set_id = _save_case_set(
        current_user["id"], name, "app_exploration", exploration_id, cases,
        {
            "exploration_id": exploration_id,
            "prompt_version": "template-v3-rule-ai-optimize",
            "generation_method": "规则覆盖 + AI逐条优化 + AI补充（可选）",
            "case_matrix": matrix,
        },
    )
    return {"id": set_id, "source_type": "app_exploration", "case_count": len(cases),
            "case_matrix": matrix, "ai_case_count": sum(
                1 for case in cases if case.get("metadata", {}).get("generated_by") == "llm"),
            "replaced": replaced}


def _save_web_cases(user_id: str, name: str, exploration_id: str,
                    cases: List[Dict[str, Any]]) -> List[str]:
    """把生成结果写入 web_cases，steps 存可执行步骤数组，生成即可在 Web 自动化里执行。"""
    conn = get_connection()
    now = _now()
    case_ids = []
    for case in cases:
        case_id = str(uuid.uuid4())[:8]
        description = "\n".join([
            f"来源：Web 自动探索 {exploration_id}（页面 {case.get('source_ref') or '-'}）",
            f"模块：{case.get('module', '-')}　优先级：{case.get('priority', '-')}　类型：{case.get('test_type', '-')}",
            f"前置条件：{case.get('preconditions', '')}",
            f"预期结果：{case.get('expected', '')}",
            "生成方式：" + ("AI 增强" if case.get("metadata", {}).get("generated_by") == "llm" else "规则生成"),
        ])
        conn.execute(
            """INSERT INTO web_cases (id, user_id, name, description, steps, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (case_id, user_id, case["title"], description,
             json.dumps(case.get("executable", []), ensure_ascii=False), now, now),
        )
        case_ids.append(case_id)
    conn.commit()
    conn.close()
    return case_ids


def _purge_app_exploration_artifacts(user_id: str, exploration_id: str) -> Dict[str, int]:
    """删除同一 APP 探索任务上一次生成的用例集，让重复生成表现为覆盖而不是不断累积。"""
    conn = get_connection()
    set_rows = conn.execute(
        """SELECT id FROM generated_case_sets
        WHERE user_id = ? AND source_type = 'app_exploration' AND source_ref = ?""",
        (user_id, exploration_id),
    ).fetchall()
    set_ids = [row["id"] for row in set_rows]
    for set_id in set_ids:
        conn.execute("DELETE FROM generated_test_cases WHERE set_id = ?", (set_id,))
        conn.execute("DELETE FROM generated_case_sets WHERE id = ?", (set_id,))
    conn.commit()
    conn.close()
    return {"case_sets": len(set_ids)}


def _purge_web_exploration_artifacts(user_id: str, exploration_id: str) -> Dict[str, int]:
    """删除同一探索任务上一次生成的结果，让重复生成表现为覆盖而不是不断累积。

    web_cases 的 description 以「来源：Web 自动探索 <探索任务id>（」开头，据此精确定位；
    用例生成模块则按 source_type + source_ref 定位对应用例集。
    """
    conn = get_connection()
    web_rows = conn.execute(
        "SELECT id FROM web_cases WHERE user_id = ? AND description LIKE ?",
        (user_id, f"来源：Web 自动探索 {exploration_id}（%"),
    ).fetchall()
    web_ids = [row["id"] for row in web_rows]
    if web_ids:
        conn.executemany("DELETE FROM web_cases WHERE id = ?", [(case_id,) for case_id in web_ids])

    set_rows = conn.execute(
        """SELECT id FROM generated_case_sets
        WHERE user_id = ? AND source_type = 'web_exploration' AND source_ref = ?""",
        (user_id, exploration_id),
    ).fetchall()
    set_ids = [row["id"] for row in set_rows]
    for set_id in set_ids:
        conn.execute("DELETE FROM generated_test_cases WHERE set_id = ?", (set_id,))
        conn.execute("DELETE FROM generated_case_sets WHERE id = ?", (set_id,))
    conn.commit()
    conn.close()
    return {"web_cases": len(web_ids), "case_sets": len(set_ids)}


@router.post("/web-exploration/{exploration_id}")
def generate_from_web_exploration(exploration_id: str, name: str = "",
                                  current_user: dict = Depends(get_current_user)):
    """把网页自动探索的结果转成可直接执行的用例，直接写入 web_cases 表。"""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM web_exploration_tasks WHERE id = ? AND user_id = ?",
        (exploration_id, current_user["id"]),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="探索任务不存在")

    task = dict(row)
    if task.get("status") in {"starting", "running"}:
        if not task.get("runner_task_id"):
            raise HTTPException(status_code=409, detail="探索任务尚未完成")
        try:
            remote = runner_get_job(task["runner_task_id"])
        except RunnerUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if remote.get("status") != "completed":
            raise HTTPException(status_code=409, detail="探索任务尚未完成")
        exploration = remote.get("result") or {}
    else:
        exploration = _json(task.get("summary"), {})

    if not exploration.get("pages"):
        raise HTTPException(status_code=400, detail="探索结果为空，无法生成用例，请重新探索")

    cases = [case for case in generate_web_cases_from_exploration(exploration) if case.get("executable")]
    if not cases:
        raise HTTPException(status_code=400, detail="探索结果中没有可执行步骤，无法生成用例")

    set_name = name or f"Web探索用例-{exploration_id}"
    # 同一探索任务重复点「生成用例」时覆盖上一次结果，避免用例不断累积。
    replaced = _purge_web_exploration_artifacts(current_user["id"], exploration_id)
    web_case_ids = _save_web_cases(current_user["id"], set_name, exploration_id, cases)
    # 同时写入「用例生成」模块，与 APP 探索用例共用同一套管理入口；
    # 每条生成用例记录对应的 web_cases id，供批量执行时取可执行步骤。
    for case, web_case_id in zip(cases, web_case_ids):
        case.setdefault("metadata", {})["web_case_id"] = web_case_id

    matrix = case_matrix(cases)
    set_id = _save_case_set(
        current_user["id"], set_name, "web_exploration", exploration_id, cases,
        {
            "exploration_id": exploration_id,
            "prompt_version": "template-v3-rule-ai-optimize",
            "generation_method": "规则覆盖 + AI逐条优化 + AI补充（可选）",
            "case_matrix": matrix,
            "web_case_ids": web_case_ids,
        },
    )
    return {
        "exploration_id": exploration_id,
        "id": set_id,
        "set_id": set_id,
        "case_count": len(cases),
        "case_ids": web_case_ids,
        "case_matrix": matrix,
        "replaced": replaced,
        "ai_case_count": sum(1 for c in cases if c.get("metadata", {}).get("generated_by") == "llm"),
    }


def _generated_cases_with_web_link(case_ids: List[str], user_id: str) -> List[Dict[str, Any]]:
    """按 generated_test_cases 的 id 取用例，并解析出关联 web_cases 的可执行步骤。"""
    conn = get_connection()
    placeholders = ",".join("?" for _ in case_ids)
    rows = conn.execute(
        f"SELECT * FROM generated_test_cases WHERE id IN ({placeholders}) AND user_id = ?",
        (*case_ids, user_id),
    ).fetchall()
    result = []
    for row in rows:
        case = dict(row)
        case["metadata"] = _json(case.get("metadata"), {})
        web_case_id = case["metadata"].get("web_case_id")
        steps: List[Any] = []
        if web_case_id:
            web_row = conn.execute(
                "SELECT steps FROM web_cases WHERE id = ? AND user_id = ?",
                (web_case_id, user_id),
            ).fetchone()
            if web_row:
                steps = _json(web_row["steps"], [])
        case["executable_steps"] = steps
        result.append(case)
    conn.close()
    return result


@router.post("/cases/batch-delete")
def batch_delete_generated_cases(data: Dict[str, Any] = Body(...),
                                 current_user: dict = Depends(get_current_user)):
    """批量删除生成用例；Web 探索来源的用例会同步删掉 Web 自动化里的可执行副本，避免留下孤儿用例。"""
    case_ids = [str(item) for item in (data.get("case_ids") or [])]
    if not case_ids:
        raise HTTPException(status_code=400, detail="请选择要删除的用例")

    cases = _generated_cases_with_web_link(case_ids, current_user["id"])
    if not cases:
        raise HTTPException(status_code=404, detail="测试用例不存在")

    web_ids = [case["metadata"]["web_case_id"] for case in cases if case["metadata"].get("web_case_id")]
    set_ids = {case["set_id"] for case in cases}
    placeholders = ",".join("?" for _ in cases)

    conn = get_connection()
    conn.execute(
        f"DELETE FROM generated_test_cases WHERE id IN ({placeholders}) AND user_id = ?",
        (*[case["id"] for case in cases], current_user["id"]),
    )
    if web_ids:
        web_placeholders = ",".join("?" for _ in web_ids)
        conn.execute(
            f"DELETE FROM web_cases WHERE id IN ({web_placeholders}) AND user_id = ?",
            (*web_ids, current_user["id"]),
        )
    # 同步刷新用例集条数，避免列表页仍显示删除前的数量。
    for set_id in set_ids:
        conn.execute(
            """UPDATE generated_case_sets
            SET case_count = (SELECT COUNT(*) FROM generated_test_cases WHERE set_id = ?), updated_at = ?
            WHERE id = ? AND user_id = ?""",
            (set_id, _now(), set_id, current_user["id"]),
        )
    conn.commit()
    conn.close()
    return {"deleted": len(cases), "deleted_web_cases": len(web_ids)}


@router.post("/cases/batch-run")
def batch_run_generated_cases(data: Dict[str, Any] = Body(...),
                              current_user: dict = Depends(get_current_user)):
    """批量执行生成用例：把所选用例的可执行步骤合并成一次真实执行提交给本机 Runner。

    目前只有 Web 探索来源的用例带可执行步骤，其余来源（文档生成）会作为跳过项返回。
    """
    case_ids = [str(item) for item in (data.get("case_ids") or [])]
    if not case_ids:
        raise HTTPException(status_code=400, detail="请选择要执行的用例")
    headless = bool(data.get("headless", True))

    cases = _generated_cases_with_web_link(case_ids, current_user["id"])
    if not cases:
        raise HTTPException(status_code=404, detail="测试用例不存在")

    runnable = [{"name": case["title"], "steps": case["executable_steps"]}
                for case in cases if case["executable_steps"]]
    skipped = [case["title"] for case in cases if not case["executable_steps"]]
    if not runnable:
        raise HTTPException(status_code=400,
                            detail="所选用例都没有可执行步骤（目前仅 Web 探索来源的用例可直接执行）")

    report_id = str(uuid.uuid4())[:8]
    report_name = f"用例生成批量执行（{len(runnable)} 条）"
    total_steps = sum(len(case["steps"]) for case in runnable)
    try:
        submit_web_run(report_id, runnable, headless=headless)
    except RunnerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    create_running_report(current_user["id"], "web_batch", report_name, total_steps, report_id=report_id)

    return {
        "report_id": report_id,
        "runner_job_id": report_id,
        "test_name": report_name,
        "case_count": len(runnable),
        "total_steps": total_steps,
        "skipped": skipped,
        "headless": headless,
        "status": "running",
    }


@router.post("/api-document")
async def generate_from_api_document(
    file: UploadFile = File(...),
    name: str = Form("接口文档测试用例集"),
    current_user: dict = Depends(get_current_user),
):
    filename = (file.filename or "").lower()
    if not filename.endswith((".md", ".markdown", ".txt")):
        raise HTTPException(status_code=400, detail="第一版仅支持 Markdown/TXT 接口文档")
    content = (await file.read()).decode("utf-8", errors="replace")
    generated = generate_api_cases(content)
    set_id = _save_case_set(
        current_user["id"], name, "api_document", file.filename or "", generated["cases"],
        {"filename": file.filename, "inventory": generated["inventory"]},
    )
    return {
        "id": set_id,
        "source_type": "api_document",
        "case_count": len(generated["cases"]),
        "inventory": generated["inventory"],
    }


@router.get("/sets")
def list_case_sets(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM generated_case_sets WHERE user_id = ? ORDER BY created_at DESC",
        (current_user["id"],),
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["metadata"] = _json(item.get("metadata"), {})
        result.append(item)
    return result


@router.get("/sets/{set_id}")
def get_case_set(set_id: str, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    row = conn.execute("SELECT * FROM generated_case_sets WHERE id = ? AND user_id = ?", (set_id, current_user["id"])).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="用例集不存在")
    cases = conn.execute("SELECT * FROM generated_test_cases WHERE set_id = ? AND user_id = ? ORDER BY created_at", (set_id, current_user["id"])).fetchall()
    conn.close()
    result = dict(row)
    result["metadata"] = _json(result.get("metadata"), {})
    result["cases"] = []
    for case in cases:
        item = dict(case)
        item["steps"] = _json(item.get("steps"), [])
        item["metadata"] = _json(item.get("metadata"), {})
        result["cases"].append(item)
    return result


@router.delete("/sets/{set_id}")
def delete_case_set(set_id: str, current_user: dict = Depends(get_current_user)):
    """删除整个生成用例集；Web 探索来源的用例集会连带清理 Web 自动化里的可执行副本。"""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM generated_case_sets WHERE id = ? AND user_id = ?",
        (set_id, current_user["id"]),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="用例集不存在")

    case_rows = conn.execute(
        "SELECT metadata FROM generated_test_cases WHERE set_id = ?", (set_id,)
    ).fetchall()
    web_ids = []
    for case_row in case_rows:
        web_case_id = _json(case_row["metadata"], {}).get("web_case_id")
        if web_case_id:
            web_ids.append(web_case_id)

    conn.execute("DELETE FROM generated_test_cases WHERE set_id = ?", (set_id,))
    conn.execute("DELETE FROM generated_case_sets WHERE id = ? AND user_id = ?",
                 (set_id, current_user["id"]))
    if web_ids:
        placeholders = ",".join("?" for _ in web_ids)
        conn.execute(
            f"DELETE FROM web_cases WHERE id IN ({placeholders}) AND user_id = ?",
            (*web_ids, current_user["id"]),
        )
    conn.commit()
    conn.close()
    return {"deleted_cases": len(case_rows), "deleted_web_cases": len(web_ids),
            "message": "用例集已删除"}


@router.patch("/cases/{case_id}")
def update_generated_case(case_id: str, data: Dict[str, Any], current_user: dict = Depends(get_current_user)):
    allowed = {"title", "module", "priority", "test_type", "preconditions", "steps", "expected", "automation_status"}
    updates, params = [], []
    for key, value in data.items():
        if key in allowed:
            updates.append(f"{key} = ?")
            params.append(json.dumps(value, ensure_ascii=False) if key == "steps" else value)
    if not updates:
        return {"message": "没有需要更新的字段"}
    updates.append("updated_at = ?")
    params.extend([_now(), case_id, current_user["id"]])
    conn = get_connection()
    cursor = conn.execute(f"UPDATE generated_test_cases SET {', '.join(updates)} WHERE id = ? AND user_id = ?", params)
    conn.commit()
    conn.close()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="测试用例不存在")
    return {"message": "测试用例更新成功"}


@router.get("/sets/{set_id}/export.csv", response_class=PlainTextResponse)
def export_case_set_csv(set_id: str, current_user: dict = Depends(get_current_user)):
    result = get_case_set(set_id, current_user)
    return cases_to_csv(result["cases"])


@router.get("/sets/{set_id}/export.pytest")
def export_case_set_pytest(set_id: str, current_user: dict = Depends(get_current_user)):
    """把审核后的 Web P0/P1 用例导出为 Pytest + Playwright + POM 文件包。"""
    result = get_case_set(set_id, current_user)
    selected = [case for case in result["cases"] if case.get("priority") in {"P0", "P1"}]
    linked = _generated_cases_with_web_link([case["id"] for case in selected], current_user["id"])
    files = generate_pytest_bundle(linked)
    if not files:
        raise HTTPException(status_code=400, detail="该用例集没有可导出的 P0/P1 Web 可执行用例")
    return {"set_id": set_id, "case_count": len(selected), "files": files}


@router.get("/sets/{set_id}/export.pytest.zip")
def export_case_set_pytest_zip(set_id: str, current_user: dict = Depends(get_current_user)):
    """下载 P0/P1 Pytest + Playwright + POM 工程压缩包。"""
    result = export_case_set_pytest(set_id, current_user)
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for filename, content in result["files"].items():
            archive.writestr(filename, content)
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="web-p0-p1-{set_id}.zip"'},
    )


@router.get("/sets/{set_id}/export.md", response_class=PlainTextResponse)
def export_case_set_markdown(set_id: str, current_user: dict = Depends(get_current_user)):
    """导出云效兼容的 9 列 Markdown 表格。"""
    result = get_case_set(set_id, current_user)
    return cases_to_markdown(result["cases"])


@router.get("/sets/{set_id}/export.openapi.json")
def export_case_set_openapi(set_id: str, current_user: dict = Depends(get_current_user)):
    result = get_case_set(set_id, current_user)
    inventory = result.get("metadata", {}).get("inventory", {})
    return JSONResponse(inventory_to_openapi(inventory))


@router.get("/sets/{set_id}/export.postman.json")
def export_case_set_postman(set_id: str, current_user: dict = Depends(get_current_user)):
    result = get_case_set(set_id, current_user)
    inventory = result.get("metadata", {}).get("inventory", {})
    return JSONResponse(cases_to_postman(result["cases"], inventory))
