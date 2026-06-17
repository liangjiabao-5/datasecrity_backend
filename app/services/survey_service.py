from pathlib import Path

from werkzeug.datastructures import FileStorage

from app.common.exceptions import BusinessError, NotFoundError
from app.common.utils import camelize_keys
from app.extensions import SessionLocal
from app.models import BusinessSystem, DataProcessorBasicSurvey, ProcessingActivitySurvey, SecurityProtectionSurvey
from app.services.audit_service import audit
from app.services import file_service
from app.services.project_service import get_project


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}
_VSDX_EXTENSION = ".vsdx"
_PROCESSING_ACTIVITY_SECTIONS = {
    "COLLECT": "collect",
    "TRANSFER": "transfer",
    "STORE": "store",
    "USE_PROCESS": "use_process",
    "PROVIDE": "provide",
    "PUBLIC": "public",
    "DELETE": "delete",
}
_DATA_PROCESSOR_BASIC_FIELDS = [
    "unit_name",
    "unified_social_credit_code",
    "office_address",
    "legal_representative",
    "staff_size",
    "business_scope",
    "data_security_officer",
    "contact_info",
    "unit_nature",
    "specific_processor_type",
    "power_industry_category",
    "business_operation_area",
    "data_processing_location",
    "main_business_scope",
    "business_scale",
    "administrative_license",
]
_PROCESSING_TEXT_FIELDS = [
    "collection_channels",
    "collection_method",
    "collection_data_scope",
    "collection_purpose",
    "collection_frequency",
    "collection_external_sources",
    "collection_contracts",
    "collection_related_systems",
    "collection_public_device_usage",
    "storage_method",
    "data_center",
    "storage_system",
    "external_storage_provider",
    "storage_location",
    "storage_duration",
    "backup_redundancy_strategy",
    "online_channel",
    "offline_transfer",
    "transfer_protocol",
    "data_interface",
    "use_purpose",
    "use_method",
    "use_scope",
    "use_scenario",
    "algorithm_rules",
    "processing_details",
    "algorithm_recommendation_service",
    "entrusted_or_joint_processing",
    "provide_purpose",
    "provide_method",
    "provide_scope",
    "data_recipients",
    "provide_contracts",
    "provided_personal_info_and_important_data",
    "public_purpose",
    "public_method",
    "public_scope",
    "public_audience_size",
    "public_data_types",
    "public_data_scale",
    "deletion_scenarios",
    "deletion_method",
    "data_archive",
    "media_destruction",
    "cross_border_presence",
    "cross_border_description",
]
_SECURITY_PROTECTION_FIELDS = [
    "compliance_assessment_status",
    "data_security_management",
    "network_security_devices_and_policies",
    "identity_authentication_and_access_control",
    "vulnerability_management",
    "remote_management_software",
    "account_password_management",
    "security_technology_application",
    "is_power_monitoring_system",
    "production_control_area_protection",
    "security_access_area_setup",
    "power_monitoring_dedicated_network",
    "zone_isolation_device_usage",
    "wide_area_network_connection_security",
    "power_dispatch_authentication",
    "network_service_security_control",
    "security_access_area_security_control",
    "zone_boundary_protection",
    "product_security_reliability",
    "operator_security_monitoring_warning",
    "security_incidents_and_threats",
    "detected_threats",
    "public_threat_alerts",
    "other_security_threats",
]
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


def get_data_processor_basic(project_id: str) -> dict:
    get_project(project_id)
    session = SessionLocal()
    row = session.query(DataProcessorBasicSurvey).filter_by(project_id=project_id, deleted=False).first()
    return camelize_keys(_data_processor_basic_payload(row))


def save_data_processor_basic(project_id: str, payload: dict) -> dict:
    get_project(project_id)
    payload = _normalize_text_payload(payload, _DATA_PROCESSOR_BASIC_FIELDS)
    session = SessionLocal()
    row = session.query(DataProcessorBasicSurvey).filter_by(project_id=project_id, deleted=False).first()
    if not row:
        row = DataProcessorBasicSurvey(project_id=project_id)
        session.add(row)
    _assign_text_fields(row, payload, _DATA_PROCESSOR_BASIC_FIELDS)
    audit("DATA_PROCESSOR_BASIC_SAVE", "DataProcessorBasicSurvey", row.id, after=payload)
    session.commit()
    return camelize_keys(_data_processor_basic_payload(row))


def get_security_protection(project_id: str) -> dict:
    get_project(project_id)
    session = SessionLocal()
    row = session.query(SecurityProtectionSurvey).filter_by(project_id=project_id, deleted=False).first()
    return camelize_keys(security_protection_payload(row))


def save_security_protection(project_id: str, payload: dict) -> dict:
    get_project(project_id)
    payload = _normalize_security_protection_payload(payload)
    session = SessionLocal()
    row = session.query(SecurityProtectionSurvey).filter_by(project_id=project_id, deleted=False).first()
    if not row:
        row = SecurityProtectionSurvey(project_id=project_id)
        session.add(row)
    _assign_text_fields(row, payload, _SECURITY_PROTECTION_FIELDS)
    audit("SECURITY_PROTECTION_SAVE", "SecurityProtectionSurvey", row.id, after=payload)
    session.commit()
    return camelize_keys(security_protection_payload(row))


def get_processing_activity_survey(project_id: str) -> dict:
    get_project(project_id)
    session = SessionLocal()
    row = session.query(ProcessingActivitySurvey).filter_by(project_id=project_id, deleted=False).first()
    return camelize_keys(processing_activity_payload(row))


def save_processing_activity_survey(project_id: str, payload: dict) -> dict:
    get_project(project_id)
    payload = _normalize_processing_activity_payload(payload)
    session = SessionLocal()
    row = session.query(ProcessingActivitySurvey).filter_by(project_id=project_id, deleted=False).first()
    if not row:
        row = ProcessingActivitySurvey(project_id=project_id)
        session.add(row)
    _assign_text_fields(row, payload, _PROCESSING_TEXT_FIELDS)
    row.involved_activities = _list_to_storage(payload.get("involved_activities"))
    audit("PROCESSING_ACTIVITY_SURVEY_SAVE", "ProcessingActivitySurvey", row.id, after=payload)
    session.commit()
    return camelize_keys(processing_activity_payload(row))


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


def _validate_diagram_file(file: FileStorage | None) -> None:
    if not file or not file.filename:
        raise BusinessError("FILE_REQUIRED", "Upload file is required.")
    suffix = Path(file.filename).suffix.lower()
    content_type = (file.content_type or "").lower()
    if suffix == _VSDX_EXTENSION:
        return
    if suffix not in _IMAGE_EXTENSIONS and not content_type.startswith("image/"):
        raise BusinessError("INVALID_DIAGRAM_FILE", "Only image or .vsdx files are supported.")


def _normalize_processing_activity_payload(payload: dict) -> dict:
    normalized = _normalize_text_payload(payload, _PROCESSING_TEXT_FIELDS)
    raw_activities = payload.get("involved_activities")
    if raw_activities in (None, ""):
        raw_activities = payload.get("activity_types")
    normalized["involved_activities"] = _selected_activity_codes(raw_activities)
    return normalized


def _selected_activity_codes(raw) -> list[str]:
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    allowed = set(_PROCESSING_ACTIVITY_SECTIONS)
    codes = [str(value).upper() for value in raw if value not in (None, "")]
    return [code for code in dict.fromkeys(codes) if code in allowed]


def _normalize_security_protection_payload(payload: dict) -> dict:
    normalized = _normalize_text_payload(payload, _SECURITY_PROTECTION_FIELDS)
    normalized["is_power_monitoring_system"] = _normalize_yes_no(normalized.get("is_power_monitoring_system"))
    return normalized


def _data_processor_basic_payload(row: DataProcessorBasicSurvey | None) -> dict:
    return _normalize_text_payload(_payload_from_row(row, _DATA_PROCESSOR_BASIC_FIELDS), _DATA_PROCESSOR_BASIC_FIELDS)


def processing_activity_payload(row: ProcessingActivitySurvey | None) -> dict:
    payload = _normalize_text_payload(_payload_from_row(row, _PROCESSING_TEXT_FIELDS), _PROCESSING_TEXT_FIELDS)
    payload["involved_activities"] = _selected_activity_codes(_storage_to_list(getattr(row, "involved_activities", None)))
    return payload


def security_protection_payload(row: SecurityProtectionSurvey | None) -> dict:
    return _normalize_security_protection_payload(_payload_from_row(row, _SECURITY_PROTECTION_FIELDS))


def _payload_from_row(row, fields: list[str]) -> dict:
    if row is None:
        return {}
    return {field: getattr(row, field, "") for field in fields}


def _assign_text_fields(row, payload: dict, fields: list[str]) -> None:
    for field in fields:
        value = payload.get(field, "")
        setattr(row, field, "" if value is None else value)


def _normalize_text_payload(payload: dict, fields: list[str]) -> dict:
    normalized = {}
    for field in fields:
        value = payload.get(field, "")
        normalized[field] = "" if value is None else value
    return normalized


def _list_to_storage(values) -> str:
    if values in (None, ""):
        return ""
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return ""
    return ",".join(str(value).upper() for value in values if value not in (None, ""))


def _storage_to_list(value) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return value
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _normalize_yes_no(value) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, bool):
        return "YES" if value else "NO"
    text = str(value).strip()
    upper = text.upper()
    if upper in {"YES", "Y", "TRUE", "1"} or text == "是":
        return "YES"
    if upper in {"NO", "N", "FALSE", "0"} or text == "否":
        return "NO"
    return text
