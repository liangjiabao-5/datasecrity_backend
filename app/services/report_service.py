from __future__ import annotations

from pathlib import Path

from flask import current_app, g

from app.common.exceptions import BusinessError, NotFoundError
from app.common.pagination import paginate_query
from app.common.utils import utcnow
from app.extensions import SessionLocal
from app.models import (
    AssessmentTeamMember,
    BusinessSystem,
    EvaluationRecord,
    FileObject,
    ProjectAssessmentItem,
    ProjectBasicInfo,
    ProjectRiskSummaryRecord,
    ReportRecord,
    ReportTask,
)
from app.services import docx_report_service, file_service
from app.services.audit_service import audit
from app.services.project_service import get_project


DEFAULT_SECTIONS = [
    "BASIC_INFO",
    "PLAN",
    "SURVEY",
    "EVALUATION",
    "RISK_SUMMARY",
    "SUGGESTIONS",
]
REPORT_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def list_reports(project_id: str) -> dict:
    get_project(project_id)
    session = SessionLocal()
    query = (
        session.query(ReportRecord)
        .filter(ReportRecord.project_id == project_id, ReportRecord.deleted.is_(False))
        .order_by(ReportRecord.created_at.desc())
    )
    return paginate_query(query, _serialize_report)


def report_readiness(project_id: str) -> dict:
    project = get_project(project_id)
    session = SessionLocal()
    blockers = []
    warnings = []
    template_path = Path(current_app.config["REPORT_TEMPLATE_PATH"])
    if not template_path.exists():
        blockers.append({"code": "REPORT_TEMPLATE_NOT_FOUND", "message": f"报告模板不存在：{template_path.name}"})
    if not session.query(ProjectBasicInfo).filter_by(project_id=project_id, deleted=False).first():
        warnings.append({"code": "BASIC_INFO_EMPTY", "message": "项目基本信息未填写，报告相应位置将使用缺省文本。"})
    if not session.query(AssessmentTeamMember).filter_by(project_id=project_id, deleted=False).first():
        warnings.append({"code": "ASSESSMENT_TEAM_EMPTY", "message": "评估团队未填写，报告团队表将使用缺省行。"})
    if not session.query(BusinessSystem).filter_by(project_id=project_id, deleted=False).first():
        warnings.append({"code": "SURVEY_SYSTEM_EMPTY", "message": "业务和信息系统调研未填写，报告相应位置将使用缺省文本。"})
    if not session.query(ProjectAssessmentItem).filter_by(project_id=project_id, deleted=False).first():
        warnings.append({"code": "ASSESSMENT_ITEMS_EMPTY", "message": "项目尚未生成现场测评检查项快照。"})
    if not session.query(EvaluationRecord).filter_by(project_id=project_id, deleted=False).first():
        warnings.append({"code": "EVALUATION_EMPTY", "message": "现场测评记录为空，报告问题清单和评分结果可能为空。"})
    if not (
        session.query(ProjectRiskSummaryRecord)
        .filter_by(project_id=project_id, deleted=False, current=True)
        .first()
    ):
        warnings.append({"code": "RISK_SUMMARY_EMPTY", "message": "汇总分析结果为空，风险清单和处置建议将使用缺省行。"})
    return {
        "projectId": project.id,
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
    }


def generate_report(project_id: str, payload: dict) -> dict:
    project = get_project(project_id)
    readiness = report_readiness(project_id)
    if readiness["blockers"]:
        blocker = readiness["blockers"][0]
        raise BusinessError(blocker["code"], blocker["message"])

    report_name = str(payload.get("report_name") or f"{project.project_name}数据安全风险评估报告").strip()
    if not report_name:
        raise BusinessError("REPORT_NAME_REQUIRED", "Report name is required.")
    if len(report_name) > 200:
        raise BusinessError("REPORT_NAME_TOO_LONG", "Report name cannot exceed 200 characters.")
    report_type = payload.get("report_type") or "DATA_SECURITY_RISK_ASSESSMENT"
    if len(str(report_type)) > 80:
        raise BusinessError("REPORT_TYPE_TOO_LONG", "Report type cannot exceed 80 characters.")
    raw_sections = payload.get("selected_sections")
    sections = list(DEFAULT_SECTIONS) if raw_sections is None else raw_sections
    if not isinstance(sections, list) or not sections or not all(section in DEFAULT_SECTIONS for section in sections):
        raise BusinessError("INVALID_REPORT_SECTIONS", "Selected report sections are invalid.")
    sections = list(dict.fromkeys(sections))

    session = SessionLocal()
    creator = getattr(g, "current_user_id", "system")
    report = ReportRecord(
        project_id=project_id,
        report_name=report_name,
        report_type=report_type,
        status="PENDING",
        selected_sections=sections,
        created_by=creator,
        updated_by=creator,
    )
    task = ReportTask(
        project_id=project_id,
        report_id=report.id,
        status="PENDING",
        created_by=creator,
        updated_by=creator,
    )
    session.add(report)
    session.add(task)
    audit(
        "REPORT_GENERATE_REQUEST",
        "ReportRecord",
        report.id,
        after={"reportId": report.id, "reportTaskId": task.id, "selectedSections": sections},
    )
    session.commit()
    _enqueue_report_task(project_id, report.id, task.id)
    return _task_response(project_id, report.id, task.id, readiness["warnings"])


def retry_report(project_id: str, report_id: str) -> dict:
    get_project(project_id)
    session = SessionLocal()
    report = _get_report(session, project_id, report_id)
    if report.status != "FAILED":
        raise BusinessError("REPORT_RETRY_NOT_ALLOWED", "Only failed reports can be retried.")
    task = ReportTask(project_id=project_id, report_id=report.id, status="PENDING")
    session.add(task)
    report.status = "PENDING"
    report.error_message = None
    audit("REPORT_RETRY", "ReportRecord", report.id, after={"reportTaskId": task.id})
    session.commit()
    _enqueue_report_task(project_id, report.id, task.id)
    return _task_response(project_id, report.id, task.id)


def execute_report_task(project_id: str, report_id: str, task_id: str) -> None:
    session = SessionLocal()
    report = _get_report(session, project_id, report_id)
    task = _get_task(session, project_id, task_id)
    report.status = "RUNNING"
    report.error_message = None
    task.status = "RUNNING"
    task.error_message = None
    session.commit()

    try:
        project = get_project(project_id)
        content = docx_report_service.generate_document(
            session,
            project,
            current_app.config["REPORT_TEMPLATE_PATH"],
            report.selected_sections,
        )
        file_info = file_service.save_bytes(
            f"{_safe_file_stem(report.report_name)}_{report.id}.docx",
            content,
            REPORT_CONTENT_TYPE,
            biz_type="REPORT",
            project_id=project_id,
        )
        result = {
            "reportId": report.id,
            "fileId": file_info["fileId"],
            "downloadUrl": _report_download_url(project_id, report.id),
        }
        report.status = "SUCCESS"
        report.file_id = file_info["fileId"]
        report.generated_at = utcnow()
        task.status = "SUCCESS"
        task.result = result
        audit("REPORT_GENERATE_SUCCESS", "ReportRecord", report.id, after=result)
        session.commit()
    except Exception as exc:
        session.rollback()
        report = _get_report(session, project_id, report_id)
        task = _get_task(session, project_id, task_id)
        error_message = _error_summary(exc)
        report.status = "FAILED"
        report.error_message = error_message
        task.status = "FAILED"
        task.error_message = error_message
        task.result = {"reportId": report.id, "errorMessage": error_message}
        audit("REPORT_GENERATE_FAILED", "ReportRecord", report.id, after=task.result)
        session.commit()


def get_task(project_id: str, task_id: str) -> dict:
    get_project(project_id)
    return _get_task(SessionLocal(), project_id, task_id).to_dict()


def get_report_file(project_id: str, report_id: str) -> FileObject:
    get_project(project_id)
    session = SessionLocal()
    report = _get_report(session, project_id, report_id)
    if report.status != "SUCCESS" or not report.file_id:
        raise BusinessError("REPORT_NOT_READY", "Report file is not ready.")
    file = file_service.get_file(report.file_id)
    audit("REPORT_DOWNLOAD", "ReportRecord", report.id, after={"fileId": file.id})
    session.commit()
    return file


def delete_report(project_id: str, report_id: str) -> dict:
    get_project(project_id)
    session = SessionLocal()
    report = _get_report(session, project_id, report_id)
    if report.status in {"PENDING", "RUNNING"}:
        raise BusinessError("REPORT_DELETE_NOT_ALLOWED", "A pending or running report cannot be deleted.")
    before = report.to_dict()
    report.status = "DELETED"
    report.deleted = True
    if report.file_id:
        file = session.get(FileObject, report.file_id)
        if file:
            file.deleted = True
    audit("REPORT_DELETE", "ReportRecord", report.id, before=before, after={"status": "DELETED", "deleted": True})
    session.commit()
    return {"reportId": report.id}


def _enqueue_report_task(project_id: str, report_id: str, task_id: str) -> None:
    from app.tasks.report_tasks import enqueue

    enqueue(project_id, report_id, task_id)


def _task_response(project_id: str, report_id: str, task_id: str, warnings: list | None = None) -> dict:
    task = _get_task(SessionLocal(), project_id, task_id)
    data = {
        "reportTaskId": task.id,
        "reportId": report_id,
        "status": task.status,
        "taskStatusUrl": f"/api/v1/projects/{project_id}/reports/tasks/{task.id}",
    }
    if task.status == "SUCCESS":
        data["downloadUrl"] = _report_download_url(project_id, report_id)
    if warnings:
        data["warnings"] = warnings
    return data


def _serialize_report(report: ReportRecord) -> dict:
    data = report.to_dict()
    data["reportId"] = report.id
    data["reportTypeName"] = "数据安全风险评估报告" if report.report_type == "DATA_SECURITY_RISK_ASSESSMENT" else report.report_type
    if report.file_id and report.status == "SUCCESS":
        data["downloadUrl"] = _report_download_url(report.project_id, report.id)
    return data


def _get_report(session, project_id: str, report_id: str) -> ReportRecord:
    report = (
        session.query(ReportRecord)
        .filter(ReportRecord.project_id == project_id, ReportRecord.id == report_id, ReportRecord.deleted.is_(False))
        .first()
    )
    if not report:
        raise NotFoundError("Report not found.")
    return report


def _get_task(session, project_id: str, task_id: str) -> ReportTask:
    task = (
        session.query(ReportTask)
        .filter(ReportTask.project_id == project_id, ReportTask.id == task_id, ReportTask.deleted.is_(False))
        .first()
    )
    if not task:
        raise NotFoundError("Report task not found.")
    return task


def _safe_file_stem(value: str) -> str:
    result = "".join(character if character not in r'\/:*?"<>|' else "_" for character in value).strip()
    return result or "数据安全风险评估报告"


def _error_summary(exc: Exception) -> str:
    message = " ".join(str(exc).split()) or exc.__class__.__name__
    return message[:1000]


def _report_download_url(project_id: str, report_id: str) -> str:
    return f"/api/v1/projects/{project_id}/reports/{report_id}/download"
