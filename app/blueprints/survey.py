from io import BytesIO

from flask import Blueprint, request, send_file

from app.common.response import success
from app.common.utils import request_json
from app.models import (
    BusinessSystem,
    CoreDataAsset,
    DataAsset,
    DataProcessingActivity,
    ImportantDataAsset,
    PersonalInfoAsset,
)
from app.services import crud_service, file_service, survey_docx_service, survey_service


bp = Blueprint("survey", __name__)


SURVEY_MODELS = {
    "business-systems": BusinessSystem,
    "data-assets": DataAsset,
    "personal-info": PersonalInfoAsset,
    "important-data": ImportantDataAsset,
    "core-data": CoreDataAsset,
    "processing-activities": DataProcessingActivity,
}


@bp.get("/projects/<project_id>/survey/data-processor-basic")
def get_data_processor_basic(project_id: str):
    return success(survey_service.get_data_processor_basic(project_id))


@bp.put("/projects/<project_id>/survey/data-processor-basic")
def save_data_processor_basic(project_id: str):
    return success(survey_service.save_data_processor_basic(project_id, request_json()))


@bp.get("/projects/<project_id>/survey/security-protection")
def get_security_protection(project_id: str):
    return success(survey_service.get_security_protection(project_id))


@bp.put("/projects/<project_id>/survey/security-protection")
def save_security_protection(project_id: str):
    return success(survey_service.save_security_protection(project_id, request_json()))


@bp.get("/projects/<project_id>/survey/processing-activity-survey")
def get_processing_activity_survey(project_id: str):
    return success(survey_service.get_processing_activity_survey(project_id))


@bp.put("/projects/<project_id>/survey/processing-activity-survey")
def save_processing_activity_survey(project_id: str):
    return success(survey_service.save_processing_activity_survey(project_id, request_json()))


@bp.post("/projects/<project_id>/survey/business-systems/<record_id>/topology-diagram")
def upload_business_system_topology_diagram(project_id: str, record_id: str):
    uploaded = request.files.get("file") or next(iter(request.files.values()), None)
    return success(survey_service.save_business_system_diagram(project_id, record_id, "topology", uploaded))


@bp.post("/projects/<project_id>/survey/business-systems/<record_id>/data-flow-diagram")
def upload_business_system_data_flow_diagram(project_id: str, record_id: str):
    uploaded = request.files.get("file") or next(iter(request.files.values()), None)
    return success(survey_service.save_business_system_diagram(project_id, record_id, "data-flow", uploaded))


@bp.get("/projects/<project_id>/survey/export-template")
def export_survey_template(project_id: str):
    return _send_generated_docx(
        project_id,
        survey_docx_service.export_template_docx(project_id),
        "SURVEY_DOCX_TEMPLATE",
    )


@bp.post("/projects/<project_id>/survey/import")
def import_survey_docx(project_id: str):
    uploaded = request.files.get("file") or next(iter(request.files.values()), None)
    return success(survey_docx_service.import_survey_docx(project_id, uploaded))


@bp.get("/projects/<project_id>/survey/export")
def export_survey_docx(project_id: str):
    return _send_generated_docx(
        project_id,
        survey_docx_service.export_survey_docx(project_id),
        "SURVEY_DOCX_EXPORT",
    )


@bp.get("/projects/<project_id>/survey/<kind>")
def list_survey_records(project_id: str, kind: str):
    return success(crud_service.list_records(project_id, _model(kind)))


@bp.post("/projects/<project_id>/survey/<kind>")
def create_survey_record(project_id: str, kind: str):
    return success(crud_service.create_record(project_id, _model(kind), request_json()))


@bp.put("/projects/<project_id>/survey/<kind>/<record_id>")
def update_survey_record(project_id: str, kind: str, record_id: str):
    return success(crud_service.update_record(project_id, _model(kind), record_id, request_json()))


@bp.delete("/projects/<project_id>/survey/<kind>/<record_id>")
def delete_survey_record(project_id: str, kind: str, record_id: str):
    return success(crud_service.delete_record(project_id, _model(kind), record_id))


def _model(kind: str):
    return SURVEY_MODELS[kind]


def _send_generated_docx(project_id: str, generated: tuple[str, bytes, str], biz_type: str):
    file_name, content, content_type = generated
    file_service.save_bytes(file_name, content, content_type, biz_type=biz_type, project_id=project_id)
    return send_file(BytesIO(content), as_attachment=True, download_name=file_name, mimetype=content_type)
