from io import BytesIO

from flask import Blueprint, request, send_file

from app.common.response import success
from app.common.utils import request_json
from app.models import AssessmentTeamMember, ClientTeamMember, FocusPoint, GapItem
from app.services import crud_service, file_service, plan_team_excel_service


bp = Blueprint("plan", __name__)


PLAN_MODELS = {
    "assessment-team": AssessmentTeamMember,
    "client-team": ClientTeamMember,
    "focus-points": FocusPoint,
    "gap-items": GapItem,
}


@bp.get("/projects/<project_id>/plan/team-export-template")
def export_team_template(project_id: str):
    return _send_generated_workbook(
        project_id,
        plan_team_excel_service.export_template_workbook(project_id),
        "PLAN_TEAM_TEMPLATE",
    )


@bp.post("/projects/<project_id>/plan/team-import")
def import_team(project_id: str):
    uploaded = request.files.get("file") or next(iter(request.files.values()), None)
    return success(plan_team_excel_service.import_team_workbook(project_id, uploaded))


@bp.get("/projects/<project_id>/plan/team-export")
def export_team(project_id: str):
    return _send_generated_workbook(
        project_id,
        plan_team_excel_service.export_team_workbook(project_id),
        "PLAN_TEAM_EXPORT",
    )


@bp.get("/projects/<project_id>/plan/<kind>")
def list_plan_records(project_id: str, kind: str):
    return success(crud_service.list_records(project_id, _model(kind)))


@bp.post("/projects/<project_id>/plan/<kind>")
def create_plan_record(project_id: str, kind: str):
    return success(crud_service.create_record(project_id, _model(kind), request_json()))


@bp.put("/projects/<project_id>/plan/<kind>/<record_id>")
def update_plan_record(project_id: str, kind: str, record_id: str):
    return success(crud_service.update_record(project_id, _model(kind), record_id, request_json()))


@bp.delete("/projects/<project_id>/plan/<kind>/<record_id>")
def delete_plan_record(project_id: str, kind: str, record_id: str):
    return success(crud_service.delete_record(project_id, _model(kind), record_id))


def _model(kind: str):
    return PLAN_MODELS[kind]


def _send_generated_workbook(project_id: str, generated: tuple[str, bytes, str], biz_type: str):
    file_name, content, content_type = generated
    file_service.save_bytes(file_name, content, content_type, biz_type=biz_type, project_id=project_id)
    return send_file(BytesIO(content), as_attachment=True, download_name=file_name, mimetype=content_type)
