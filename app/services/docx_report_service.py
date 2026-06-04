from __future__ import annotations

import copy
import io
import mimetypes
import re
import struct
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

from app.models import (
    AssessedOrganization,
    AssessmentTeamMember,
    AssessmentTemplateItem,
    BusinessSystem,
    ClientTeamMember,
    CoreDataAsset,
    DataAsset,
    DataProcessingActivity,
    EvaluationRecord,
    FileObject,
    ImportantDataAsset,
    PersonalInfoAsset,
    ProcessingActivitySurvey,
    ProjectAssessmentItem,
    ProjectBasicInfo,
    ProjectContact,
    ProjectReference,
    ProjectRiskSummaryRecord,
    RiskMatrix,
    ScoreModel,
    ScoreModelRange,
    SecurityProtectionSurvey,
)
from app.services import file_service
from app.services.evaluation_service import calculate_project_score_snapshot


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
XML = "http://www.w3.org/XML/1998/namespace"

NS = {"w": W, "r": R}
IMAGE_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
EMU_PER_INCH = 914400

for prefix, uri in [
    ("w", W),
    ("r", R),
    ("wp", WP),
    ("a", A),
    ("pic", PIC),
    ("mc", "http://schemas.openxmlformats.org/markup-compatibility/2006"),
    ("w14", "http://schemas.microsoft.com/office/word/2010/wordml"),
    ("w15", "http://schemas.microsoft.com/office/word/2012/wordml"),
    ("w16du", "http://schemas.microsoft.com/office/word/2023/wordml/word16du"),
]:
    ET.register_namespace(prefix, uri)


ACTIVITY_NAMES = {
    "COLLECT": "数据收集",
    "STORE": "数据存储",
    "TRANSFER": "数据传输",
    "USE": "数据使用和加工",
    "USE_PROCESS": "数据使用和加工",
    "USEPROCESS": "数据使用和加工",
    "PROVIDE": "数据提供",
    "PUBLIC": "数据公开",
    "DELETE": "数据删除",
}
ACTIVITY_PAYLOAD_KEYS = {
    "COLLECT": "collect",
    "STORE": "store",
    "TRANSFER": "transfer",
    "USE": "use_process",
    "USE_PROCESS": "use_process",
    "USEPROCESS": "use_process",
    "PROVIDE": "provide",
    "PUBLIC": "public",
    "DELETE": "delete",
}
ACTIVITY_DETAIL_FIELDS = [
    (("scenarios",), "处理场景"),
    (("methods",), "处理方式"),
    (("data_types", "data_type"), "数据类型"),
    (("data_levels", "data_level"), "数据级别"),
    (("data_names", "data_name"), "数据名称"),
    (("scale",), "数据规模"),
    (("purpose",), "处理目的"),
    (("frequency",), "处理频率"),
    (("security_measures", "protection_measures"), "安全措施"),
]
MISSING_TEXT_MARKERS = ("尚未填写", "尚未配置", "未填写", "未上传")
RESULT_NAMES = {
    "COMPLIANT": "符合",
    "PARTIAL": "基本符合",
    "NON_COMPLIANT": "不符合",
    "NOT_APPLICABLE": "不适用",
}
POSSIBILITY_NAMES = {"HIGH": "高", "MEDIUM": "中", "LOW": "低"}
HARM_NAMES = {
    "VERY_HIGH": "很高",
    "HIGH": "高",
    "RELATIVELY_HIGH": "较高",
    "MEDIUM": "中",
    "LOW": "低",
}
RISK_NAMES = {
    "MAJOR": "重大安全风险",
    "HIGH": "高安全风险",
    "MEDIUM": "中安全风险",
    "LOW": "低安全风险",
    "SLIGHT": "轻微安全风险",
}
SECURITY_FIELD_NAMES = {
    "classified_protection_assessment": "等级保护测评",
    "commercial_cryptography_assessment": "商用密码应用安全性评估",
    "security_testing_last_three_years": "近三年安全检测",
    "historical_issue_rectification": "历史问题整改",
    "dedicated_data_security_org": "专职数据安全机构",
    "data_security_owner": "数据安全负责人",
    "security_devices": "安全设备",
    "security_policy_update_frequency": "安全策略更新频率",
    "identity_auth_measures": "身份鉴别措施",
    "least_privilege": "最小权限",
    "vulnerability_scan_frequency": "漏洞扫描频率",
    "high_risk_vulnerability_fix_period": "高危漏洞修复周期",
    "remote_management_tools": "远程管理工具",
    "remote_access_authorized_objects": "远程访问授权对象",
    "password_complexity_requirement": "口令复杂度要求",
    "transmission_encryption": "传输加密",
    "storage_encryption": "存储加密",
    "desensitization": "数据脱敏",
    "threat_types_detected_last_year": "上年度发现的威胁类型",
    "data_security_incident_frequency": "数据安全事件发生频率",
    "data_security_incident_types": "数据安全事件类型",
    "incident_detail_description": "事件情况",
}


def generate_document(session, project, template_path: str, selected_sections: list[str] | None = None) -> bytes:
    template = Path(template_path)
    if not template.exists():
        raise FileNotFoundError(f"Report template not found: {template}")

    with zipfile.ZipFile(template, "r") as source:
        entries = {info.filename: source.read(info.filename) for info in source.infolist()}
        infos = {info.filename: info for info in source.infolist()}

    document = ET.fromstring(entries["word/document.xml"])
    relationships = ET.fromstring(entries["word/_rels/document.xml.rels"])
    content_types = ET.fromstring(entries["[Content_Types].xml"])
    data = _load_report_data(session, project)

    _fill_paragraphs(document, data)
    tables = list(document.iter(_qn(W, "tbl")))
    _fill_basic_information_table(tables[0], data)
    _fill_indicator_tables(document, tables[1:5], data)
    _fill_table(tables[5], _business_system_rows(data), preserve_blank=True)
    _fill_table(tables[6], _assessment_team_rows(data))
    _fill_table(tables[7], _client_team_rows(data))
    _fill_table(tables[8], _data_asset_rows(data))
    _fill_optional_table(document, tables[9], _personal_info_rows(data), "表2 个人信息情况")
    _fill_optional_table(document, tables[10], _important_data_rows(data), "表3 重要数据情况")
    _fill_optional_table(document, tables[11], _core_data_rows(data), "表4 核心数据情况")
    _fill_table(tables[12], _processing_activity_rows(data))
    _fill_table(tables[13], _reference_rows(data))
    _fill_table(tables[14], _evaluation_issue_rows(data))
    _fill_table(tables[15], _risk_source_rows(data))
    _fill_table(tables[18], _harm_rows(data))
    _fill_table(tables[20], _score_range_rows(data))
    _fill_table(tables[21], _score_result_rows(data))
    _fill_table(tables[22], _score_summary_rows(data))
    _fill_risk_matrix_table(tables[23], data)
    _fill_table(tables[24], _risk_rows(data))
    _fill_table(tables[25], _suggestion_rows(data))

    media_entries = {}
    if not selected_sections or "SURVEY" in selected_sections:
        _insert_business_system_image(
            document,
            relationships,
            content_types,
            data,
            comment_id="41",
            file_field="topology_file_id",
            fallback_text="未上传网络拓扑图",
            media_entries=media_entries,
        )
        _insert_business_system_image(
            document,
            relationships,
            content_types,
            data,
            comment_id="52",
            file_field="business_flow_file_id",
            fallback_text="未上传数据流转图",
            media_entries=media_entries,
        )

    _apply_section_selection(document, selected_sections)
    _renumber_table_captions(document)
    _mark_missing_content_red(document)
    _remove_comments(document, relationships, content_types, entries)
    _set_update_fields(entries)

    entries["word/document.xml"] = ET.tostring(document, encoding="utf-8", xml_declaration=True)
    entries["word/_rels/document.xml.rels"] = ET.tostring(relationships, encoding="utf-8", xml_declaration=True)
    entries["[Content_Types].xml"] = ET.tostring(content_types, encoding="utf-8", xml_declaration=True)
    entries.update(media_entries)

    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as target:
        for name, content in entries.items():
            if name in infos:
                target.writestr(infos[name], content)
            else:
                target.writestr(name, content, compress_type=zipfile.ZIP_DEFLATED)
    return result.getvalue()


def _set_update_fields(entries: dict) -> None:
    settings_name = "word/settings.xml"
    if settings_name not in entries:
        return
    settings = ET.fromstring(entries[settings_name])
    update_fields = settings.find(_qn(W, "updateFields"))
    if update_fields is None:
        update_fields = ET.SubElement(settings, _qn(W, "updateFields"))
    update_fields.set(_qn(W, "val"), "true")
    entries[settings_name] = ET.tostring(settings, encoding="utf-8", xml_declaration=True)


def _apply_section_selection(document, selected_sections: list[str] | None) -> None:
    selected = set(selected_sections or [])
    if not selected or selected.issuperset({"BASIC_INFO", "PLAN", "SURVEY", "EVALUATION", "RISK_SUMMARY", "SUGGESTIONS"}):
        return
    markers = [
        ("BASIC_INFO", "评估背景"),
        ("PLAN", "评估工作开展情况"),
        ("SURVEY", "数据安全基本信息"),
        ("EVALUATION", "数据安全风险识别"),
        ("RISK_SUMMARY", "风险分析与评价"),
        ("SUGGESTIONS", "安全风险处理"),
    ]
    body = document.find(_qn(W, "body"))
    children = list(body)
    positions = {}
    for section, heading in markers:
        position = next((index for index, child in enumerate(children) if _element_text(child).strip() == heading), None)
        if position is not None:
            positions[section] = position
    ordered = [(section, positions[section]) for section, _heading in markers if section in positions]
    for index, (section, start) in enumerate(ordered):
        end = ordered[index + 1][1] if index + 1 < len(ordered) else len(children)
        if section in selected:
            continue
        for child in children[start:end]:
            if child.tag != _qn(W, "sectPr") and child in list(body):
                body.remove(child)


def _load_report_data(session, project) -> dict:
    project_id = project.id
    basic = session.query(ProjectBasicInfo).filter_by(project_id=project_id, deleted=False).first()
    organization = session.query(AssessedOrganization).filter_by(project_id=project_id, deleted=False).first()
    contacts = _rows(session, ProjectContact, project_id)
    assessment_team = _rows(session, AssessmentTeamMember, project_id)
    client_team = _rows(session, ClientTeamMember, project_id)
    systems = _rows(session, BusinessSystem, project_id)
    assets = _rows(session, DataAsset, project_id)
    personal_info = _rows(session, PersonalInfoAsset, project_id)
    important_data = _rows(session, ImportantDataAsset, project_id)
    core_data = _rows(session, CoreDataAsset, project_id)
    legacy_activities = _rows(session, DataProcessingActivity, project_id)
    references = _rows(session, ProjectReference, project_id)
    assessment_items = _rows(session, ProjectAssessmentItem, project_id)
    indicator_items = assessment_items
    if not indicator_items and project.assessment_template_id:
        indicator_items = (
            session.query(AssessmentTemplateItem)
            .filter(
                AssessmentTemplateItem.template_id == project.assessment_template_id,
                AssessmentTemplateItem.deleted.is_(False),
            )
            .order_by(AssessmentTemplateItem.sort_order.asc())
            .all()
        )

    evaluation_records = _rows(session, EvaluationRecord, project_id)
    records_by_item = {row.item_id: row for row in evaluation_records}
    evaluation_rows = [(item, records_by_item.get(item.id)) for item in assessment_items]
    risks = (
        session.query(ProjectRiskSummaryRecord)
        .filter(
            ProjectRiskSummaryRecord.project_id == project_id,
            ProjectRiskSummaryRecord.deleted.is_(False),
            ProjectRiskSummaryRecord.current.is_(True),
        )
        .order_by(ProjectRiskSummaryRecord.created_at.asc())
        .all()
    )
    processing = session.query(ProcessingActivitySurvey).filter_by(project_id=project_id, deleted=False).first()
    security = session.query(SecurityProtectionSurvey).filter_by(project_id=project_id, deleted=False).first()
    score_model = session.get(ScoreModel, project.score_model_id) if project.score_model_id else None
    score_ranges = []
    if score_model:
        score_ranges = (
            session.query(ScoreModelRange)
            .filter(ScoreModelRange.score_model_id == score_model.id, ScoreModelRange.deleted.is_(False))
            .order_by(ScoreModelRange.min_score.asc())
            .all()
        )
    risk_matrix = session.get(RiskMatrix, project.risk_matrix_id) if project.risk_matrix_id else None
    score = calculate_project_score_snapshot(session, project)
    return {
        "project": project,
        "basic": basic,
        "organization": organization,
        "contacts": contacts,
        "assessment_team": assessment_team,
        "client_team": client_team,
        "systems": systems,
        "assets": assets,
        "personal_info": personal_info,
        "important_data": important_data,
        "core_data": core_data,
        "legacy_activities": legacy_activities,
        "references": references,
        "assessment_items": assessment_items,
        "indicator_items": indicator_items,
        "evaluation_rows": evaluation_rows,
        "risks": risks,
        "processing": processing.payload if processing and processing.payload else {},
        "security": security.payload if security and security.payload else {},
        "score_model": score_model,
        "score_ranges": score_ranges,
        "risk_matrix": risk_matrix.matrix_json if risk_matrix and isinstance(risk_matrix.matrix_json, dict) else {},
        "score": score,
    }


def _rows(session, model, project_id: str) -> list:
    return (
        session.query(model)
        .filter(model.project_id == project_id, model.deleted.is_(False))
        .order_by(model.created_at.asc())
        .all()
    )


def _fill_paragraphs(document, data: dict) -> None:
    project = data["project"]
    basic = data["basic"]
    organization = data["organization"]
    system = data["systems"][0] if data["systems"] else None
    target = _value(getattr(basic, "assessment_target", None), project.project_name)
    org_name = _value(getattr(organization, "name", None), project.assessment_org)
    activities = _activity_names(data)
    domains = _indicator_domain_names(data)
    report_date = _format_date(datetime.now())
    end_date = _format_date(getattr(basic, "plan_end_date", None)) or report_date
    replacements = {
        "0": project.project_code,
        "1": org_name,
        "2": target,
        "3": org_name,
        "4": report_date,
        "13": target,
        "14": end_date,
        "15": org_name,
        "16": target,
        "19": target,
        "20": _domain_phrase(domains),
        "25": target,
        "26": target,
        "27": "、".join(activities) or "尚未填写的数据处理活动",
        "30": org_name,
        "33": _format_date(getattr(basic, "plan_start_date", None)) or "未填写",
        "34": end_date,
        "35": target,
        "36": org_name,
        "37": target,
        "38": _value(getattr(organization, "description", None), "未填写公司简介"),
        "39": _value(getattr(basic, "system_description", None), "未填写系统描述"),
        "40": _value(getattr(system, "business_function", None), "未填写业务功能描述"),
        "42": f" {target}",
        "43": org_name,
        "44": target,
        "49": target,
        "50": f"{'、'.join(activities)}阶段" if activities else "尚未填写的数据处理活动阶段",
        "53": target,
        "55": org_name,
        "56": _security_summary(data),
        "58": _domain_phrase(domains),
        "59": target,
        "60": target,
        "61": target,
        "62": target,
        "64": target,
        "65": target,
        "66": target,
        "68": target,
        "69": target,
        "70": target,
        "75": _score_value_phrase(data),
        "76": target,
        "77": target,
        "78": target,
        "83": target,
        "84": target,
        "85": target,
        "87": target,
        "88": target,
        "89": _evaluation_summary(data, "数据安全管理"),
        "90": _security_summary(data),
        "91": _processing_summary(data),
        "92": target,
        "93": _risk_count_phrase(data),
        "94": target,
        "95": target,
    }
    for comment_id, value in replacements.items():
        _replace_comment_text(document, comment_id, value)

    _fill_paragraph_block(
        document,
        "本次评估所依据的法律法规",
        "本次评估所参考的标准规范",
        _named_values(getattr(basic, "laws", None)) or ["未填写评估所依据的法律法规"],
        lambda text: bool(re.fullmatch(r"X+", text)),
    )
    _fill_paragraph_block(
        document,
        "本次评估所参考的标准规范",
        "评估要求",
        _named_values(getattr(basic, "standards", None)) or ["未填写评估所参考的标准规范"],
        lambda text: bool(re.fullmatch(r"X+", text)),
    )
    _fill_paragraph_block(
        document,
        "数据处理活动情况",
        "业务架构及数据流转示意图如下所示：",
        _activity_narratives(data),
        lambda text: "阶段：" in text,
    )
    _replace_all_text(
        document,
        {
            "XXXXXX公司": org_name,
            "XXXX公司": org_name,
            "XXXX系统": target,
            "XX系统": target,
            "20XX年XX月XX日": report_date,
        },
    )


def _fill_basic_information_table(table, data: dict) -> None:
    basic = data["basic"]
    organization = data["organization"]
    contact = data["contacts"][0] if data["contacts"] else None
    leader = data["assessment_team"][0] if data["assessment_team"] else None
    target = _value(getattr(basic, "assessment_target", None), data["project"].project_name)
    values = {
        (1, 1): target,
        (2, 1): _value(getattr(organization, "name", None), data["project"].assessment_org),
        (2, 3): _value(getattr(organization, "credit_code", None)),
        (3, 1): _value(getattr(organization, "address", None)),
        (3, 3): _value(getattr(organization, "postal_code", None)),
        (4, 2): _value(getattr(contact, "name", None)),
        (4, 4): _value(getattr(contact, "title", None)),
        (5, 2): _value(getattr(contact, "department", None)),
        (5, 4): _value(getattr(contact, "phone", None)),
        (6, 2): _value(getattr(contact, "mobile", None)),
        (6, 4): _value(getattr(contact, "email", None)),
        (8, 1): _value(getattr(organization, "data_security_owner", None)),
        (15, 2): _value(getattr(leader, "name", None), "未填写"),
        (15, 4): _offset_date(getattr(basic, "plan_end_date", None), -4) or "未填写",
        (16, 4): _offset_date(getattr(basic, "plan_end_date", None), -2) or "未填写",
        (17, 4): _format_date(getattr(basic, "plan_end_date", None)) or "未填写",
    }
    rows = table.findall(_qn(W, "tr"))
    for (row_index, cell_index), value in values.items():
        cells = rows[row_index].findall(_qn(W, "tc"))
        _set_cell_text(cells[cell_index], value)


def _fill_indicator_tables(document, tables: list, data: dict) -> None:
    grouped = defaultdict(Counter)
    for item in data["indicator_items"]:
        grouped[_value(item.sheet_name, "其他")][_value(item.category, "其他")] += 1
    expected = ["数据安全管理", "数据安全技术", "数据处理活动", "个人信息保护"]
    for table, domain in zip(tables, expected):
        rows = [[index, name, count] for index, (name, count) in enumerate(grouped.get(domain, {}).items(), start=1)]
        if domain == "个人信息保护" and not rows:
            _remove_table_and_caption(document, table, "个人信息保护指标")
        else:
            _fill_table(table, rows)


def _fill_optional_table(document, table, rows: list[list], caption: str) -> None:
    if rows:
        _fill_table(table, rows)
    else:
        _remove_table_and_caption(document, table, caption)


def _fill_table(table, rows: list[list], keep_empty_row: bool = True, preserve_blank: bool = False) -> None:
    existing = table.findall(_qn(W, "tr"))
    if len(existing) < 2:
        return
    template_row = copy.deepcopy(existing[1])
    column_count = len(template_row.findall(_qn(W, "tc")))
    for row in existing[1:]:
        table.remove(row)
    if not rows and keep_empty_row:
        rows = [["-"] * column_count]
    for values in rows:
        if len(values) != column_count:
            raise ValueError(f"Report table requires {column_count} columns, received {len(values)}.")
        row = copy.deepcopy(template_row)
        _strip_comment_nodes(row)
        for cell, value in zip(row.findall(_qn(W, "tc")), values):
            _set_cell_text(cell, value, default="" if preserve_blank else "-")
        table.append(row)


def _fill_risk_matrix_table(table, data: dict) -> None:
    matrix = data["risk_matrix"]
    rows = table.findall(_qn(W, "tr"))
    possibility_levels = ["HIGH", "MEDIUM", "LOW"]
    harm_levels = ["VERY_HIGH", "HIGH", "RELATIVELY_HIGH", "MEDIUM", "LOW"]
    for row_index, possibility in enumerate(possibility_levels, start=2):
        cells = rows[row_index].findall(_qn(W, "tc"))
        _set_cell_text(cells[1], POSSIBILITY_NAMES[possibility])
        values = matrix.get(possibility) or {}
        for cell_index, harm in enumerate(harm_levels, start=2):
            _set_cell_text(cells[cell_index], RISK_NAMES.get(values.get(harm), _value(values.get(harm))))


def _business_system_rows(data: dict) -> list[list]:
    target = _value(getattr(data["basic"], "assessment_target", None), data["project"].project_name)
    description = _value(getattr(data["basic"], "system_description", None), "未填写系统描述")
    systems = data["systems"] or [None]
    return [
        [
            "",
            _value(getattr(system, "system_name", None), target),
            description,
            "",
        ]
        for system in systems
    ]


def _assessment_team_rows(data: dict) -> list[list]:
    return [[row.name, row.organization, row.role] for row in data["assessment_team"]]


def _client_team_rows(data: dict) -> list[list]:
    return [[row.name, row.department, row.position] for row in data["client_team"]]


def _data_asset_rows(data: dict) -> list[list]:
    return [
        [
            row.data_name,
            row.data_form,
            row.data_scope,
            row.data_scale,
            row.data_source,
            row.storage_location,
            row.flow_description,
            _yes_no(row.classified),
            row.data_category,
            row.data_level,
            _yes_no(row.personal_info),
        ]
        for row in data["assets"]
    ]


def _personal_info_rows(data: dict) -> list[list]:
    return [
        [row.data_name, row.data_category or row.category, row.scale, row.sensitivity, row.data_source, row.business_flow]
        for row in data["personal_info"]
    ]


def _important_data_rows(data: dict) -> list[list]:
    return [[row.data_name, row.data_category or row.category, row.scale, row.data_source, row.business_flow] for row in data["important_data"]]


def _core_data_rows(data: dict) -> list[list]:
    return [[row.data_name, row.data_category or row.category, row.scale, row.data_source, row.business_flow] for row in data["core_data"]]


def _processing_activity_rows(data: dict) -> list[list]:
    selected = _activity_codes(data)
    assets = data["assets"]
    asset_types = _unique_join([row.data_category or row.data_form for row in assets])
    asset_levels = _unique_join([row.data_level for row in assets])
    asset_names = _unique_join([row.data_name for row in assets])
    legacy_by_type = {str(row.activity_type or "").upper(): row for row in data["legacy_activities"]}
    rows = []
    for index, code in enumerate(selected, start=1):
        detail = _activity_detail(data["processing"], code)
        legacy = legacy_by_type.get(code)
        rows.append(
            [
                index,
                _activity_name(code),
                _payload_text(detail, "data_types", "data_type") or asset_types,
                _payload_text(detail, "data_level", "data_levels") or asset_levels,
                _payload_text(detail, "data_names", "data_name") or asset_names,
                _payload_text(detail, "purpose", "scenarios") or getattr(legacy, "description", None),
                _payload_text(detail, "frequency") or "-",
            ]
        )
    return rows


def _reference_rows(data: dict) -> list[list]:
    return [[index, row.type, row.name, "-", "-"] for index, row in enumerate(data["references"], start=1)]


def _evaluation_issue_rows(data: dict) -> list[list]:
    rows = []
    for item, record in data["evaluation_rows"]:
        if not record or record.evaluation_result not in {"PARTIAL", "NON_COMPLIANT"}:
            continue
        rows.append(
            [
                len(rows) + 1,
                item.sheet_name or item.category,
                item.category or item.subcategory,
                item.check_point,
                record.evaluation_record,
                RESULT_NAMES.get(record.evaluation_result, record.evaluation_result),
            ]
        )
    return rows


def _risk_source_rows(data: dict) -> list[list]:
    return [
        [
            index,
            _list_text(row.risk_types),
            row.risk_description,
            row.risk_source_description,
            row.related_data,
            _activity_list_text(row.related_activities),
        ]
        for index, row in enumerate(data["risks"], start=1)
    ]


def _harm_rows(data: dict) -> list[list]:
    return [
        [index, _list_text(row.risk_types), row.risk_description, HARM_NAMES.get(row.harm_level, row.harm_level)]
        for index, row in enumerate(data["risks"], start=1)
    ]


def _score_range_rows(data: dict) -> list[list]:
    rows = []
    for row in data["score_ranges"]:
        left = "[" if row.include_min else "（"
        right = "]" if row.include_max else "）"
        rows.append([POSSIBILITY_NAMES.get(row.level, row.level), f"{left}{_number(row.min_score)}，{_number(row.max_score)}{right}"])
    return rows


def _score_result_rows(data: dict) -> list[list]:
    scores = getattr(data["score_model"], "result_scores", None) or {"COMPLIANT": 1, "PARTIAL": 0.5, "NON_COMPLIANT": 0}
    return [[RESULT_NAMES[level], _number(scores.get(level))] for level in ["COMPLIANT", "PARTIAL", "NON_COMPLIANT"]]


def _score_summary_rows(data: dict) -> list[list]:
    basic = data["basic"]
    target = _value(getattr(basic, "assessment_target", None), data["project"].project_name)
    score = data["score"]
    return [[target, _number(score["score"]), POSSIBILITY_NAMES.get(score["possibilityLevel"], score["possibilityLevel"])]]


def _risk_rows(data: dict) -> list[list]:
    return [
        [
            index,
            _list_text(row.risk_types),
            row.risk_description,
            HARM_NAMES.get(row.harm_level, row.harm_level),
            POSSIBILITY_NAMES.get(row.possibility_level, row.possibility_level),
            row.risk_source_description,
            RISK_NAMES.get(row.risk_level, row.risk_level),
            _activity_list_text(row.related_activities),
        ]
        for index, row in enumerate(data["risks"], start=1)
    ]


def _suggestion_rows(data: dict) -> list[list]:
    return [
        [
            index,
            _list_text(row.risk_types),
            row.risk_description,
            row.risk_source_description,
            RISK_NAMES.get(row.risk_level, row.risk_level),
            row.remediation_suggestion,
        ]
        for index, row in enumerate(data["risks"], start=1)
    ]


def _insert_business_system_image(
    document,
    relationships,
    content_types,
    data: dict,
    comment_id: str,
    file_field: str,
    fallback_text: str,
    media_entries: dict,
) -> None:
    paragraph = _comment_paragraph(document, comment_id)
    if paragraph is None:
        return
    system = data["systems"][0] if data["systems"] else None
    file_id = getattr(system, file_field, None) if system else None
    file_row = None
    if file_id:
        candidate = file_service.get_file(file_id)
        if candidate and candidate.project_id in (None, data["project"].id):
            file_row = candidate
    if not file_row:
        _set_paragraph_text(paragraph, fallback_text)
        return

    content = file_service.read_bytes(file_row)
    extension = _image_extension(file_row, content)
    if not extension:
        _set_paragraph_text(paragraph, f"{fallback_text}（上传文件格式不支持插入 Word）")
        return
    relationship_id = _next_relationship_id(relationships)
    media_name = f"word/media/report-image-{len(media_entries) + 1}.{extension}"
    relationships.append(
        ET.Element(
            _qn(REL, "Relationship"),
            {
                "Id": relationship_id,
                "Type": IMAGE_REL_TYPE,
                "Target": media_name.removeprefix("word/"),
            },
        )
    )
    _ensure_image_content_type(content_types, extension)
    media_entries[media_name] = content
    width, height = _image_extent(content)
    _set_paragraph_drawing(paragraph, relationship_id, width, height, Path(file_row.file_name).name, len(media_entries))


def _image_extension(file_row: FileObject, content: bytes) -> str | None:
    suffix = Path(file_row.file_name or "").suffix.lower().lstrip(".")
    if suffix == "jpeg":
        suffix = "jpg"
    if suffix in {"png", "jpg", "gif", "bmp"}:
        return suffix
    guessed = (mimetypes.guess_extension(file_row.content_type or "") or "").lower().lstrip(".")
    return "jpg" if guessed == "jpeg" else guessed if guessed in {"png", "jpg", "gif", "bmp"} else None


def _image_extent(content: bytes) -> tuple[int, int]:
    size = _image_pixel_size(content)
    if not size:
        return int(5.5 * EMU_PER_INCH), int(3.2 * EMU_PER_INCH)
    pixel_width, pixel_height = size
    width = pixel_width / 96 * EMU_PER_INCH
    height = pixel_height / 96 * EMU_PER_INCH
    scale = min(1.0, (6.0 * EMU_PER_INCH) / width, (4.5 * EMU_PER_INCH) / height)
    return max(int(width * scale), 1), max(int(height * scale), 1)


def _image_pixel_size(content: bytes) -> tuple[int, int] | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 24:
        return struct.unpack(">II", content[16:24])
    if content.startswith((b"GIF87a", b"GIF89a")) and len(content) >= 10:
        return struct.unpack("<HH", content[6:10])
    if content.startswith(b"BM") and len(content) >= 26:
        return struct.unpack("<II", content[18:26])
    if content.startswith(b"\xff\xd8"):
        index = 2
        while index + 9 < len(content):
            if content[index] != 0xFF:
                index += 1
                continue
            marker = content[index + 1]
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                height, width = struct.unpack(">HH", content[index + 5:index + 9])
                return width, height
            if index + 4 > len(content):
                break
            length = struct.unpack(">H", content[index + 2:index + 4])[0]
            index += max(length + 2, 2)
    return None


def _set_paragraph_drawing(paragraph, relationship_id: str, width: int, height: int, name: str, image_index: int) -> None:
    _clear_paragraph_content(paragraph)
    properties = paragraph.find(_qn(W, "pPr"))
    if properties is None:
        properties = ET.Element(_qn(W, "pPr"))
        paragraph.insert(0, properties)
    justification = properties.find(_qn(W, "jc"))
    if justification is None:
        justification = ET.SubElement(properties, _qn(W, "jc"))
    justification.set(_qn(W, "val"), "center")

    run = ET.SubElement(paragraph, _qn(W, "r"))
    drawing = ET.SubElement(run, _qn(W, "drawing"))
    inline = ET.SubElement(drawing, _qn(WP, "inline"), {"distT": "0", "distB": "0", "distL": "0", "distR": "0"})
    ET.SubElement(inline, _qn(WP, "extent"), {"cx": str(width), "cy": str(height)})
    ET.SubElement(inline, _qn(WP, "effectExtent"), {"l": "0", "t": "0", "r": "0", "b": "0"})
    ET.SubElement(inline, _qn(WP, "docPr"), {"id": str(1000 + image_index), "name": name or f"report-image-{image_index}"})
    frame = ET.SubElement(inline, _qn(WP, "cNvGraphicFramePr"))
    ET.SubElement(frame, _qn(A, "graphicFrameLocks"), {"noChangeAspect": "1"})
    graphic = ET.SubElement(inline, _qn(A, "graphic"))
    graphic_data = ET.SubElement(graphic, _qn(A, "graphicData"), {"uri": "http://schemas.openxmlformats.org/drawingml/2006/picture"})
    picture = ET.SubElement(graphic_data, _qn(PIC, "pic"))
    non_visual = ET.SubElement(picture, _qn(PIC, "nvPicPr"))
    ET.SubElement(non_visual, _qn(PIC, "cNvPr"), {"id": "0", "name": name or f"report-image-{image_index}"})
    ET.SubElement(non_visual, _qn(PIC, "cNvPicPr"))
    fill = ET.SubElement(picture, _qn(PIC, "blipFill"))
    ET.SubElement(fill, _qn(A, "blip"), {_qn(R, "embed"): relationship_id})
    stretch = ET.SubElement(fill, _qn(A, "stretch"))
    ET.SubElement(stretch, _qn(A, "fillRect"))
    shape = ET.SubElement(picture, _qn(PIC, "spPr"))
    transform = ET.SubElement(shape, _qn(A, "xfrm"))
    ET.SubElement(transform, _qn(A, "off"), {"x": "0", "y": "0"})
    ET.SubElement(transform, _qn(A, "ext"), {"cx": str(width), "cy": str(height)})
    geometry = ET.SubElement(shape, _qn(A, "prstGeom"), {"prst": "rect"})
    ET.SubElement(geometry, _qn(A, "avLst"))


def _next_relationship_id(relationships) -> str:
    used = {item.get("Id") for item in relationships}
    index = 1
    while f"rId{index}" in used:
        index += 1
    return f"rId{index}"


def _ensure_image_content_type(content_types, extension: str) -> None:
    extension = extension.lower()
    for item in content_types.findall(_qn(CT, "Default")):
        if (item.get("Extension") or "").lower() == extension:
            return
    mime = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "bmp": "image/bmp"}[extension]
    content_types.append(ET.Element(_qn(CT, "Default"), {"Extension": extension, "ContentType": mime}))


def _renumber_table_captions(document) -> None:
    captions = []
    for paragraph in document.iter(_qn(W, "p")):
        text = _element_text(paragraph).strip()
        match = re.match(r"^表\s*(\d+)\s*(.*)$", text)
        if match:
            captions.append((paragraph, int(match.group(1)), match.group(2)))
    number_map = {old: new for new, (_paragraph, old, _tail) in enumerate(captions, start=1)}
    pattern = re.compile(r"表\s*(\d+)")
    for text_node in document.iter(_qn(W, "t")):
        original = text_node.text or ""
        text_node.text = pattern.sub(lambda match: f"表{number_map.get(int(match.group(1)), int(match.group(1)))}", original)
    for new, (paragraph, _old, tail) in enumerate(captions, start=1):
        _set_paragraph_text(paragraph, f"表{new} {tail}".rstrip())


def _fill_paragraph_block(document, start_heading: str, end_heading: str, values: list[str], predicate) -> None:
    body = document.find(_qn(W, "body"))
    children = list(body)
    start = next((index for index, child in enumerate(children) if _element_text(child).strip() == start_heading), None)
    end = next((index for index, child in enumerate(children) if _element_text(child).strip() == end_heading), None)
    if start is None or end is None or end <= start:
        return
    candidates = [
        child
        for child in children[start + 1:end]
        if child.tag == _qn(W, "p") and predicate(_element_text(child).strip())
    ]
    if not candidates:
        return
    template = copy.deepcopy(candidates[-1])
    for index, paragraph in enumerate(candidates):
        if index < len(values):
            _set_paragraph_text(paragraph, values[index])
        else:
            body.remove(paragraph)
    if len(values) > len(candidates):
        end_child = children[end]
        insert_at = list(body).index(end_child)
        for value in values[len(candidates):]:
            paragraph = copy.deepcopy(template)
            _set_paragraph_text(paragraph, value)
            body.insert(insert_at, paragraph)
            insert_at += 1


def _remove_table_and_caption(document, table, caption_text: str) -> None:
    parent_map = _parent_map(document)
    parent = parent_map.get(table)
    if parent is not None:
        parent.remove(table)
    for paragraph in list(document.iter(_qn(W, "p"))):
        if caption_text in _element_text(paragraph):
            paragraph_parent = parent_map.get(paragraph)
            if paragraph_parent is not None:
                paragraph_parent.remove(paragraph)
            break


def _remove_comments(document, relationships, content_types, entries: dict) -> None:
    _strip_comment_nodes(document)
    for relationship in list(relationships):
        rel_type = relationship.get("Type") or ""
        if "comments" in rel_type.lower() or rel_type.lower().endswith("/people"):
            relationships.remove(relationship)
    for item in list(content_types):
        part_name = (item.get("PartName") or "").lower()
        if "comment" in part_name or part_name.endswith("/people.xml"):
            content_types.remove(item)
    for name in list(entries):
        lower = name.lower()
        if lower.startswith("word/comments") or lower == "word/people.xml":
            entries.pop(name, None)


def _strip_comment_nodes(root) -> None:
    comment_tags = {
        _qn(W, "commentRangeStart"),
        _qn(W, "commentRangeEnd"),
        _qn(W, "commentReference"),
    }
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag in comment_tags:
                parent.remove(child)


def _mark_missing_content_red(document) -> None:
    for run in document.iter(_qn(W, "r")):
        text = _element_text(run).strip()
        if not text or (text != "-" and not any(marker in text for marker in MISSING_TEXT_MARKERS)):
            continue
        properties = run.find(_qn(W, "rPr"))
        if properties is None:
            properties = ET.Element(_qn(W, "rPr"))
            run.insert(0, properties)
        color = properties.find(_qn(W, "color"))
        if color is None:
            color = ET.SubElement(properties, _qn(W, "color"))
        color.attrib.clear()
        color.set(_qn(W, "val"), "FF0000")


def _replace_comment_text(document, comment_id: str, value) -> None:
    elements = list(document.iter())
    start_tag = _qn(W, "commentRangeStart")
    end_tag = _qn(W, "commentRangeEnd")
    id_attr = _qn(W, "id")
    start_index = next(
        (index for index, element in enumerate(elements) if element.tag == start_tag and element.get(id_attr) == comment_id),
        None,
    )
    end_index = next(
        (index for index, element in enumerate(elements) if element.tag == end_tag and element.get(id_attr) == comment_id),
        None,
    )
    if start_index is None or end_index is None or end_index <= start_index:
        return
    text_nodes = [element for element in elements[start_index + 1:end_index] if element.tag == _qn(W, "t")]
    if not text_nodes:
        paragraph = _comment_paragraph(document, comment_id)
        if paragraph is not None:
            _append_text_run(paragraph, _value(value, ""))
        return
    text_nodes[0].text = _value(value, "")
    for node in text_nodes[1:]:
        node.text = ""


def _replace_all_text(document, replacements: dict[str, str]) -> None:
    for text_node in document.iter(_qn(W, "t")):
        text = text_node.text or ""
        for old, new in replacements.items():
            text = text.replace(old, new)
        text_node.text = text
    for paragraph in document.iter(_qn(W, "p")):
        for old, new in replacements.items():
            _replace_across_text_nodes(paragraph, old, new)


def _replace_across_text_nodes(element, old: str, new: str) -> None:
    while True:
        text_nodes = list(element.iter(_qn(W, "t")))
        values = [node.text or "" for node in text_nodes]
        combined = "".join(values)
        start = combined.find(old)
        if start < 0:
            return
        end = start + len(old)
        cursor = 0
        start_index = end_index = 0
        start_offset = end_offset = 0
        for index, value in enumerate(values):
            next_cursor = cursor + len(value)
            if cursor <= start < next_cursor:
                start_index = index
                start_offset = start - cursor
            if cursor < end <= next_cursor:
                end_index = index
                end_offset = end - cursor
                break
            cursor = next_cursor
        if start_index == end_index:
            value = values[start_index]
            text_nodes[start_index].text = value[:start_offset] + new + value[end_offset:]
            continue
        text_nodes[start_index].text = values[start_index][:start_offset] + new
        for index in range(start_index + 1, end_index):
            text_nodes[index].text = ""
        text_nodes[end_index].text = values[end_index][end_offset:]


def _comment_paragraph(document, comment_id: str):
    parent_map = _parent_map(document)
    id_attr = _qn(W, "id")
    marker = next(
        (
            element
            for element in document.iter(_qn(W, "commentRangeStart"))
            if element.get(id_attr) == comment_id
        ),
        None,
    )
    if marker is None:
        return None
    current = marker
    while current in parent_map:
        current = parent_map[current]
        if current.tag == _qn(W, "p"):
            return current
    return None


def _set_cell_text(cell, value, default: str = "-") -> None:
    paragraphs = cell.findall(_qn(W, "p"))
    if not paragraphs:
        paragraphs = [ET.SubElement(cell, _qn(W, "p"))]
    _set_paragraph_text(paragraphs[0], _value(value, default))
    for paragraph in paragraphs[1:]:
        cell.remove(paragraph)


def _set_paragraph_text(paragraph, value: str) -> None:
    run_properties = None
    first_run = paragraph.find(_qn(W, "r"))
    if first_run is not None:
        properties = first_run.find(_qn(W, "rPr"))
        if properties is not None:
            run_properties = copy.deepcopy(properties)
    _clear_paragraph_content(paragraph)
    run = ET.SubElement(paragraph, _qn(W, "r"))
    if run_properties is not None:
        run.append(run_properties)
    text = ET.SubElement(run, _qn(W, "t"))
    text.text = _value(value, "")
    if text.text.startswith(" ") or text.text.endswith(" "):
        text.set(_qn(XML, "space"), "preserve")


def _append_text_run(paragraph, value: str) -> None:
    run = ET.SubElement(paragraph, _qn(W, "r"))
    text = ET.SubElement(run, _qn(W, "t"))
    text.text = value


def _clear_paragraph_content(paragraph) -> None:
    for child in list(paragraph):
        if child.tag != _qn(W, "pPr"):
            paragraph.remove(child)


def _element_text(element) -> str:
    return "".join(text.text or "" for text in element.iter(_qn(W, "t")))


def _parent_map(root) -> dict:
    return {child: parent for parent in root.iter() for child in parent}


def _activity_codes(data: dict) -> list[str]:
    raw = _payload_value(data["processing"], "activity_types", "activityTypes") or []
    if isinstance(raw, str):
        raw = [raw]
    codes = [str(value).upper() for value in raw if value not in (None, "")]
    if not codes:
        codes = [str(row.activity_type or "").upper() for row in data["legacy_activities"] if row.activity_type]
    return list(dict.fromkeys(codes))


def _activity_names(data: dict) -> list[str]:
    return [_activity_name(code) for code in _activity_codes(data)]


def _activity_name(code: str) -> str:
    normalized = str(code or "").upper()
    return ACTIVITY_NAMES.get(normalized, str(code or "-"))


def _activity_detail(processing: dict, code: str) -> dict:
    key = ACTIVITY_PAYLOAD_KEYS.get(code, str(code or "").lower())
    value = _payload_value(processing, key, _to_camel(key))
    return value if isinstance(value, dict) else {}


def _processing_summary(data: dict) -> str:
    names = _activity_names(data)
    if not names:
        return "尚未填写数据处理活动调研信息"
    details = []
    for code in _activity_codes(data):
        payload = _activity_detail(data["processing"], code)
        description = _payload_text(payload, "scenarios", "methods", "security_measures", "purpose")
        details.append(f"{_activity_name(code)}{f'（{description}）' if description else ''}")
    return f"主要涉及{'、'.join(details)}"


def _activity_narratives(data: dict) -> list[str]:
    rows = []
    for code in _activity_codes(data):
        payload = _activity_detail(data["processing"], code)
        description = _activity_detail_summary(payload)
        rows.append(f"{_activity_name(code)}阶段：{description or '未填写具体处理情况'}。")
    return rows or ["尚未填写数据处理活动具体情况。"]


def _activity_detail_summary(payload: dict) -> str:
    parts = []
    for keys, label in ACTIVITY_DETAIL_FIELDS:
        lookup_keys = []
        for key in keys:
            lookup_keys.extend([key, _to_camel(key)])
        value = _payload_value(payload, *lookup_keys)
        if value not in (None, "", [], {}):
            parts.append(f"{label}为{_list_text(value)}")
    return "；".join(parts)


def _security_summary(data: dict) -> str:
    security = data["security"]
    parts = []
    for key, value in security.items():
        if value in (None, "", [], {}):
            continue
        label = SECURITY_FIELD_NAMES.get(key, key)
        parts.append(f"{label}为{_list_text(value)}")
    return "；".join(parts) if parts else "尚未填写安全防护措施调研信息"


def _evaluation_summary(data: dict, sheet_name: str | None = None) -> str:
    records = []
    for item, record in data["evaluation_rows"]:
        if record and (not sheet_name or item.sheet_name == sheet_name):
            records.append(record.evaluation_result)
    counts = Counter(records)
    return (
        f"已完成现场测评{len(records)}项，其中符合{counts['COMPLIANT']}项、"
        f"基本符合{counts['PARTIAL']}项、不符合{counts['NON_COMPLIANT']}项、不适用{counts['NOT_APPLICABLE']}项"
    )


def _risk_count_phrase(data: dict) -> str:
    counts = Counter(row.risk_level for row in data["risks"])
    return (
        f"{counts['MAJOR']}项重大安全风险、{counts['HIGH']}项高安全风险、"
        f"{counts['MEDIUM']}项中安全风险、{counts['LOW']}项低安全风险和{counts['SLIGHT']}项轻微安全风险"
    )


def _indicator_domain_names(data: dict) -> list[str]:
    names = []
    for item in data["indicator_items"]:
        if item.sheet_name and item.sheet_name not in names:
            names.append(item.sheet_name)
    return names


def _domain_phrase(domains: list[str]) -> str:
    if not domains:
        return "尚未配置评估指标"
    count_name = {1: "一个", 2: "两个", 3: "三个", 4: "四个", 5: "五个"}.get(len(domains), f"{len(domains)}个")
    if len(domains) == 1:
        joined = domains[0]
    else:
        joined = "、".join(domains[:-1]) + "及" + domains[-1]
    return f"{joined}{count_name}方面"


def _score_value_phrase(data: dict) -> str:
    rows = _score_result_rows(data)
    return "、".join(row[1] for row in rows)


def _activity_list_text(values) -> str:
    if not values:
        return "-"
    if isinstance(values, str):
        values = [values]
    return "、".join(_activity_name(value) for value in values)


def _payload_text(payload: dict, *keys: str) -> str:
    for key in keys:
        value = _payload_value(payload, key, _to_camel(key))
        if value not in (None, "", [], {}):
            return _list_text(value)
    return ""


def _payload_value(payload: dict, *keys: str):
    if not isinstance(payload, dict):
        return None
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _named_values(values) -> list[str]:
    result = []
    for item in values or []:
        if isinstance(item, dict):
            result.append(item.get("name") or item.get("title") or item.get("value"))
        else:
            result.append(item)
    return [_value(item, "") for item in result if item not in (None, "")]


def _list_text(value) -> str:
    if value in (None, "", [], {}):
        return "-"
    if isinstance(value, dict):
        return "；".join(f"{key}：{_list_text(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return "、".join(_value(item) for item in value if item not in (None, ""))
    if isinstance(value, bool):
        return _yes_no(value)
    return str(value)


def _unique_join(values) -> str:
    return "、".join(dict.fromkeys(_value(value, "") for value in values if value not in (None, "")))


def _yes_no(value) -> str:
    if value is None:
        return "-"
    return "是" if bool(value) else "否"


def _offset_date(value, days: int) -> str:
    parsed = _parse_date(value)
    return _format_date(parsed + timedelta(days=days)) if parsed else ""


def _format_date(value) -> str:
    parsed = _parse_date(value)
    return parsed.strftime("%Y年%m月%d日") if parsed else ""


def _parse_date(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("/", "-"))
    except ValueError:
        return None


def _number(value) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)


def _value(value, default: str = "-") -> str:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return _yes_no(value)
    return str(value)


def _to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def _qn(namespace: str, tag: str) -> str:
    return f"{{{namespace}}}{tag}"
