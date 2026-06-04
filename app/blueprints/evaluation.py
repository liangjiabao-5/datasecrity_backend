from io import BytesIO

from flask import Blueprint, request, send_file

from app.common.response import success
from app.common.utils import request_json
from app.services import evaluation_service, file_service


bp = Blueprint("evaluation", __name__)


@bp.get("/projects/<project_id>/evaluation/catalog")
def catalog(project_id: str):
    return success(evaluation_service.catalog(project_id))


@bp.get("/projects/<project_id>/evaluation/items")
def list_items(project_id: str):
    return success(evaluation_service.list_items(project_id, request.args))


@bp.put("/projects/<project_id>/evaluation/items/<item_id>/record")
def save_record(project_id: str, item_id: str):
    return success(evaluation_service.save_record(project_id, item_id, request_json()))


@bp.post("/projects/<project_id>/evaluation/items/batch-result")
def batch_result(project_id: str):
    return success(evaluation_service.batch_result(project_id, request_json()))


@bp.post("/projects/<project_id>/evaluation/calculate-score")
def calculate_score(project_id: str):
    return success(evaluation_service.calculate_score(project_id))


@bp.get("/projects/<project_id>/evaluation/export-template")
def export_template(project_id: str):
    return _send_generated_workbook(project_id, evaluation_service.export_template_workbook(project_id), "EVALUATION_TEMPLATE")


@bp.post("/projects/<project_id>/evaluation/import")
def import_evaluation(project_id: str):
    uploaded = request.files.get("file") or next(iter(request.files.values()), None)
    return success(evaluation_service.import_records_workbook(project_id, uploaded))


@bp.get("/projects/<project_id>/evaluation/export")
def export_evaluation(project_id: str):
    return _send_generated_workbook(project_id, evaluation_service.export_records_workbook(project_id), "EVALUATION_EXPORT")


def _send_generated_workbook(project_id: str, generated: tuple[str, bytes, str], biz_type: str):
    file_name, content, content_type = generated
    file_service.save_bytes(file_name, content, content_type, biz_type=biz_type, project_id=project_id)
    return send_file(BytesIO(content), as_attachment=True, download_name=file_name, mimetype=content_type)
