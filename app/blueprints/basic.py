from flask import Blueprint

from app.common.response import success
from app.common.utils import request_json
from app.services import basic_info_service


bp = Blueprint("basic", __name__)


@bp.get("/projects/<project_id>/basic-info")
def get_basic_info(project_id: str):
    return success(basic_info_service.get_basic_info(project_id))


@bp.put("/projects/<project_id>/basic-info")
def save_basic_info(project_id: str):
    return success(basic_info_service.save_basic_info(project_id, request_json()))


@bp.post("/projects/<project_id>/basic-info/custom-references")
def create_reference(project_id: str):
    return success(basic_info_service.create_reference(project_id, request_json()))
