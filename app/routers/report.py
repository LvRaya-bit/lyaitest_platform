# app/routers/report.py
from fastapi import APIRouter, HTTPException, Depends
from typing import List
from app.services.report_service import (
    delete_report,
    get_all_reports,
    get_reports_by_session,
    get_report_by_id,
)
from app.dependencies.auth import get_current_user
from app.models.report import TestReport

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

@router.get("/", response_model=List[TestReport])
async def list_reports(current_user: dict = Depends(get_current_user)):
    """获取当前用户的所有测试报告"""
    return get_all_reports(user_id=current_user["id"])

@router.get("/session/{session_id}", response_model=List[TestReport])
async def get_reports_by_session_route(session_id: str, current_user: dict = Depends(get_current_user)):
    """获取指定会话的所有测试报告"""
    reports = get_reports_by_session(session_id, user_id=current_user["id"])
    if not reports:
        raise HTTPException(status_code=404, detail="未找到该会话的报告")
    return reports

@router.get("/{report_id}", response_model=TestReport)
async def get_report(report_id: str, current_user: dict = Depends(get_current_user)):
    """根据ID获取测试报告"""
    report = get_report_by_id(report_id, user_id=current_user["id"])
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    return report


@router.delete("/{report_id}")
async def delete_report_route(report_id: str, current_user: dict = Depends(get_current_user)):
    """删除一条执行结果；只清理平台记录，不动本机 Runner 上的执行痕迹。"""
    if not delete_report(report_id, user_id=current_user["id"]):
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"message": "删除成功"}