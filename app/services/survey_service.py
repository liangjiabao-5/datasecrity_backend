from pathlib import Path

from werkzeug.datastructures import FileStorage

from app.common.exceptions import BusinessError, NotFoundError
from app.common.utils import camelize_keys
from app.extensions import SessionLocal
from app.models import BusinessSystem, ProcessingActivitySurvey, SecurityProtectionSurvey
from app.services.audit_service import audit
from app.services import file_service
from app.services.project_service import get_project


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}
_BUSINESS_SYSTEM_DIAGRAMS = {
    "topology": {
        "field": "topology_file_id",
        "biz_type": "SURVEY_TOPOLOGY_DIAGRAM",
    },
    "data-flow": {
        "field": "business_flow_file_id",
        "biz_type": "SURVEY_DATA_FLOW_DIAGRAM",
    },
}


def get_security_protection(project_id: str) -> dict:
    get_project(project_id)
    session = SessionLocal()
    row = session.query(SecurityProtectionSurvey).filter_by(project_id=project_id, deleted=False).first()
    return camelize_keys(row.payload) if row and row.payload else {}


def save_security_protection(project_id: str, payload: dict) -> dict:
    get_project(project_id)
    session = SessionLocal()
    row = session.query(SecurityProtectionSurvey).filter_by(project_id=project_id, deleted=False).first()
    if not row:
        row = SecurityProtectionSurvey(project_id=project_id, payload={})
        session.add(row)
    row.payload = payload
    audit("SECURITY_PROTECTION_SAVE", "SecurityProtectionSurvey", row.id, after=payload)
    session.commit()
    return camelize_keys(row.payload or {})


def get_processing_activity_survey(project_id: str) -> dict:
    get_project(project_id)
    session = SessionLocal()
    row = session.query(ProcessingActivitySurvey).filter_by(project_id=project_id, deleted=False).first()
    return camelize_keys(row.payload) if row and row.payload else _default_processing_activity_survey()


def save_processing_activity_survey(project_id: str, payload: dict) -> dict:
    get_project(project_id)
    session = SessionLocal()
    row = session.query(ProcessingActivitySurvey).filter_by(project_id=project_id, deleted=False).first()
    if not row:
        row = ProcessingActivitySurvey(project_id=project_id, payload={})
        session.add(row)
    row.payload = payload
    audit("PROCESSING_ACTIVITY_SURVEY_SAVE", "ProcessingActivitySurvey", row.id, after=payload)
    session.commit()
    return camelize_keys(row.payload or {})


def save_business_system_diagram(
    project_id: str,
    record_id: str,
    diagram_type: str,
    file: FileStorage | None,
) -> dict:
    get_project(project_id)
    diagram = _BUSINESS_SYSTEM_DIAGRAMS[diagram_type]
    _validate_diagram_file(file)

    session = SessionLocal()
    record = (
        session.query(BusinessSystem)
        .filter(BusinessSystem.id == record_id, BusinessSystem.project_id == project_id, BusinessSystem.deleted.is_(False))
        .first()
    )
    if not record:
        raise NotFoundError("BusinessSystem not found.")

    before = record.to_dict()
    saved_file = file_service.save_upload(file, biz_type=diagram["biz_type"], project_id=project_id)
    setattr(record, diagram["field"], saved_file["fileId"])
    after = record.to_dict()
    audit("BUSINESS_SYSTEM_DIAGRAM_UPLOAD", "BusinessSystem", record.id, before=before, after=after)
    session.commit()
    return {"file": saved_file, "businessSystem": record.to_dict()}


def _default_processing_activity_survey() -> dict:
    return {
        "activityTypes": [],
        "collect": {},
        "transfer": {},
        "store": {},
        "useProcess": {},
        "provide": {},
        "public": {},
        "delete": {},
    }


def _validate_diagram_file(file: FileStorage | None) -> None:
    if not file or not file.filename:
        raise BusinessError("FILE_REQUIRED", "Upload file is required.")
    suffix = Path(file.filename).suffix.lower()
    content_type = (file.content_type or "").lower()
    if suffix not in _IMAGE_EXTENSIONS and not content_type.startswith("image/"):
        raise BusinessError("INVALID_DIAGRAM_FILE", "Only image files are supported.")
