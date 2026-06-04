from flask import Blueprint

from app.common.response import success
from app.common.utils import request_json
from app.models import AssessmentTeamMember, ClientTeamMember, FocusPoint, GapItem
from app.services import crud_service


bp = Blueprint("plan", __name__)


PLAN_MODELS = {
    "assessment-team": AssessmentTeamMember,
    "client-team": ClientTeamMember,
    "focus-points": FocusPoint,
    "gap-items": GapItem,
}


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
