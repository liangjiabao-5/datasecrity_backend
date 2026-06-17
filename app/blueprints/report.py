from flask import Blueprint, send_file

from app.common.response import success
from app.common.utils import request_json
from app.services import file_service, report_service


bp = Blueprint("report", __name__)


@bp.get("/projects/<project_id>/reports")
def report_list(project_id: str):
    return success(report_service.list_reports(project_id))


@bp.get("/projects/<project_id>/reports/readiness")
def report_readiness(project_id: str):
    return success(report_service.report_readiness(project_id))


@bp.post("/projects/<project_id>/reports/generate")
def report_generate(project_id: str):
    """创建报告生成任务。

    接口只负责解析请求并返回任务信息，Word 渲染、文件保存和状态回写由报告服务及后台任务完成。
    """
    return success(report_service.generate_report(project_id, request_json()))


@bp.get("/projects/<project_id>/reports/tasks/<task_id>")
def report_task(project_id: str, task_id: str):
    return success(report_service.get_task(project_id, task_id))


@bp.get("/projects/<project_id>/reports/<report_id>/download")
def report_download(project_id: str, report_id: str):
    file = report_service.get_report_file(project_id, report_id)
    return send_file(
        file_service.file_stream(file),
        as_attachment=True,
        download_name=file.file_name,
        mimetype=file.content_type or "application/octet-stream",
    )


@bp.post("/projects/<project_id>/reports/<report_id>/retry")
def report_retry(project_id: str, report_id: str):
    return success(report_service.retry_report(project_id, report_id))


@bp.delete("/projects/<project_id>/reports/<report_id>")
def report_delete(project_id: str, report_id: str):
    return success(report_service.delete_report(project_id, report_id))
