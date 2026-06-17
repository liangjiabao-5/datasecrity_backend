from io import BytesIO

from flask import Blueprint, request, send_file

from app.common.response import success
from app.common.utils import request_json
from app.services import basic_info_excel_service, basic_info_service, file_service


bp = Blueprint("basic", __name__)


@bp.get("/projects/<project_id>/basic-info")
def get_basic_info(project_id: str):
    return success(basic_info_service.get_basic_info(project_id))


@bp.put("/projects/<project_id>/basic-info")
def save_basic_info(project_id: str):
    return success(basic_info_service.save_basic_info(project_id, request_json()))


@bp.get("/projects/<project_id>/basic-info/export-template")
def export_basic_info_template(project_id: str):
    return _send_generated_workbook(
        project_id,
        basic_info_excel_service.export_template_workbook(project_id),
        "BASIC_INFO_TEMPLATE",
    )


@bp.post("/projects/<project_id>/basic-info/import")
def import_basic_info(project_id: str):
    uploaded = request.files.get("file") or next(iter(request.files.values()), None)
    return success(basic_info_excel_service.import_basic_info_workbook(project_id, uploaded))


@bp.get("/projects/<project_id>/basic-info/export")
def export_basic_info(project_id: str):
    return _send_generated_workbook(
        project_id,
        basic_info_excel_service.export_basic_info_workbook(project_id),
        "BASIC_INFO_EXPORT",
    )


@bp.post("/projects/<project_id>/basic-info/custom-references")
def create_reference(project_id: str):
    return success(basic_info_service.create_reference(project_id, request_json()))


def _send_generated_workbook(project_id: str, generated: tuple[str, bytes, str], biz_type: str):
    file_name, content, content_type = generated
    file_service.save_bytes(file_name, content, content_type, biz_type=biz_type, project_id=project_id)
    return send_file(BytesIO(content), as_attachment=True, download_name=file_name, mimetype=content_type)
