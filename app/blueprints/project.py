from flask import Blueprint, request

from app.common.response import success
from app.common.utils import request_json
from app.services import project_service


bp = Blueprint("project", __name__)


@bp.get("/projects")
def list_projects():
    return success(project_service.list_projects(request.args))


@bp.get("/projects/statistics")
def statistics():
    return success(project_service.statistics())


@bp.post("/projects")
def create_project():
    return success(project_service.create_project(request_json()))


@bp.get("/projects/<project_id>")
def get_project(project_id: str):
    return success(project_service.serialize_project(project_service.get_project(project_id)))


@bp.put("/projects/<project_id>")
def update_project(project_id: str):
    return success(project_service.update_project(project_id, request_json()))


@bp.delete("/projects/<project_id>")
def delete_project(project_id: str):
    return success(project_service.delete_project(project_id))


@bp.post("/projects/<project_id>/start")
def start_project(project_id: str):
    return success(project_service.start_project(project_id))
