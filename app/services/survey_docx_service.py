from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from pathlib import Path
import re
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

from app.common.exceptions import BusinessError
from app.common.utils import camelize_keys
from app.extensions import SessionLocal
from app.models import (
    BusinessSystem,
    CoreDataAsset,
    DataAsset,
    DataProcessorBasicSurvey,
    ImportantDataAsset,
    PersonalInfoAsset,
    ProcessingActivitySurvey,
    SecurityProtectionSurvey,
)
from app.services.audit_service import audit
from app.services.project_service import get_project
from app.services import survey_service


DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEMPLATE_PATH = Path("doc") / "附录A（资料性）调研表格.docx"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
FILL_MARKER = "填写："
DATA_SCOPE_NAMES = ["一般数据", "个人信息", "重要数据", "核心数据"]

ET.register_namespace("w", W_NS)

DATA_PROCESSOR_ROWS = {
    "unit_name": 1,
    "unified_social_credit_code": 2,
    "office_address": 3,
    "legal_representative": 4,
    "staff_size": 5,
    "business_scope": 6,
    "data_security_officer": 7,
    "contact_info": 8,
    "unit_nature": 9,
    "specific_processor_type": 10,
    "power_industry_category": 11,
    "business_operation_area": 12,
    "data_processing_location": 13,
    "main_business_scope": 14,
    "business_scale": 15,
    "administrative_license": 16,
}
BUSINESS_SYSTEM_ROWS = {
    "system_name": 1,
    "business_function": 2,
    "service_object": 3,
    "user_scale": 4,
    "coverage_area": 5,
    "classified_protection_level": 6,
    "related_departments": 7,
    "data_scopes": 8,
}
DATA_ASSET_FIELDS = [
    "data_name",
    "data_form",
    "data_scope",
    "data_scale",
    "data_source",
    "storage_location",
    "flow_description",
    "classified",
    "data_category",
    "data_level",
    "personal_info",
]
PERSONAL_INFO_FIELDS = ["data_name", "data_category", "scale", "sensitivity", "data_source", "business_flow"]
IMPORTANT_DATA_FIELDS = ["data_name", "data_category", "scale", "data_source", "business_flow"]
CORE_DATA_FIELDS = ["data_name", "data_category", "scale", "data_source", "business_flow"]
PROCESSING_ROWS = [
    ("COLLECT", "collection_channels", 1),
    ("COLLECT", "collection_method", 2),
    ("COLLECT", "collection_data_scope", 3),
    ("COLLECT", "collection_purpose", 4),
    ("COLLECT", "collection_frequency", 5),
    ("COLLECT", "collection_external_sources", 6),
    ("COLLECT", "collection_contracts", 7),
    ("COLLECT", "collection_related_systems", 8),
    ("COLLECT", "collection_public_device_usage", 9),
    ("STORE", "storage_method", 10),
    ("STORE", "data_center", 11),
    ("STORE", "storage_system", 12),
    ("STORE", "external_storage_provider", 13),
    ("STORE", "storage_location", 14),
    ("STORE", "storage_duration", 15),
    ("STORE", "backup_redundancy_strategy", 16),
    ("TRANSFER", "online_channel", 17),
    ("TRANSFER", "offline_transfer", 18),
    ("TRANSFER", "transfer_protocol", 19),
    ("TRANSFER", "data_interface", 20),
    ("USE_PROCESS", "use_purpose", 21),
    ("USE_PROCESS", "use_method", 22),
    ("USE_PROCESS", "use_scope", 23),
    ("USE_PROCESS", "use_scenario", 24),
    ("USE_PROCESS", "algorithm_rules", 25),
    ("USE_PROCESS", "processing_details", 26),
    ("USE_PROCESS", "algorithm_recommendation_service", 27),
    ("USE_PROCESS", "entrusted_or_joint_processing", 28),
    ("PROVIDE", "provide_purpose", 29),
    ("PROVIDE", "provide_method", 30),
    ("PROVIDE", "provide_scope", 31),
    ("PROVIDE", "data_recipients", 32),
    ("PROVIDE", "provide_contracts", 33),
    ("PROVIDE", "provided_personal_info_and_important_data", 34),
    ("PUBLIC", "public_purpose", 35),
    ("PUBLIC", "public_method", 36),
    ("PUBLIC", "public_scope", 37),
    ("PUBLIC", "public_audience_size", 38),
    ("PUBLIC", "public_data_types", 39),
    ("PUBLIC", "public_data_scale", 40),
    ("DELETE", "deletion_scenarios", 41),
    ("DELETE", "deletion_method", 42),
    ("DELETE", "data_archive", 43),
    ("DELETE", "media_destruction", 44),
    ("TRANSFER", "cross_border_presence", 45),
]
SECURITY_ROWS = {
    "compliance_assessment_status": 1,
    "data_security_management": 2,
    "network_security_devices_and_policies": 3,
    "identity_authentication_and_access_control": 4,
    "vulnerability_management": 5,
    "remote_management_software": 6,
    "account_password_management": 7,
    "security_technology_application": 8,
    "production_control_area_protection": 9,
    "security_incidents_and_threats": 10,
    "detected_threats": 11,
    "public_threat_alerts": 12,
    "other_security_threats": 13,
}
SECURITY_SIMPLE_ROWS = {
    field: row_no for field, row_no in SECURITY_ROWS.items() if row_no <= 8
}
SECURITY_THREAT_FIELDS = [
    "security_incidents_and_threats",
    "detected_threats",
    "public_threat_alerts",
    "other_security_threats",
]
SECURITY_ROW_PROMPTS = {
    "compliance_assessment_status": ["已开展的等级保护测评、商用密码应用安全性评估、安全检测、风险评估、安全认证、合规审计情况，及发现问题的整改情况。"],
    "data_security_management": ["数据安全管理组织、人员及制度情况"],
    "network_security_devices_and_policies": ["防火墙、入侵检测、入侵防御等网络安全设备及策略情况"],
    "identity_authentication_and_access_control": ["身份鉴别与访问控制情况"],
    "vulnerability_management": ["网络安全漏洞管理及修复情况"],
    "remote_management_software": ["VPN等远程管理软件的用户及管理情况"],
    "account_password_management": ["设备、系统及用户的账号口令管理情况"],
    "security_technology_application": ["加密、脱敏、去标识化等安全技术应用情况"],
}
SECURITY_THREAT_PROMPTS = {
    "security_incidents_and_threats": ["3年内发生的网络和数据安全事件、攻击威胁情况，包括事件名称、数据类型和数量、发生原因、级别、处置措施、整改措施等，重大事件需提供事件调查评估报告。"],
    "detected_threats": ["实际环境中通过检测工具、监测系统、日志审计等发现的威胁。"],
    "public_threat_alerts": ["近期公开发布的社会或特定行业威胁事件、威胁预警。"],
    "other_security_threats": ["其他可能面临的数据泄露、窃取、篡改、破坏/损毁、丢失、滥用、非法获取、非法利用、非法提供等安全威胁。"],
}
POWER_MONITORING_DEFAULT_PAYLOAD = {
    "is_power_monitoring_system": "",
    "production_control_area_protection": "",
    "security_access_area_setup": "",
    "power_monitoring_dedicated_network": "",
    "zone_isolation_device_usage": "",
    "wide_area_network_connection_security": "",
    "power_dispatch_authentication": "",
    "network_service_security_control": "",
    "security_access_area_security_control": "",
    "zone_boundary_protection": "",
    "product_security_reliability": "",
    "operator_security_monitoring_warning": "",
}
POWER_MONITORING_FIELD_KEYWORDS = [
    ("is_power_monitoring_system", ("是否为电力监控系统",)),
    ("production_control_area_protection", ("生产控制区和管理信息区", "设置", "防护")),
    ("security_access_area_setup", ("安全接入区", "设立")),
    ("power_monitoring_dedicated_network", ("电力监控专用网络", "使用")),
    ("zone_isolation_device_usage", ("生产控制区", "管理信息区", "安全接入区", "隔离")),
    ("wide_area_network_connection_security", ("生产控制区", "电力监控专用网络", "广域网")),
    ("power_dispatch_authentication", ("电力调度认证",)),
    ("network_service_security_control", ("网络服务", "安全管控")),
    ("security_access_area_security_control", ("安全接入区", "安全管控")),
    ("zone_boundary_protection", ("分区边界", "安全防护")),
    ("product_security_reliability", ("产品", "安全可靠")),
    ("operator_security_monitoring_warning", ("运营者", "预警")),
]
POWER_MONITORING_FIELD_PROMPTS = {
    "production_control_area_protection": ["1）生产控制区和管理信息区的设置和防护情况"],
    "security_access_area_setup": ["2）安全接入区的设立情况"],
    "power_monitoring_dedicated_network": ["3）电力监控专用网络的使用情况"],
    "zone_isolation_device_usage": ["4）生产控制区与管理信息区、安全接入区的隔离及隔离装置使用情况"],
    "wide_area_network_connection_security": [
        "5）生产控制区与电力监控专用网络的广域网之间的连接安全方案",
        "5）生产控制区与电力监控专用网络的广域网之间的联接安全方案",
    ],
    "power_dispatch_authentication": ["6）电力调度认证机制建设情况"],
    "network_service_security_control": ["7）网络服务的安全管控情况"],
    "security_access_area_security_control": ["8）安全接入区的安全管控情况"],
    "zone_boundary_protection": ["9）电力监控系统分区边界的安全防护情况"],
    "product_security_reliability": ["10）系统使用的产品安全可靠情况"],
    "operator_security_monitoring_warning": [
        "11）运营者的网络安全监测预警机制建设情况",
        "11）运营者的网络安全检测预警机制建设情况",
    ],
}
NONE_MARKERS = {"不涉及", "无"}


def export_template_docx(project_id: str) -> tuple[str, bytes, str]:
    get_project(project_id)
    return f"信息调研模板-{project_id}.docx", TEMPLATE_PATH.read_bytes(), DOCX_MIME_TYPE


def import_survey_docx(project_id: str, file) -> dict:
    get_project(project_id)
    content = _read_docx_upload(file)
    document, tables = _document_tables(content)
    _validate_tables(tables)
    _, default_tables = _document_tables(TEMPLATE_PATH.read_bytes())

    parsed = {
        "data_processor_basic": _parse_data_processor(tables[0], default_tables[0]),
        "business_system": _parse_business_system(tables[1], default_tables[1]),
        "data_assets": _parse_record_table(tables[2], default_tables[2], 2, DATA_ASSET_FIELDS),
        "personal_info": _parse_record_table(tables[3], default_tables[3], 2, PERSONAL_INFO_FIELDS),
        "important_data": _parse_record_table(tables[4], default_tables[4], 2, IMPORTANT_DATA_FIELDS),
        "core_data": _parse_record_table(tables[5], default_tables[5], 2, CORE_DATA_FIELDS),
        "processing_activity": _parse_processing_activity(tables[6], default_tables[6]),
        "security_protection": _parse_security_protection(tables[7], default_tables[7]),
    }
    return camelize_keys(_save_import(project_id, parsed))


def export_survey_docx(project_id: str) -> tuple[str, bytes, str]:
    get_project(project_id)
    template = TEMPLATE_PATH.read_bytes()
    document, tables = _document_tables(template)
    _validate_tables(tables)
    data = _current_survey_data(project_id)

    _fill_data_processor(tables[0], data["data_processor"])
    _fill_business_system(tables[1], data["business_system"])
    _fill_record_table(tables[2], data["data_assets"], DATA_ASSET_FIELDS, 2)
    _fill_record_table(tables[3], data["personal_info"], PERSONAL_INFO_FIELDS, 2)
    _fill_record_table(tables[4], data["important_data"], IMPORTANT_DATA_FIELDS, 2)
    _fill_record_table(tables[5], data["core_data"], CORE_DATA_FIELDS, 2)
    _fill_processing_activity(tables[6], data["processing"])
    _fill_security_protection(tables[7], data["security"])

    content = _write_document_xml(template, document)
    return f"信息调研-{project_id}.docx", content, DOCX_MIME_TYPE


def _read_docx_upload(file) -> bytes:
    if not file or not file.filename:
        raise BusinessError("FILE_REQUIRED", "Upload file is required.")
    if Path(file.filename).suffix.lower() != ".docx":
        _raise_import_validation([_import_error(None, 0, "file", "导入文件必须是 .docx 格式。")])
    try:
        file.stream.seek(0)
        return file.stream.read()
    except Exception as exc:
        _raise_import_validation([_import_error(None, 0, "file", "导入文件读取失败。")], exc)


def _document_tables(content: bytes):
    try:
        with zipfile.ZipFile(BytesIO(content)) as docx:
            document = ET.fromstring(docx.read("word/document.xml"))
    except Exception as exc:
        _raise_import_validation([_import_error(None, 0, "file", "导入文件不是有效的 Word 文档。")], exc)
    return document, list(document.iter(W + "tbl"))


def _validate_tables(tables: list) -> None:
    if len(tables) < 8:
        _raise_import_validation([_import_error("附录A", 0, "table", "导入文件缺少信息调研模板必需表格。")])


def _parse_data_processor(table, default_table) -> dict:
    return {field: _changed_row_value(table, default_table, row_no) for field, row_no in DATA_PROCESSOR_ROWS.items()}


def _parse_business_system(table, default_table) -> dict:
    payload = {}
    for field, row_no in BUSINESS_SYSTEM_ROWS.items():
        value = _changed_row_value(table, default_table, row_no)
        if field == "data_scopes":
            payload[field] = "、".join(_parse_data_scopes(value))
        else:
            payload[field] = value
    return payload


def _parse_record_table(table, default_table, start_row: int, fields: list[str]) -> list[dict]:
    records = []
    rows = table.findall(W + "tr")
    default_rows = default_table.findall(W + "tr")
    if start_row < len(rows):
        default_row = default_rows[start_row] if start_row < len(default_rows) else None
        first_cell_value = _changed_cell_value(rows[start_row], default_row, 0)
        if _is_none_marker(first_cell_value):
            return records
    for row_index in range(start_row, len(rows)):
        row = rows[row_index]
        default_row = default_rows[row_index] if row_index < len(default_rows) else None
        values = {}
        for col_index, field in enumerate(fields):
            value = _changed_cell_value(row, default_row, col_index)
            if field in {"classified", "personal_info"}:
                values[field] = _yes_no_bool(value)
            else:
                values[field] = value
        if _record_has_values(values):
            records.append(values)
    return records


def _parse_processing_activity(table, default_table) -> dict:
    payload = {}
    activity_order = []
    activity_values = {}
    for code, field, row_no in PROCESSING_ROWS:
        value = _changed_row_value(table, default_table, row_no)
        payload[field] = value
        if code not in activity_values:
            activity_order.append(code)
            activity_values[code] = []
        activity_values[code].append(value)
    payload["involved_activities"] = [
        code for code in activity_order if any(_is_meaningful_activity_value(value) for value in activity_values[code])
    ]
    return payload


def _parse_security_protection(table, default_table) -> dict:
    payload = {}
    for field, row_no in SECURITY_SIMPLE_ROWS.items():
        payload[field] = _security_sequence_answer_value(table, row_no, SECURITY_ROW_PROMPTS.get(field, []))
    payload.update(_parse_power_monitoring_protection(table, default_table))
    threat_start = _find_sequence_row_index(table, "10")
    for offset, field in enumerate(SECURITY_THREAT_FIELDS):
        row_index = threat_start + offset if threat_start is not None else None
        payload[field] = _security_row_answer_value(table, row_index, SECURITY_THREAT_PROMPTS.get(field, []))
    return payload


def _parse_power_monitoring_protection(table, default_table) -> dict:
    payload = dict(POWER_MONITORING_DEFAULT_PAYLOAD)
    rows = table.findall(W + "tr")
    default_rows = default_table.findall(W + "tr")
    start = _find_sequence_row_index(table, "9")
    default_start = _find_sequence_row_index(default_table, "9")
    if start is None:
        return payload

    row9_cells = rows[start].findall(W + "tc")
    row9_value = _changed_row_value_at(table, default_table, start, default_start)
    if len(row9_cells) < 3 and row9_value:
        payload["production_control_area_protection"] = row9_value

    end = _find_sequence_row_index(table, "10") or len(rows)
    default_end = _find_sequence_row_index(default_table, "10") or len(default_rows)
    power_status = ""
    for offset, row_index in enumerate(range(start, end)):
        field = _power_monitoring_field_for_label(_row_label_text(rows[row_index]))
        if field != "is_power_monitoring_system":
            continue
        default_row_index = _matching_default_power_row_index(default_rows, default_start, default_end, offset, field)
        power_status = _power_monitoring_yes_no_value(table, default_table, row_index, default_row_index)
        payload[field] = power_status
        break

    if power_status == "NO":
        return payload

    for offset, row_index in enumerate(range(start, end)):
        field = _power_monitoring_field_for_label(_row_label_text(rows[row_index]))
        if not field or field == "is_power_monitoring_system":
            continue
        payload[field] = _security_row_answer_value(table, row_index, POWER_MONITORING_FIELD_PROMPTS.get(field, []))
    return payload


def _changed_row_value(table, default_table, row_no: int) -> str:
    return _changed_row_value_at(table, default_table, row_no, row_no)


def _changed_row_value_at(table, default_table, row_no: int | None, default_row_no: int | None = None) -> str:
    if row_no is None:
        return ""
    rows = table.findall(W + "tr")
    default_rows = default_table.findall(W + "tr")
    if row_no >= len(rows):
        return ""
    if default_row_no is None:
        default_row_no = row_no
    default_row = default_rows[default_row_no] if default_row_no < len(default_rows) else None
    cells = rows[row_no].findall(W + "tc")
    if not cells:
        return ""
    return _changed_cell_value(rows[row_no], default_row, len(cells) - 1)


def _changed_row_answer_value_at(table, default_table, row_no: int | None, default_row_no: int | None = None) -> str:
    if row_no is None:
        return ""
    rows = table.findall(W + "tr")
    default_rows = default_table.findall(W + "tr")
    if row_no >= len(rows):
        return ""
    if default_row_no is None:
        default_row_no = row_no
    cells = rows[row_no].findall(W + "tc")
    if not cells:
        return ""
    value = _answer_text(_cell_text(cells[-1]))
    default_value = ""
    if default_row_no is not None and default_row_no < len(default_rows):
        default_cells = default_rows[default_row_no].findall(W + "tc")
        default_value = _answer_text(_cell_text(default_cells[-1])) if default_cells else ""
    if _same_text(value, default_value):
        return ""
    if default_value and value.startswith(default_value):
        return value[len(default_value):].strip()
    return value


def _changed_cell_value(row, default_row, col_no: int) -> str:
    cells = row.findall(W + "tc")
    if col_no >= len(cells):
        return ""
    value = _answer_text(_cell_text(cells[col_no]))
    if default_row is None:
        return value
    default_cells = default_row.findall(W + "tc")
    default_value = _answer_text(_cell_text(default_cells[col_no])) if col_no < len(default_cells) else ""
    return "" if _same_text(value, default_value) else value


def _answer_text(value: str) -> str:
    if FILL_MARKER in value:
        return value.split(FILL_MARKER, 1)[1].strip()
    return value.strip()


def _security_sequence_answer_value(table, sequence: int, prompts: list[str]) -> str:
    row_index = _find_sequence_row_index(table, str(sequence))
    return _security_row_answer_value(table, row_index, prompts)


def _security_row_answer_value(table, row_index: int | None, prompts: list[str]) -> str:
    if row_index is None:
        return ""
    rows = table.findall(W + "tr")
    if row_index >= len(rows):
        return ""
    cells = rows[row_index].findall(W + "tc")
    if not cells:
        return ""
    value = _answer_text(_cell_text(cells[-1]))
    return _strip_answer_prompts(value, prompts)


def _strip_answer_prompts(value: str, prompts: list[str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for prompt in prompts:
        prompt = str(prompt or "").strip()
        if not prompt:
            continue
        if text.startswith(prompt):
            answer = text[len(prompt):].strip()
            return "" if _is_known_prompt_text(answer) else answer
        if _comparison_text(text) == _comparison_text(prompt):
            return ""
    return "" if _is_known_prompt_text(text) else text


def _is_known_prompt_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    normalized = _comparison_text(text)
    for prompts in (
        *SECURITY_ROW_PROMPTS.values(),
        *SECURITY_THREAT_PROMPTS.values(),
        *POWER_MONITORING_FIELD_PROMPTS.values(),
    ):
        for prompt in prompts:
            if normalized == _comparison_text(prompt):
                return True
    return False


def _changed_security_sequence_value(table, default_table, sequence: int) -> str:
    row_index = _find_sequence_row_index(table, str(sequence))
    default_row_index = _find_sequence_row_index(default_table, str(sequence))
    return _changed_row_value_at(table, default_table, row_index, default_row_index)


def _find_sequence_row_index(table, sequence: str) -> int | None:
    for index, row in enumerate(table.findall(W + "tr")):
        cells = row.findall(W + "tc")
        if cells and _comparison_text(_cell_text(cells[0])) == sequence:
            return index
    return None


def _matching_default_power_row_index(
    default_rows: list,
    default_start: int | None,
    default_end: int,
    offset: int,
    field: str,
) -> int | None:
    if default_start is None:
        return None
    for default_row_index in range(default_start, min(default_end, len(default_rows))):
        default_field = _power_monitoring_field_for_label(_row_label_text(default_rows[default_row_index]))
        if default_field == field:
            return default_row_index
    return None


def _row_label_text(row) -> str:
    cells = row.findall(W + "tc")
    if len(cells) >= 3:
        return " ".join(_cell_text(cell) for cell in cells[:-1])
    return " ".join(_cell_text(cell) for cell in cells)


def _power_monitoring_field_for_label(value: str) -> str | None:
    text = _comparison_text(value)
    for field, keywords in POWER_MONITORING_FIELD_KEYWORDS:
        if all(_comparison_text(keyword) in text for keyword in keywords):
            return field
    return None


def _power_monitoring_yes_no_value(table, default_table, row_no: int, default_row_no: int | None) -> str:
    rows = table.findall(W + "tr")
    text = _row_label_text(rows[row_no])
    checked = _checked_yes_no_text(text)
    if checked:
        return checked
    return _yes_no_text(_changed_row_value_at(table, default_table, row_no, default_row_no))


def _checked_yes_no_text(value: str) -> str:
    text = str(value or "")
    if re.search(r"[☑☒√✓]\s*是", text):
        return "YES"
    if re.search(r"[☑☒√✓]\s*否", text):
        return "NO"
    normalized = _comparison_text(text)
    if normalized.endswith("是"):
        return "YES"
    if normalized.endswith("否"):
        return "NO"
    return ""


def _is_meaningful_activity_value(value) -> bool:
    return value not in (None, "") and not _is_none_marker(value)


def _is_none_marker(value) -> bool:
    return _comparison_text(value) in NONE_MARKERS


def _comparison_text(value) -> str:
    text = _answer_text(str(value or ""))
    kept = []
    for char in text:
        category = unicodedata.category(char)
        if category[0] in {"P", "S", "Z"} or category in {"Cc", "Cf"}:
            continue
        kept.append(char)
    return "".join(kept).casefold()


def _yes_no_text(value) -> str:
    text = _comparison_text(value)
    if text in {"是", "yes", "y", "true", "1"}:
        return "YES"
    if text in {"否", "no", "n", "false", "0"}:
        return "NO"
    return "" if value is None else str(value).strip()


def _record_has_values(values: dict) -> bool:
    for value in values.values():
        if isinstance(value, bool):
            if value:
                return True
        elif value not in (None, ""):
            return True
    return False


def _save_import(project_id: str, parsed: dict) -> dict:
    session = SessionLocal()
    processor = _upsert_one(session, DataProcessorBasicSurvey, project_id)
    _assign_fields(processor, parsed["data_processor_basic"])

    business_system = _save_primary_business_system(session, project_id, parsed["business_system"])
    data_assets = _replace_records(session, project_id, DataAsset, parsed["data_assets"])
    personal_info = _replace_records(session, project_id, PersonalInfoAsset, parsed["personal_info"])
    important_data = _replace_records(session, project_id, ImportantDataAsset, parsed["important_data"])
    core_data = _replace_records(session, project_id, CoreDataAsset, parsed["core_data"])

    processing = _upsert_one(session, ProcessingActivitySurvey, project_id)
    processing_payload = parsed["processing_activity"]
    involved = processing_payload.pop("involved_activities", [])
    _assign_fields(processing, processing_payload)
    processing.involved_activities = ",".join(involved)

    security = _upsert_one(session, SecurityProtectionSurvey, project_id)
    _assign_fields(security, parsed["security_protection"])

    audit(
        "SURVEY_DOCX_IMPORT",
        "Project",
        project_id,
        after={
            "dataAssetCount": len(data_assets),
            "personalInfoCount": len(personal_info),
            "importantDataCount": len(important_data),
            "coreDataCount": len(core_data),
        },
    )
    session.commit()
    return {
        "data_processor_basic": survey_service._data_processor_basic_payload(processor),
        "business_system": business_system.to_dict(),
        "processing_activity": survey_service.processing_activity_payload(processing),
        "security_protection": survey_service.security_protection_payload(security),
        "counts": {
            "dataAssets": len(data_assets),
            "personalInfo": len(personal_info),
            "importantData": len(important_data),
            "coreData": len(core_data),
        },
    }


def _upsert_one(session, model, project_id: str):
    row = session.query(model).filter_by(project_id=project_id, deleted=False).first()
    if not row:
        row = model(project_id=project_id)
        session.add(row)
    return row


def _save_primary_business_system(session, project_id: str, payload: dict) -> BusinessSystem:
    rows = (
        session.query(BusinessSystem)
        .filter(BusinessSystem.project_id == project_id, BusinessSystem.deleted.is_(False))
        .order_by(BusinessSystem.created_at.asc())
        .all()
    )
    record = rows[0] if rows else BusinessSystem(project_id=project_id)
    if not rows:
        session.add(record)
    for stale in rows[1:]:
        stale.deleted = True
    _assign_fields(record, payload)
    return record


def _replace_records(session, project_id: str, model, rows: list[dict]) -> list:
    session.query(model).filter_by(project_id=project_id, deleted=False).update({"deleted": True})
    records = [model(project_id=project_id, **row) for row in rows]
    session.add_all(records)
    return records


def _assign_fields(record, payload: dict, skip_empty: bool = False) -> None:
    columns = {column.key for column in record.__mapper__.columns}
    ignored = {"id", "project_id", "tenant_id", "created_by", "created_at", "updated_by", "updated_at", "deleted"}
    for key, value in payload.items():
        if key in columns and key not in ignored:
            if skip_empty and value in (None, ""):
                continue
            setattr(record, key, "" if value is None else value)


def _current_survey_data(project_id: str) -> dict:
    session = SessionLocal()
    return {
        "data_processor": session.query(DataProcessorBasicSurvey).filter_by(project_id=project_id, deleted=False).first(),
        "business_system": (
            session.query(BusinessSystem)
            .filter_by(project_id=project_id, deleted=False)
            .order_by(BusinessSystem.created_at.asc())
            .first()
        ),
        "data_assets": _active_rows(session, project_id, DataAsset),
        "personal_info": _active_rows(session, project_id, PersonalInfoAsset),
        "important_data": _active_rows(session, project_id, ImportantDataAsset),
        "core_data": _active_rows(session, project_id, CoreDataAsset),
        "processing": session.query(ProcessingActivitySurvey).filter_by(project_id=project_id, deleted=False).first(),
        "security": session.query(SecurityProtectionSurvey).filter_by(project_id=project_id, deleted=False).first(),
    }


def _active_rows(session, project_id: str, model) -> list:
    return (
        session.query(model)
        .filter(model.project_id == project_id, model.deleted.is_(False))
        .order_by(model.created_at.asc())
        .all()
    )


def _fill_data_processor(table, row: DataProcessorBasicSurvey | None) -> None:
    for field, row_no in DATA_PROCESSOR_ROWS.items():
        _set_row_last_cell(table, row_no, _row_value(row, field))


def _fill_business_system(table, row: BusinessSystem | None) -> None:
    for field, row_no in BUSINESS_SYSTEM_ROWS.items():
        if field == "data_scopes":
            value = _format_data_scopes(getattr(row, field, "") if row else "")
        else:
            value = _row_value(row, field)
        _set_row_last_cell(table, row_no, value)


def _fill_record_table(table, rows: list, fields: list[str], start_row: int) -> None:
    table_rows = table.findall(W + "tr")
    template_row = deepcopy(table_rows[start_row]) if len(table_rows) > start_row else _new_row(len(fields))
    for old_row in table_rows[start_row:]:
        table.remove(old_row)
    for record in rows:
        row = deepcopy(template_row)
        for col_no, field in enumerate(fields):
            value = getattr(record, field, "")
            if field in {"classified", "personal_info"}:
                value = _bool_text(value)
            _set_cell_text(_ensure_cell(row, col_no), value)
        table.append(row)


def _fill_processing_activity(table, row: ProcessingActivitySurvey | None) -> None:
    for _code, field, row_no in PROCESSING_ROWS:
        _set_row_last_cell_with_marker(table, row_no, _row_value(row, field))


def _fill_security_protection(table, row: SecurityProtectionSurvey | None) -> None:
    for field, row_no in SECURITY_ROWS.items():
        _set_row_last_cell_with_marker(table, row_no, _row_value(row, field))


def _write_document_xml(template: bytes, document) -> bytes:
    output = BytesIO()
    xml = ET.tostring(document, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(BytesIO(template)) as zin, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, xml if item.filename == "word/document.xml" else zin.read(item.filename))
    return output.getvalue()


def _set_row_last_cell(table, row_no: int, value) -> None:
    rows = table.findall(W + "tr")
    if row_no >= len(rows):
        return
    cells = rows[row_no].findall(W + "tc")
    if cells:
        _set_cell_text(cells[-1], value)


def _set_row_last_cell_with_marker(table, row_no: int, value) -> None:
    rows = table.findall(W + "tr")
    if row_no >= len(rows):
        return
    cells = rows[row_no].findall(W + "tc")
    if not cells:
        return
    cell = cells[-1]
    base = _strip_existing_fill(_cell_text(cell))
    text = f"{base}\n{FILL_MARKER}{value}" if value not in (None, "") else base
    _set_cell_text(cell, text)


def _strip_existing_fill(value: str) -> str:
    return value.split(FILL_MARKER, 1)[0].strip() if FILL_MARKER in value else value


def _cell_text(cell) -> str:
    return "".join(text.text or "" for text in cell.iter(W + "t")).strip()


def _set_cell_text(cell, value) -> None:
    text = "" if value is None else str(value)
    texts = list(cell.iter(W + "t"))
    if texts:
        texts[0].text = text
        for extra in texts[1:]:
            extra.text = ""
        return
    paragraph = cell.find(W + "p")
    if paragraph is None:
        paragraph = ET.SubElement(cell, W + "p")
    run = ET.SubElement(paragraph, W + "r")
    node = ET.SubElement(run, W + "t")
    node.text = text


def _ensure_cell(row, col_no: int):
    cells = row.findall(W + "tc")
    while len(cells) <= col_no:
        row.append(_new_cell())
        cells = row.findall(W + "tc")
    return cells[col_no]


def _new_row(cell_count: int):
    row = ET.Element(W + "tr")
    for _ in range(cell_count):
        row.append(_new_cell())
    return row


def _new_cell():
    cell = ET.Element(W + "tc")
    paragraph = ET.SubElement(cell, W + "p")
    run = ET.SubElement(paragraph, W + "r")
    ET.SubElement(run, W + "t")
    return cell


def _row_value(row, field: str) -> str:
    if row is None:
        return ""
    value = getattr(row, field, "")
    if isinstance(value, bool):
        return _bool_text(value)
    return "" if value is None else value


def _parse_data_scopes(value: str) -> list[str]:
    if not value:
        return []
    selected = []
    for name in DATA_SCOPE_NAMES:
        checked = re.search(rf"[☑√■]\s*{re.escape(name)}", value)
        unchecked = re.search(rf"[□]\s*{re.escape(name)}", value)
        if checked or (not unchecked and name in value):
            selected.append(name)
    return selected


def _format_data_scopes(value) -> str:
    selected = _split_text(value)
    return " ".join(f"{'☑' if name in selected else '□'}{name}" for name in DATA_SCOPE_NAMES)


def _split_text(value) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [item.strip() for item in re.split(r"[、,，;\s]+", str(value)) if item.strip()]


def _yes_no_bool(value: str) -> bool | None:
    text = str(value or "").strip().lower()
    if text in {"是", "yes", "y", "true", "1"}:
        return True
    if text in {"否", "no", "n", "false", "0"}:
        return False
    return None


def _bool_text(value) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return ""


def _same_text(left: str, right: str) -> bool:
    return " ".join(str(left or "").split()) == " ".join(str(right or "").split())


def _raise_import_validation(errors: list[dict], cause: Exception | None = None):
    error = BusinessError("IMPORT_VALIDATION_FAILED", "导入文件存在格式错误", data={"errors": errors})
    if cause:
        raise error from cause
    raise error


def _import_error(table_name, row_no: int, field: str, reason: str) -> dict:
    return {"tableName": table_name, "rowNo": row_no, "field": field, "reason": reason}
