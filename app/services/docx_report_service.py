from __future__ import annotations

import copy
import io
import json
import logging
import math
import mimetypes
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
import zlib
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

from app.common.exceptions import NotFoundError
from app.models import (
    AssessedOrganization,
    AssessmentTeamMember,
    AssessmentTemplateItem,
    BusinessSystem,
    ClientTeamMember,
    CoreDataAsset,
    DataAsset,
    DataProcessorBasicSurvey,
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
    RiskMatrix,
    ScoreModel,
    ScoreModelRange,
    SecurityProtectionSurvey,
)
from app.services import file_service, llm_gateway_service, risk_service, survey_service
from app.services.evaluation_service import calculate_project_score_snapshot


logger = logging.getLogger(__name__)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
V = "urn:schemas-microsoft-com:vml"
O = "urn:schemas-microsoft-com:office:office"
W10 = "urn:schemas-microsoft-com:office:word"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
WP14 = "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
W16DU = "http://schemas.microsoft.com/office/word/2023/wordml/word16du"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
XML = "http://www.w3.org/XML/1998/namespace"

NS = {"w": W, "r": R}
IMAGE_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
OLE_OBJECT_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject"
EMBEDDED_PACKAGE_CONTENT_TYPE = "application/vnd.ms-visio.drawing"
EMU_PER_INCH = 914400
IMAGE_CONTENT_TYPES = {
    "bmp": "image/bmp",
    "emf": "image/x-emf",
    "gif": "image/gif",
    "jpg": "image/jpeg",
    "png": "image/png",
    "wmf": "image/x-wmf",
}
VISIO_PREVIEW_WIDTH = 960
VISIO_PREVIEW_HEIGHT = 540
IGNORABLE_PREFIX_URIS = {
    "w14": W14,
    "w15": W15,
    "wp14": WP14,
    "w16du": W16DU,
    "wpc": "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "wpi": "http://schemas.microsoft.com/office/word/2010/wordprocessingInk",
    "wne": "http://schemas.microsoft.com/office/word/2006/wordml",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "wpsCustomData": "http://www.wps.cn/officeDocument/2013/wpsCustomData",
}

for prefix, uri in [
    ("w", W),
    ("r", R),
    ("wp", WP),
    ("a", A),
    ("pic", PIC),
    ("v", V),
    ("o", O),
    ("w10", W10),
    ("mc", MC),
    ("m", M),
    ("wp14", WP14),
    ("w14", W14),
    ("w15", W15),
    ("w16du", W16DU),
    ("wpc", "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"),
    ("wpg", "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"),
    ("wpi", "http://schemas.microsoft.com/office/word/2010/wordprocessingInk"),
    ("wne", "http://schemas.microsoft.com/office/word/2006/wordml"),
    ("wps", "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"),
    ("wpsCustomData", "http://www.wps.cn/officeDocument/2013/wpsCustomData"),
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
    (("collection_channels",), "采集渠道"),
    (("collection_method",), "收集方式"),
    (("collection_data_scope",), "收集数据范围"),
    (("collection_purpose",), "收集目的"),
    (("collection_frequency",), "收集频率"),
    (("collection_external_sources",), "外部数据来源"),
    (("collection_contracts",), "收集合约"),
    (("collection_related_systems",), "相关系统"),
    (("collection_public_device_usage",), "公共设备使用情况"),
    (("storage_method",), "存储方式"),
    (("data_center",), "数据中心"),
    (("storage_system",), "存储系统"),
    (("external_storage_provider",), "外部存储服务商"),
    (("storage_location",), "存储位置"),
    (("storage_duration",), "存储期限"),
    (("backup_redundancy_strategy",), "备份冗余策略"),
    (("online_channel",), "线上传输渠道"),
    (("offline_transfer",), "线下传输方式"),
    (("transfer_protocol",), "传输协议"),
    (("data_interface",), "数据接口"),
    (("use_purpose",), "使用目的"),
    (("use_method",), "使用方式"),
    (("use_scope",), "使用范围"),
    (("use_scenario",), "使用场景"),
    (("algorithm_rules",), "算法规则"),
    (("processing_details",), "加工处理情况"),
    (("algorithm_recommendation_service",), "算法推荐服务"),
    (("entrusted_or_joint_processing",), "委托或共同处理"),
    (("provide_purpose",), "提供目的"),
    (("provide_method",), "提供方式"),
    (("provide_scope",), "提供范围"),
    (("data_recipients",), "数据接收方"),
    (("provide_contracts",), "提供合约"),
    (("provided_personal_info_and_important_data",), "提供的个人信息和重要数据"),
    (("public_purpose",), "公开目的"),
    (("public_method",), "公开方式"),
    (("public_scope",), "公开范围"),
    (("public_audience_size",), "公开受众规模"),
    (("public_data_types",), "公开数据类型"),
    (("public_data_scale",), "公开数据规模"),
    (("deletion_scenarios",), "删除场景"),
    (("deletion_method",), "删除方式"),
    (("data_archive",), "数据归档"),
    (("media_destruction",), "介质销毁"),
    (("cross_border_presence",), "是否存在跨境"),
    (("cross_border_description",), "跨境说明"),
]
ACTIVITY_FLAT_DETAIL_KEYS = {
    "COLLECT": {
        "collection_channels",
        "collection_method",
        "collection_data_scope",
        "collection_purpose",
        "collection_frequency",
        "collection_external_sources",
        "collection_contracts",
        "collection_related_systems",
        "collection_public_device_usage",
    },
    "STORE": {
        "storage_method",
        "data_center",
        "storage_system",
        "external_storage_provider",
        "storage_location",
        "storage_duration",
        "backup_redundancy_strategy",
    },
    "TRANSFER": {
        "online_channel",
        "offline_transfer",
        "transfer_protocol",
        "data_interface",
        "cross_border_presence",
        "cross_border_description",
    },
    "USE": {
        "use_purpose",
        "use_method",
        "use_scope",
        "use_scenario",
        "algorithm_rules",
        "processing_details",
        "algorithm_recommendation_service",
        "entrusted_or_joint_processing",
    },
    "USE_PROCESS": {
        "use_purpose",
        "use_method",
        "use_scope",
        "use_scenario",
        "algorithm_rules",
        "processing_details",
        "algorithm_recommendation_service",
        "entrusted_or_joint_processing",
    },
    "USEPROCESS": {
        "use_purpose",
        "use_method",
        "use_scope",
        "use_scenario",
        "algorithm_rules",
        "processing_details",
        "algorithm_recommendation_service",
        "entrusted_or_joint_processing",
    },
    "PROVIDE": {
        "provide_purpose",
        "provide_method",
        "provide_scope",
        "data_recipients",
        "provide_contracts",
        "provided_personal_info_and_important_data",
    },
    "PUBLIC": {
        "public_purpose",
        "public_method",
        "public_scope",
        "public_audience_size",
        "public_data_types",
        "public_data_scale",
    },
    "DELETE": {
        "deletion_scenarios",
        "deletion_method",
        "data_archive",
        "media_destruction",
    },
}
ACTIVITY_RELATED_DATA_FIELDS = {
    "COLLECT": ("collection_data_scope",),
    "PROVIDE": ("provided_personal_info_and_important_data", "provide_scope"),
    "PUBLIC": ("public_data_types",),
}
ACTIVITY_DATA_DETAIL_LIMIT = 5
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
    "compliance_assessment_status": "合规测评情况",
    "data_security_management": "数据安全管理",
    "network_security_devices_and_policies": "网络安全设备及策略",
    "identity_authentication_and_access_control": "身份认证与访问控制",
    "vulnerability_management": "漏洞管理",
    "remote_management_software": "远程管理软件",
    "account_password_management": "账号口令管理",
    "security_technology_application": "安全技术应用",
    "is_power_monitoring_system": "是否为电力监控系统",
    "production_control_area_protection": "生产控制大区防护",
    "security_access_area_setup": "安全接入区设置",
    "power_monitoring_dedicated_network": "电力监控专用网络",
    "zone_isolation_device_usage": "区域隔离装置使用",
    "wide_area_network_connection_security": "广域网连接安全",
    "power_dispatch_authentication": "电力调度认证",
    "network_service_security_control": "网络服务安全控制",
    "security_access_area_security_control": "安全接入区安全控制",
    "zone_boundary_protection": "区域边界防护",
    "product_security_reliability": "产品安全可靠性",
    "operator_security_monitoring_warning": "运营者安全监测预警",
    "security_incidents_and_threats": "安全事件和威胁",
    "detected_threats": "已发现威胁",
    "public_threat_alerts": "公开威胁预警",
    "other_security_threats": "其他安全威胁",
}


def generate_document(session, project, template_path: str, selected_sections: list[str] | None = None) -> bytes:
    """使用正式 Word 模板和项目最终数据生成 docx 字节。

    直接编辑 docx 包内 OpenXML，以保留模板样式、页眉页脚和固定内容；生成结束后移除全部批注。
    """
    template = Path(template_path)
    if not template.exists():
        raise FileNotFoundError(f"Report template not found: {template}")

    # 完整读取模板压缩包，后续只替换必要的 XML 部件并原样写回其他文件。
    with zipfile.ZipFile(template, "r") as source:
        entries = {info.filename: source.read(info.filename) for info in source.infolist()}
        infos = {info.filename: info for info in source.infolist()}

    document = ET.fromstring(entries["word/document.xml"])
    relationships = ET.fromstring(entries["word/_rels/document.xml.rels"])
    content_types = ET.fromstring(entries["[Content_Types].xml"])
    data = _load_report_data(session, project)

    # 新版模板不再展示三个按系统重复的四级标题。兼容仍保留旧标题的部署模板，
    # 在填充批注前按完整标题文本移除，避免遗留“XX系统”占位内容。
    _remove_obsolete_risk_system_headings(document)

    # 填充模板正文中由批注标注的段落：封面、评估背景/目的/依据、5.2 评估对象和范围、
    # 7.2 数据安全基本信息、8 风险分析与评价、9 风险处理等章节中的系统名称、单位名称、
    # 日期、处理活动、综合描述和风险统计等正文内容。
    _fill_paragraphs(document, data)
    tables = list(document.iter(_qn(W, "tbl")))
    _remove_processing_activity_table(document, tables)
    tables = list(document.iter(_qn(W, "tbl")))

    # 填充模板基本信息表：被评估单位、统一社会信用代码、地址、联系人、评估负责人、
    # 计划时间等项目基本信息和被评估单位信息。
    _fill_basic_information_table(tables[0], data)

    # 填充 5.1 评估指标表组：数据安全管理、数据安全技术、数据处理活动、
    # 个人信息保护四类指标的控制点和评估项数量。
    _fill_indicator_tables(document, tables[1:5], data)

    # 填充 5.2 评估对象和范围下方系统范围表：只替换模板数据行中的系统名称和系统描述，
    # 序号与涉及数据类型保持模板原始内容。
    _fill_business_system_table(tables[5], data)

    # 填充 6.1/评估工作开展情况中的评估团队表。
    _fill_table(tables[6], _assessment_team_rows(data))

    # 填充 6.1/评估工作开展情况中的被评估方配合人员表。
    _fill_table(tables[7], _client_team_rows(data))

    # 填充 7.2.2 数据资产情况表：数据名称、形态、范围、规模、来源、存储分布、
    # 流转情况、分类分级、类别、级别和是否为个人信息。
    _fill_table(tables[8], _data_asset_rows(data))

    # 填充 7.2.2 个人信息情况表；没有个人信息数据时删除该表及对应表题。
    _fill_optional_table(document, tables[9], _personal_info_rows(data), "表2 个人信息情况")

    # 填充 7.2.2 重要数据情况表；没有重要数据时删除该表及对应表题。
    _fill_optional_table(document, tables[10], _important_data_rows(data), "表3 重要数据情况")

    # 填充 7.2.2 核心数据情况表；没有核心数据时删除该表及对应表题。
    _fill_optional_table(document, tables[11], _core_data_rows(data), "表4 核心数据情况")

    # 填充 8.1 数据安全风险识别中的文档核查/参考资料表。
    _fill_table(tables[12], _reference_rows(data))

    # 填充 8.2 数据安全风险识别中的现场测评问题清单表，仅写入基本符合和不符合项。
    _fill_table(tables[13], _evaluation_issue_rows(data))

    # 填充 8.3.1 风险源识别表：风险类型、风险描述、风险源描述、
    # 涉及数据类型和涉及数据处理活动。
    _fill_table(tables[14], _risk_source_rows(data))

    # 填充 8.3.2 风险危害程度分析结果表。
    _fill_table(_find_table_by_headers(tables, "风险危害程度", "危害程度分析"), _harm_rows(data))

    # 填充 8.3.3 风险发生可能性等级表：评分模型中的等级与综合得分区间。
    _fill_table(_find_table_by_headers(tables, "等级", "综合得分"), _score_range_rows(data))

    # 填充 8.3.3 评估项定性判定得分表：符合、基本符合、不符合对应分值。
    _fill_table(_find_table_by_headers(tables, "评估项k定性判定", "评估项k的得分"), _score_result_rows(data))

    # 填充 8.3.3.1 综合得分及风险发生可能等级表：评估对象、综合得分和可能性等级。
    _fill_table(tables[21], _score_summary_rows(data))

    # 填充 8.3.4 风险评价矩阵表：按可能性和危害程度映射风险等级。
    _fill_risk_matrix_table(tables[22], data)

    # 填充 8.3.5 数据安全风险清单表：风险类型、描述、危害程度、可能性、
    # 风险源、风险等级和涉及处理活动。
    _fill_table(_find_table_by_headers(tables, "风险发生的", "风险等级", "涉及的数据处理活动"), _risk_rows(data))

    # 填充 9 安全风险处理/整改建议表：风险信息和最终整改建议。
    _fill_table(_find_table_by_headers(tables, "风险源描述", "风险等级", "整改建议"), _suggestion_rows(data))

    # 图片单独写入 docx media，并在正文批注位置建立关系和绘图节点。
    media_entries = {}
    if not selected_sections or "SURVEY" in selected_sections:
        _insert_business_system_attachment(
            document,
            relationships,
            content_types,
            data,
            comment_id="42",
            file_field="topology_file_id",
            expected_biz_type="SURVEY_TOPOLOGY_DIAGRAM",
            fallback_text="未上传网络拓扑图",
            media_entries=media_entries,
        )
        _insert_business_system_attachment(
            document,
            relationships,
            content_types,
            data,
            comment_id="52",
            file_field="business_flow_file_id",
            expected_biz_type="SURVEY_DATA_FLOW_DIAGRAM",
            fallback_text="未上传数据流转图",
            media_entries=media_entries,
        )

    # 最后统一处理可选章节、编号、缺失值颜色和批注清理，避免中途改变模板定位结构。
    _apply_section_selection(document, selected_sections)
    _renumber_table_captions(document)
    _mark_missing_content_red(document)
    _remove_comments(document, relationships, content_types, entries)
    _set_update_fields(entries)
    _clean_markup_compatibility(document)

    entries["word/document.xml"] = ET.tostring(document, encoding="utf-8", xml_declaration=True)
    entries["word/_rels/document.xml.rels"] = ET.tostring(relationships, encoding="utf-8", xml_declaration=True)
    entries["[Content_Types].xml"] = ET.tostring(content_types, encoding="utf-8", xml_declaration=True)
    entries.update(media_entries)

    # 将修改后的 OpenXML 和模板未修改部件重新打包为最终 docx 文件。
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
    _clean_markup_compatibility(settings)
    entries[settings_name] = ET.tostring(settings, encoding="utf-8", xml_declaration=True)


def _clean_markup_compatibility(root) -> None:
    ignorable_attribute = _qn(MC, "Ignorable")
    ignorable = root.get(ignorable_attribute)
    if not ignorable:
        return
    used_namespaces = _element_namespaces(root)
    prefixes = [
        prefix
        for prefix in ignorable.split()
        if IGNORABLE_PREFIX_URIS.get(prefix) in used_namespaces
    ]
    if prefixes:
        root.set(ignorable_attribute, " ".join(prefixes))
    else:
        root.attrib.pop(ignorable_attribute, None)


def _element_namespaces(root) -> set[str]:
    namespaces = set()
    for element in root.iter():
        for name in [element.tag, *element.attrib]:
            if isinstance(name, str) and name.startswith("{"):
                namespaces.add(name[1:].split("}", 1)[0])
    return namespaces


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
    data_processor = session.query(DataProcessorBasicSurvey).filter_by(project_id=project_id, deleted=False).first()
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
    risk_sources = risk_service.current_records(session, project_id)
    # 与汇总分析页面保持同一展示口径：开启合并时，风险危害判定、风险清单和
    # 整改建议均使用页面返回的合并行；风险源识别表仍保留风险源页的明细口径。
    risks = risk_service.current_records_for_display(session, project)
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
        "data_processor": data_processor,
        "assets": assets,
        "personal_info": personal_info,
        "important_data": important_data,
        "core_data": core_data,
        "legacy_activities": legacy_activities,
        "references": references,
        "assessment_items": assessment_items,
        "indicator_items": indicator_items,
        "evaluation_rows": evaluation_rows,
        "risk_sources": risk_sources,
        "risks": risks,
        "processing": survey_service.processing_activity_payload(processing),
        "security": survey_service.security_protection_payload(security),
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


def _report_target(data: dict) -> str:
    system = _primary_business_system(data)
    return _value(getattr(system, "system_name", None), "未填写信息系统名称")


def _primary_business_system(data: dict) -> BusinessSystem | None:
    systems = data["systems"]
    if not systems:
        return None
    attached = [system for system in systems if system.topology_file_id or system.business_flow_file_id]
    candidates = attached or systems
    return max(candidates, key=lambda row: (row.updated_at or row.created_at or datetime.min, row.id))


def _business_system_description(data: dict) -> str:
    system = _primary_business_system(data)
    return _value(getattr(system, "business_function", None), "未填写业务功能描述")


DATA_PROCESSOR_SUMMARY_LABELS = [
    ("unit_name", "单位名称"),
    ("unified_social_credit_code", "统一社会信用代码"),
    ("office_address", "办公地址"),
    ("legal_representative", "法定代表人"),
    ("staff_size", "人员规模"),
    ("business_scope", "经营范围"),
    ("data_security_officer", "数据安全负责人"),
    ("contact_info", "联系方式"),
    ("unit_nature", "单位性质"),
    ("specific_processor_type", "特定数据处理者类型"),
    ("power_industry_category", "电力行业类别"),
    ("business_operation_area", "业务运营区域"),
    ("data_processing_location", "数据处理地点"),
    ("main_business_scope", "主要业务范围"),
    ("business_scale", "业务规模"),
    ("administrative_license", "行政许可"),
]


def _data_processor_name(data: dict) -> str:
    return _value(getattr(data["data_processor"], "unit_name", None), "未填写单位名称")


def _data_processor_office_address(data: dict) -> str:
    return _value(getattr(data["data_processor"], "office_address", None), "未填写办公地址")


def _data_processor_summary(data: dict) -> str:
    processor = data["data_processor"]
    if not processor:
        return "未填写数据处理者基本情况"
    parts = []
    for field, label in DATA_PROCESSOR_SUMMARY_LABELS:
        value = getattr(processor, field, None)
        if value not in (None, "", [], {}):
            parts.append(f"{label}为{_list_text(value)}")
    return "；".join(parts) + "。" if parts else "未填写数据处理者基本情况"


def _fill_paragraphs(document, data: dict) -> None:
    project = data["project"]
    basic = data["basic"]
    target = _report_target(data)
    processor_name = _data_processor_name(data)
    office_address = _data_processor_office_address(data)
    activities = _activity_names(data)
    domains = _indicator_domain_names(data)
    generated_date = _format_date(datetime.now())
    end_date = _format_date(getattr(basic, "plan_end_date", None)) or generated_date
    report_date = end_date
    security_summary = _security_summary(data)
    activity_text = "、".join(activities) or "尚未填写的数据处理活动"
    activity_stage_text = f"{activity_text}阶段" if activities else "尚未填写的数据处理活动阶段"
    replacements = {
        "0": project.project_code,
        "1": processor_name,
        "2": target,
        "3": processor_name,
        "4": report_date,
        "5": target,
        "6": processor_name,
        "7": office_address,
        "13": target,
        "14": end_date,
        "15": processor_name,
        "16": target,
        "19": activity_text,
        "20": str(len(activities)),
        "21": target,
        "22": _domain_phrase(domains),
        "27": target,
        "28": target,
        "29": activity_text,
        "30": target,
        "31": _business_system_description(data),
        "32": processor_name,
        "35": _format_date(getattr(basic, "plan_start_date", None)) or "未填写",
        "36": end_date,
        "37": target,
        "38": processor_name,
        "39": target,
        "40": _data_processor_summary(data),
        "41": _business_system_description(data),
        "43": f" {target}",
        "44": processor_name,
        "45": target,
        "50": target,
        "51": activity_stage_text,
        "53": target,
        "54": processor_name,
        "55": security_summary,
        "57": _domain_phrase(domains),
        "58": target,
        "59": target,
        "60": target,
        "61": target,
        "63": target,
        "64": target,
        "67": target,
        "75": target,
        "76": target,
        "77": _number(data["score"]["score"]),
        "78": POSSIBILITY_NAMES.get(data["score"]["possibilityLevel"], data["score"]["possibilityLevel"]),
        "82": target,
        "84": target,
        "85": target,
        "86": _evaluation_summary(data, "数据安全管理"),
        "87": security_summary,
        "88": _processing_summary(data),
        "89": target,
        "90": _risk_count_phrase(data),
        "91": target,
        "92": target,
    }
    for comment_id, value in replacements.items():
        _replace_comment_text(document, comment_id, value)
    _replace_indicator_scope_paragraph(document, target, domains)

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
            "XXXXXX公司": processor_name,
            "XXXX公司": processor_name,
            "XX公司": processor_name,
            "XXXX系统": target,
            "XX系统": target,
            "20XX年XX月XX日": report_date,
        },
    )


def _fill_basic_information_table(table, data: dict) -> None:
    basic = data["basic"]
    organization = data["organization"]
    processor = data["data_processor"]
    contact = data["contacts"][0] if data["contacts"] else None
    target = _report_target(data)
    values = {
        (1, 1): target,
        (2, 1): _data_processor_name(data),
        (2, 3): _value(getattr(processor, "unified_social_credit_code", None)),
        (3, 1): _data_processor_office_address(data),
        (3, 3): _value(getattr(organization, "postal_code", None)),
        (4, 2): _value(getattr(contact, "name", None)),
        (4, 4): _value(getattr(contact, "title", None)),
        (5, 2): _value(getattr(contact, "department", None)),
        (5, 4): _value(getattr(contact, "phone", None)),
        (6, 2): _value(getattr(contact, "mobile", None)),
        (6, 4): _value(getattr(contact, "email", None)),
        (8, 1): _value(getattr(processor, "data_security_officer", None)),
        (15, 4): _offset_date(getattr(basic, "plan_end_date", None), -4) or "未填写",
        (16, 4): _offset_date(getattr(basic, "plan_end_date", None), -2) or "未填写",
        (17, 4): _format_date(getattr(basic, "plan_end_date", None)) or "未填写",
    }
    rows = table.findall(_qn(W, "tr"))
    for (row_index, cell_index), value in values.items():
        cells = rows[row_index].findall(_qn(W, "tc"))
        _set_cell_text(cells[cell_index], value)
    leader_cells = rows[15].findall(_qn(W, "tc"))
    _set_cell_text(leader_cells[2], "", default="")


def _fill_business_system_table(table, data: dict) -> None:
    rows = table.findall(_qn(W, "tr"))
    if len(rows) < 2:
        return
    for row in rows[2:]:
        table.remove(row)

    cells = rows[1].findall(_qn(W, "tc"))
    if len(cells) != 4:
        raise ValueError(f"Report business system table requires 4 columns, received {len(cells)}.")
    target = _report_target(data)
    description = _business_system_description(data)
    _set_cell_text(cells[1], target)
    _set_cell_text(cells[2], description)


def _replace_indicator_scope_paragraph(document, target: str, domains: list[str]) -> None:
    value = f"{target}围绕{_domain_phrase(domains)}开展评估实施工作，详细评估细则如下："
    for paragraph in document.iter(_qn(W, "p")):
        text = _element_text(paragraph)
        if "围绕" in text and "开展评估实施工作" in text:
            _set_paragraph_text(paragraph, value)
            return


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


def _remove_processing_activity_table(document, tables: list) -> None:
    for table in tables:
        rows = table.findall(_qn(W, "tr"))
        if not rows:
            continue
        header_text = _element_text(rows[0])
        if "数据处理活动" in header_text and "处理频率" in header_text:
            _remove_table_and_caption(document, table, "处理活动")
            return


def _fill_table(table, rows: list[list], keep_empty_row: bool = True) -> None:
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
            _set_cell_text(cell, value)
        table.append(row)


def _find_table_by_headers(tables: list, *required_headers: str):
    """按表头文本定位动态表，避免模板内表格位置调整后填错表。"""
    for table in tables:
        rows = table.findall(_qn(W, "tr"))
        if not rows:
            continue
        header_text = _element_text(rows[0]).replace("\n", "")
        if all(header in header_text for header in required_headers):
            return table
    raise ValueError(f"Report template table not found: {', '.join(required_headers)}")


def _remove_obsolete_risk_system_headings(document) -> None:
    obsolete = {
        "XX系统风险源识别",
        "XX系统风险危害程度判定",
        "XX系统安全风险清单",
    }
    parent_map = {child: parent for parent in document.iter() for child in parent}
    for paragraph in list(document.iter(_qn(W, "p"))):
        if _element_text(paragraph).strip() not in obsolete:
            continue
        parent = parent_map.get(paragraph)
        if parent is not None:
            parent.remove(paragraph)


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
        for index, row in enumerate(data["risk_sources"], start=1)
    ]


def _harm_rows(data: dict) -> list[list]:
    return [
        [
            index,
            _list_text(row.risk_types),
            row.risk_description,
            HARM_NAMES.get(row.harm_level, row.harm_level),
            row.harm_description,
        ]
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
    target = _report_target(data)
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


def _insert_business_system_attachment(
    document,
    relationships,
    content_types,
    data: dict,
    comment_id: str,
    file_field: str,
    expected_biz_type: str,
    fallback_text: str,
    media_entries: dict,
) -> None:
    paragraph = _comment_paragraph(document, comment_id)
    if paragraph is None:
        return
    file_row = _business_system_attachment_file(data, file_field, expected_biz_type)
    if not file_row:
        _set_paragraph_text(paragraph, fallback_text)
        return

    content = file_service.read_bytes(file_row)
    if _is_visio_file(file_row):
        _insert_visio_attachment(document, paragraph, relationships, content_types, file_row, content, media_entries)
        return

    extension = _image_extension(file_row)
    if not extension:
        _set_paragraph_text(paragraph, f"{fallback_text}（上传文件格式不支持插入 Word）")
        return
    relationship_id = _next_relationship_id(relationships)
    image_index = _next_entry_index(media_entries, "word/media/report-image-")
    media_name = f"word/media/report-image-{image_index}.{extension}"
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
    _set_paragraph_drawing(paragraph, relationship_id, width, height, Path(file_row.file_name).name, image_index)


def _business_system_attachment_file(data: dict, file_field: str, expected_biz_type: str) -> FileObject | None:
    candidates = []
    for system in data["systems"]:
        file_id = getattr(system, file_field, None)
        if not file_id:
            continue
        try:
            file_row = file_service.get_file(file_id)
        except NotFoundError:
            continue
        if file_row.project_id not in (None, data["project"].id):
            continue
        if expected_biz_type and file_row.biz_type != expected_biz_type:
            continue
        candidates.append((_file_order_value(file_row, system), file_row))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _file_order_value(file_row: FileObject, system: BusinessSystem) -> tuple:
    timestamp = (
        getattr(file_row, "updated_at", None)
        or getattr(file_row, "created_at", None)
        or getattr(system, "updated_at", None)
        or getattr(system, "created_at", None)
        or datetime.min
    )
    return timestamp, file_row.id


def _insert_visio_attachment(
    document,
    paragraph,
    relationships,
    content_types,
    file_row: FileObject,
    content: bytes,
    extra_entries: dict,
) -> None:
    relationship_id = _next_relationship_id(relationships)
    visio_index = _next_entry_index(extra_entries, "word/embeddings/report-visio-")
    package_name = f"word/embeddings/report-visio-{visio_index}.vsdx"
    relationships.append(
        ET.Element(
            _qn(REL, "Relationship"),
            {
                "Id": relationship_id,
                "Type": OLE_OBJECT_REL_TYPE,
                "Target": package_name.removeprefix("word/"),
            },
        )
    )
    _ensure_part_content_type(content_types, f"/{package_name}", EMBEDDED_PACKAGE_CONTENT_TYPE)
    extra_entries[package_name] = content
    _set_paragraph_visio_object(
        document,
        paragraph,
        relationship_id,
        file_row,
    )


def _is_visio_file(file_row: FileObject) -> bool:
    return Path(file_row.file_name or "").suffix.lower() == ".vsdx"


def _image_extension(file_row: FileObject) -> str | None:
    suffix = _normalize_image_extension(Path(file_row.file_name or "").suffix.lower().lstrip("."))
    if suffix in IMAGE_CONTENT_TYPES:
        return suffix
    guessed = _normalize_image_extension((mimetypes.guess_extension(file_row.content_type or "") or "").lower().lstrip("."))
    return guessed if guessed in IMAGE_CONTENT_TYPES else None


def _normalize_image_extension(extension: str) -> str:
    return "jpg" if extension in {"jpe", "jpeg"} else extension


def _visio_preview_image(content: bytes) -> tuple[bytes, str] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as package:
            names = package.namelist()
            preview_name = _visio_preview_name(names)
            if preview_name:
                extension = _normalize_image_extension(Path(preview_name).suffix.lower().lstrip("."))
                return package.read(preview_name), extension
    except zipfile.BadZipFile:
        return _basic_visio_preview_image([])
    rendered = _render_visio_preview_image(content)
    if rendered:
        return rendered
    return _basic_visio_preview_image(_visio_preview_shapes(content))


def _render_visio_preview_image(content: bytes) -> tuple[bytes, str] | None:
    converter = _office_converter_command()
    if not converter:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="visio-preview-") as tmp:
            workdir = Path(tmp)
            source = workdir / "input.vsdx"
            source.write_bytes(content)
            subprocess.run(
                [converter, "--headless", "--convert-to", "png", "--outdir", str(workdir), str(source)],
                cwd=workdir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
            outputs = sorted(path for path in workdir.glob("*.png") if path.name != source.name)
            if not outputs:
                return None
            return outputs[0].read_bytes(), "png"
    except (OSError, subprocess.SubprocessError):
        return None


def _office_converter_command() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def _visio_preview_name(names: list[str]) -> str | None:
    candidates = []
    for name in names:
        normalized = name.replace("\\", "/").lower()
        extension = _normalize_image_extension(Path(normalized).suffix.lstrip("."))
        if extension not in IMAGE_CONTENT_TYPES:
            continue
        if normalized.startswith("docprops/thumbnail."):
            return name
        if normalized.startswith("visio/media/"):
            candidates.append(name)
    return candidates[0] if candidates else None


def _visio_preview_shapes(content: bytes) -> list[tuple]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as package:
            page_name = _first_visio_page_name(package.namelist())
            if not page_name:
                return []
            root = ET.fromstring(package.read(page_name))
    except (ET.ParseError, KeyError, zipfile.BadZipFile):
        return []
    namespace = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""
    cell_tag = _qn(namespace, "Cell") if namespace else "Cell"
    shape_tag = _qn(namespace, "Shape") if namespace else "Shape"
    shapes_container_tag = _qn(namespace, "Shapes") if namespace else "Shapes"
    container = root.find(shapes_container_tag)
    if container is None:
        return []
    shapes = _visio_preview_shapes_from_container(container, namespace, _identity_matrix())
    return shapes[:240]


def _visio_preview_shapes_from_container(container, namespace: str, parent_matrix: tuple[float, ...]) -> list[dict]:
    cell_tag = _qn(namespace, "Cell") if namespace else "Cell"
    shape_tag = _qn(namespace, "Shape") if namespace else "Shape"
    shapes_container_tag = _qn(namespace, "Shapes") if namespace else "Shapes"
    shapes = []
    for shape in container.findall(shape_tag):
        cells = {cell.get("N"): _float(cell.get("V")) for cell in shape.findall(cell_tag)}
        shape_matrix = _compose_matrix(parent_matrix, _visio_shape_matrix(cells))
        child_container = shape.find(shapes_container_tag)
        if child_container is not None:
            shapes.extend(_visio_preview_shapes_from_container(child_container, namespace, shape_matrix))
        shape_text = _visio_shape_text(shape, namespace)
        if _has_cells(cells, "BeginX", "BeginY", "EndX", "EndY"):
            route = _visio_geometry_lines(shape, namespace, cells, shape_text, shape_matrix)
            if route:
                shapes.extend(route)
            else:
                x1, y1 = _transform_point(parent_matrix, cells["BeginX"], cells["BeginY"])
                x2, y2 = _transform_point(parent_matrix, cells["EndX"], cells["EndY"])
                shapes.append({"type": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "text": shape_text})
            continue
        if child_container is not None or not _has_cells(cells, "Width", "Height"):
            continue
        width = abs(cells["Width"])
        height = abs(cells["Height"])
        if width <= 0.01 or height <= 0.01:
            continue
        corners = [
            _transform_point(shape_matrix, 0, 0),
            _transform_point(shape_matrix, width, 0),
            _transform_point(shape_matrix, width, height),
            _transform_point(shape_matrix, 0, height),
        ]
        xs = [point[0] for point in corners]
        ys = [point[1] for point in corners]
        shapes.append({"type": "rect", "x1": min(xs), "y1": min(ys), "x2": max(xs), "y2": max(ys), "text": shape_text})
    return shapes


def _visio_geometry_lines(shape, namespace: str, cells: dict, shape_text: str, shape_matrix: tuple[float, ...]) -> list[dict]:
    section_tag = _qn(namespace, "Section") if namespace else "Section"
    row_tag = _qn(namespace, "Row") if namespace else "Row"
    cell_tag = _qn(namespace, "Cell") if namespace else "Cell"
    lines = []
    current = None
    for section in shape.findall(section_tag):
        if section.get("N") != "Geometry":
            continue
        for row in section.findall(row_tag):
            row_type = row.get("T")
            if row_type not in {"MoveTo", "LineTo"}:
                continue
            row_cells = {cell.get("N"): _float(cell.get("V")) for cell in row.findall(cell_tag)}
            point = _transform_point(shape_matrix, row_cells.get("X") or 0, row_cells.get("Y") or 0)
            if row_type == "MoveTo":
                current = point
                continue
            if current is None:
                current = point
                continue
            if current != point:
                lines.append({"type": "line", "x1": current[0], "y1": current[1], "x2": point[0], "y2": point[1], "text": shape_text})
            current = point
    return lines


def _identity_matrix() -> tuple[float, float, float, float, float, float]:
    return 1, 0, 0, 1, 0, 0


def _visio_shape_matrix(cells: dict) -> tuple[float, float, float, float, float, float]:
    pin_x = cells.get("PinX") or 0
    pin_y = cells.get("PinY") or 0
    loc_x = cells.get("LocPinX") or 0
    loc_y = cells.get("LocPinY") or 0
    angle = cells.get("Angle") or 0
    cos_value = math.cos(angle)
    sin_value = math.sin(angle)
    return (
        cos_value,
        -sin_value,
        sin_value,
        cos_value,
        pin_x - cos_value * loc_x + sin_value * loc_y,
        pin_y - sin_value * loc_x - cos_value * loc_y,
    )


def _compose_matrix(parent: tuple[float, ...], child: tuple[float, ...]) -> tuple[float, float, float, float, float, float]:
    pa, pb, pc, pd, pe, pf = parent
    ca, cb, cc, cd, ce, cf = child
    return (
        pa * ca + pb * cc,
        pa * cb + pb * cd,
        pc * ca + pd * cc,
        pc * cb + pd * cd,
        pa * ce + pb * cf + pe,
        pc * ce + pd * cf + pf,
    )


def _transform_point(matrix: tuple[float, ...], x_value: float, y_value: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return a * x_value + b * y_value + e, c * x_value + d * y_value + f


def _visio_shape_text(shape, namespace: str) -> str:
    text_tag = _qn(namespace, "Text") if namespace else "Text"
    text_node = shape.find(text_tag)
    if text_node is None:
        return ""
    raw = "".join(text_node.itertext()).replace("\r", "\n")
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in raw.splitlines()]
    return "\n".join(line for line in lines if line)


def _first_visio_page_name(names: list[str]) -> str | None:
    pages = [
        name
        for name in names
        if name.startswith("visio/pages/page")
        and name.endswith(".xml")
        and Path(name).name != "pages.xml"
    ]
    return sorted(pages)[0] if pages else None


def _has_cells(cells: dict, *keys: str) -> bool:
    return all(cells.get(key) is not None for key in keys)


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _basic_visio_preview_image(shapes: list[tuple]) -> tuple[bytes, str]:
    rendered = _system_drawing_visio_preview_image(shapes)
    if rendered:
        return rendered, "png"

    width, height = VISIO_PREVIEW_WIDTH, VISIO_PREVIEW_HEIGHT
    canvas = bytearray([255, 255, 255]) * (width * height)
    _draw_rect(canvas, width, height, 0, 0, width - 1, height - 1, (221, 226, 232), fill=(255, 255, 255))
    _draw_rect(canvas, width, height, 18, 18, width - 19, height - 19, (209, 218, 229), fill=(248, 250, 252))
    if shapes:
        _draw_visio_shapes(canvas, width, height, shapes)
    else:
        _draw_generic_visio_preview(canvas, width, height)
    return _encode_png(width, height, canvas), "png"


def _draw_visio_shapes(canvas: bytearray, width: int, height: int, shapes: list[tuple]) -> None:
    pixel_shapes = _visio_pixel_shapes(shapes, width, height)
    if not pixel_shapes:
        _draw_generic_visio_preview(canvas, width, height)
        return
    for item in pixel_shapes:
        if item["type"] != "rect":
            continue
        _draw_rect(
            canvas,
            width,
            height,
            item["x1"],
            item["y1"],
            item["x2"],
            item["y2"],
            (55, 101, 163),
            fill=(226, 239, 255),
        )
    for item in pixel_shapes:
        if item["type"] != "line":
            continue
        _draw_line(canvas, width, height, item["x1"], item["y1"], item["x2"], item["y2"], (75, 85, 99), thickness=2)


def _visio_pixel_shapes(shapes: list[dict], width: int, height: int) -> list[dict]:
    bounds = _shape_bounds(shapes)
    if not bounds:
        return []
    min_x, min_y, max_x, max_y = bounds
    margin = 60
    scale = min((width - margin * 2) / max(max_x - min_x, 0.1), (height - margin * 2) / max(max_y - min_y, 0.1))

    def point(x_value: float, y_value: float) -> tuple[int, int]:
        x = margin + int((x_value - min_x) * scale)
        y = height - margin - int((y_value - min_y) * scale)
        return x, y

    pixels = []
    for item in shapes:
        x1, y1 = point(item["x1"], item["y1"])
        x2, y2 = point(item["x2"], item["y2"])
        pixels.append(
            {
                "type": item["type"],
                "x1": min(x1, x2),
                "y1": min(y1, y2),
                "x2": max(x1, x2),
                "y2": max(y1, y2),
                "text": item.get("text") or "",
            }
        )
    return pixels


def _shape_bounds(shapes: list[tuple]) -> tuple[float, float, float, float] | None:
    points = []
    for item in shapes:
        points.extend([(item["x1"], item["y1"]), (item["x2"], item["y2"])])
    points = [(x, y) for x, y in points if x is not None and y is not None]
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _system_drawing_visio_preview_image(shapes: list[dict]) -> bytes | None:
    if not shapes or not any(shape.get("text") for shape in shapes):
        return None
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return None
    pixel_shapes = _visio_pixel_shapes(shapes, VISIO_PREVIEW_WIDTH, VISIO_PREVIEW_HEIGHT)
    if not pixel_shapes:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="visio-preview-render-") as tmp:
            workdir = Path(tmp)
            json_path = workdir / "shapes.json"
            output_path = workdir / "preview.png"
            script_path = workdir / "render.ps1"
            json_path.write_text(json.dumps(pixel_shapes, ensure_ascii=False), encoding="utf-8")
            script_path.write_text(_SYSTEM_DRAWING_VISIO_PREVIEW_SCRIPT, encoding="utf-8")
            command = [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                str(json_path),
                str(output_path),
                str(VISIO_PREVIEW_WIDTH),
                str(VISIO_PREVIEW_HEIGHT),
            ]
            result = subprocess.run(
                command,
                cwd=workdir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            )
            if result.returncode != 0 or not output_path.exists():
                return None
            return output_path.read_bytes()
    except (OSError, subprocess.SubprocessError):
        return None


_SYSTEM_DRAWING_VISIO_PREVIEW_SCRIPT = r"""
param(
  [string]$JsonPath,
  [string]$OutputPath,
  [int]$CanvasWidth,
  [int]$CanvasHeight
)
Add-Type -AssemblyName System.Drawing
$items = Get-Content -LiteralPath $JsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
$bitmap = New-Object System.Drawing.Bitmap($CanvasWidth, $CanvasHeight)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$graphics.Clear([System.Drawing.Color]::White)
$borderPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(209, 218, 229), 1)
$panelBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(248, 250, 252))
$shapePen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(55, 101, 163), 1)
$linePen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(75, 85, 99), 2)
$shapeBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(226, 239, 255))
$textBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(15, 23, 42))
$format = New-Object System.Drawing.StringFormat
$format.Alignment = [System.Drawing.StringAlignment]::Center
$format.LineAlignment = [System.Drawing.StringAlignment]::Center
$format.Trimming = [System.Drawing.StringTrimming]::EllipsisCharacter
$format.FormatFlags = [System.Drawing.StringFormatFlags]::LineLimit
$font = $null
foreach ($family in @("Microsoft YaHei", "SimSun", "Arial Unicode MS", "Arial")) {
  try {
    $font = New-Object System.Drawing.Font($family, 10, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Point)
    break
  } catch {}
}
if ($font -eq $null) {
  $font = New-Object System.Drawing.Font([System.Drawing.FontFamily]::GenericSansSerif, 10)
}
$graphics.FillRectangle($panelBrush, 18, 18, $CanvasWidth - 36, $CanvasHeight - 36)
$graphics.DrawRectangle($borderPen, 18, 18, $CanvasWidth - 37, $CanvasHeight - 37)
foreach ($item in $items) {
  if ($item.type -eq "rect") {
    $x = [Math]::Min($item.x1, $item.x2)
    $y = [Math]::Min($item.y1, $item.y2)
    $w = [Math]::Max([Math]::Abs($item.x2 - $item.x1), 1)
    $h = [Math]::Max([Math]::Abs($item.y2 - $item.y1), 1)
    $graphics.FillRectangle($shapeBrush, $x, $y, $w, $h)
    $graphics.DrawRectangle($shapePen, $x, $y, $w, $h)
  }
}
foreach ($item in $items) {
  if ($item.type -eq "line") {
    $graphics.DrawLine($linePen, [float]$item.x1, [float]$item.y1, [float]$item.x2, [float]$item.y2)
  }
}
foreach ($item in $items) {
  if ([string]::IsNullOrWhiteSpace([string]$item.text)) {
    continue
  }
  $x = [Math]::Min($item.x1, $item.x2)
  $y = [Math]::Min($item.y1, $item.y2)
  $w = [Math]::Max([Math]::Abs($item.x2 - $item.x1), 28)
  $h = [Math]::Max([Math]::Abs($item.y2 - $item.y1), 18)
  if ($item.type -eq "line") {
    $x = (($item.x1 + $item.x2) / 2) - 55
    $y = (($item.y1 + $item.y2) / 2) - 14
    $w = 110
    $h = 28
  }
  $rect = New-Object System.Drawing.RectangleF([float]$x, [float]$y, [float]$w, [float]$h)
  $graphics.DrawString([string]$item.text, $font, $textBrush, $rect, $format)
}
$bitmap.Save($OutputPath, [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bitmap.Dispose()
"""


def _draw_generic_visio_preview(canvas: bytearray, width: int, height: int) -> None:
    left = width // 2 - 150
    top = height // 2 - 95
    _draw_rect(canvas, width, height, left, top, left + 300, top + 190, (55, 101, 163), fill=(226, 239, 255))
    _draw_rect(canvas, width, height, left + 25, top + 30, left + 115, top + 85, (37, 99, 235), fill=(219, 234, 254))
    _draw_rect(canvas, width, height, left + 185, top + 105, left + 275, top + 160, (37, 99, 235), fill=(219, 234, 254))
    _draw_line(canvas, width, height, left + 115, top + 58, left + 185, top + 132, (75, 85, 99), thickness=3)
    _draw_word(canvas, width, height, left + 77, top + 70, "VSDX", 8, (30, 64, 175))


def _draw_rect(
    canvas: bytearray,
    width: int,
    height: int,
    left: int,
    top: int,
    right: int,
    bottom: int,
    color: tuple[int, int, int],
    fill: tuple[int, int, int] | None = None,
) -> None:
    left, right = sorted((left, right))
    top, bottom = sorted((top, bottom))
    left = max(left, 0)
    right = min(right, width - 1)
    top = max(top, 0)
    bottom = min(bottom, height - 1)
    if left >= right or top >= bottom:
        return
    if fill:
        for y in range(top + 1, bottom):
            start = (y * width + left + 1) * 3
            length = max(right - left - 1, 0)
            canvas[start:start + length * 3] = bytes(fill) * length
    _draw_line(canvas, width, height, left, top, right, top, color)
    _draw_line(canvas, width, height, right, top, right, bottom, color)
    _draw_line(canvas, width, height, right, bottom, left, bottom, color)
    _draw_line(canvas, width, height, left, bottom, left, top, color)


def _draw_line(
    canvas: bytearray,
    width: int,
    height: int,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color: tuple[int, int, int],
    thickness: int = 1,
) -> None:
    dx = abs(x2 - x1)
    dy = -abs(y2 - y1)
    step_x = 1 if x1 < x2 else -1
    step_y = 1 if y1 < y2 else -1
    error = dx + dy
    while True:
        _draw_dot(canvas, width, height, x1, y1, color, thickness)
        if x1 == x2 and y1 == y2:
            break
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x1 += step_x
        if doubled <= dx:
            error += dx
            y1 += step_y


def _draw_dot(canvas: bytearray, width: int, height: int, x: int, y: int, color: tuple[int, int, int], size: int) -> None:
    radius = max(size // 2, 0)
    for yy in range(y - radius, y + radius + 1):
        if yy < 0 or yy >= height:
            continue
        for xx in range(x - radius, x + radius + 1):
            if xx < 0 or xx >= width:
                continue
            index = (yy * width + xx) * 3
            canvas[index:index + 3] = bytes(color)


def _draw_word(canvas: bytearray, width: int, height: int, x: int, y: int, text: str, scale: int, color: tuple[int, int, int]) -> None:
    cursor = x
    for char in text.upper():
        pattern = _LETTER_PATTERNS.get(char)
        if not pattern:
            cursor += 4 * scale
            continue
        for row_index, row in enumerate(pattern):
            for col_index, value in enumerate(row):
                if value != "1":
                    continue
                _draw_rect(
                    canvas,
                    width,
                    height,
                    cursor + col_index * scale,
                    y + row_index * scale,
                    cursor + (col_index + 1) * scale - 1,
                    y + (row_index + 1) * scale - 1,
                    color,
                    fill=color,
                )
        cursor += (len(pattern[0]) + 1) * scale


_LETTER_PATTERNS = {
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "S": ["11111", "10000", "10000", "11110", "00001", "00001", "11110"],
    "V": ["10001", "10001", "10001", "10001", "01010", "01010", "00100"],
    "X": ["10001", "01010", "00100", "00100", "00100", "01010", "10001"],
}


def _encode_png(width: int, height: int, rgb: bytearray) -> bytes:
    def chunk(name: bytes, content: bytes) -> bytes:
        return struct.pack(">I", len(content)) + name + content + struct.pack(">I", zlib.crc32(name + content) & 0xFFFFFFFF)

    rows = [b"\x00" + bytes(rgb[index:index + width * 3]) for index in range(0, len(rgb), width * 3)]
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")


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


def _set_paragraph_visio_object(
    document,
    paragraph,
    package_relationship_id: str,
    file_row: FileObject,
) -> None:
    relationship_index = package_relationship_id.removeprefix("rId")
    shape_id = f"_x0000_i{relationship_index}"

    object_paragraph = ET.Element(_qn(W, "p"))
    ET.SubElement(object_paragraph, _qn(W, "pPr"))
    object_run = ET.SubElement(object_paragraph, _qn(W, "r"))
    ET.SubElement(object_run, _qn(W, "rPr"))
    object_node = ET.SubElement(object_run, _qn(W, "object"))
    shape = ET.SubElement(
        object_node,
        _qn(V, "shape"),
        {
            "id": shape_id,
            "type": "#_x0000_t75",
            "style": "width:400pt;height:300pt",
            _qn(O, "ole"): "",
        },
    )
    ET.SubElement(
        shape,
        _qn(V, "imagedata"),
        {
            _qn(O, "relid"): package_relationship_id,
            "src": f"visio_placeholder{relationship_index}.png",
        },
    )
    ole_object = ET.SubElement(
        object_node,
        _qn(O, "OLEObject"),
        {
            _qn(R, "id"): package_relationship_id,
            "ProgID": "Visio.Drawing.15" if Path(file_row.file_name or "").suffix.lower() == ".vsdx" else "Visio.Drawing.12",
            "Type": "Embed",
            "ShapeID": shape_id,
            "DrawAspect": "Content",
            "ObjectID": f"_obj{relationship_index}",
        },
    )
    _insert_after_and_remove_anchor(document, paragraph, object_paragraph)


def _insert_after_and_remove_anchor(document, anchor, inserted) -> None:
    parent = _parent_map(document).get(anchor)
    if parent is None:
        _clear_paragraph_content(anchor)
        for child in list(inserted):
            anchor.append(child)
        return
    index = list(parent).index(anchor)
    parent.insert(index + 1, inserted)
    parent.remove(anchor)


def _next_relationship_id(relationships) -> str:
    used = {item.get("Id") for item in relationships}
    index = 1
    while f"rId{index}" in used:
        index += 1
    return f"rId{index}"


def _next_entry_index(entries: dict, prefix: str) -> int:
    return sum(1 for name in entries if name.startswith(prefix)) + 1


def _ensure_image_content_type(content_types, extension: str) -> None:
    extension = extension.lower()
    for item in content_types.findall(_qn(CT, "Default")):
        if (item.get("Extension") or "").lower() == extension:
            return
    mime = IMAGE_CONTENT_TYPES[extension]
    content_types.append(ET.Element(_qn(CT, "Default"), {"Extension": extension, "ContentType": mime}))


def _ensure_part_content_type(content_types, part_name: str, content_type: str) -> None:
    for item in content_types.findall(_qn(CT, "Override")):
        if item.get("PartName") == part_name:
            item.set("ContentType", content_type)
            return
    content_types.append(ET.Element(_qn(CT, "Override"), {"PartName": part_name, "ContentType": content_type}))


def _renumber_table_captions(document) -> None:
    captions = []
    for paragraph in document.iter(_qn(W, "p")):
        text = _element_text(paragraph).strip()
        match = re.match(r"^表\s*(\d+)\s*(.*)$", text)
        if match:
            captions.append((paragraph, int(match.group(1)), match.group(2)))
    number_map = {old: new for new, (_paragraph, old, _tail) in enumerate(captions, start=1)}
    _renumber_table_references(document, number_map)
    for new, (paragraph, _old, tail) in enumerate(captions, start=1):
        _set_paragraph_text(paragraph, f"表{new} {tail}".rstrip())


def _renumber_table_references(document, number_map: dict[int, int]) -> None:
    pattern = re.compile(r"表\s*(\d+)(?!\d)")
    for paragraph in document.iter(_qn(W, "p")):
        text_nodes = list(paragraph.iter(_qn(W, "t")))
        values = [node.text or "" for node in text_nodes]
        combined = "".join(values)
        matches = [
            match
            for match in pattern.finditer(combined)
            if number_map.get(int(match.group(1)), int(match.group(1))) != int(match.group(1))
        ]
        for match in reversed(matches):
            old = int(match.group(1))
            _replace_text_span(text_nodes, values, match.start(), match.end(), f"表{number_map[old]}")
            values = [node.text or "" for node in text_nodes]


def _replace_text_span(text_nodes: list, values: list[str], start: int, end: int, new: str) -> None:
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
        return
    text_nodes[start_index].text = values[start_index][:start_offset] + new
    for index in range(start_index + 1, end_index):
        text_nodes[index].text = ""
    text_nodes[end_index].text = values[end_index][end_offset:]


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
    raw = _payload_value(
        data["processing"],
        "involved_activities",
        "involvedActivities",
        "activity_types",
        "activityTypes",
    ) or []
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
    if isinstance(value, dict):
        return value
    allowed_keys = ACTIVITY_FLAT_DETAIL_KEYS.get(str(code or "").upper(), set())
    return {
        snake_key: field_value
        for keys, _label in ACTIVITY_DETAIL_FIELDS
        for snake_key in keys
        if snake_key in allowed_keys
        for field_value in [_payload_value(processing, snake_key, _to_camel(snake_key))]
        if field_value not in (None, "", [], {})
    }


def _processing_summary(data: dict) -> str:
    names = _activity_names(data)
    if not names:
        return "尚未填写数据处理活动调研信息"
    details = []
    for code in _activity_codes(data):
        payload = _activity_detail(data["processing"], code)
        description = _activity_detail_summary(payload)
        details.append(f"{_activity_name(code)}{f'（{description}）' if description else ''}")
    return f"主要涉及{'、'.join(details)}"


def _activity_narratives(data: dict) -> list[str]:
    rows = []
    for code in _activity_codes(data):
        activity_name = _activity_name(code)
        related_data = _activity_related_data(data, code)
        if not related_data:
            continue
        description = f"{activity_name}阶段：处理活动包括{activity_name}"
        data_text = _activity_related_data_summary(data, code, related_data)
        description += f"；涉及的数据包括{data_text}"
        rows.append(f"{description}。")
    return rows


def _activity_related_data(data: dict, code: str) -> list[str]:
    """提取阶段涉及的数据；优先使用该阶段专属字段，缺失时使用数据资产清单。"""
    processing = data["processing"]
    detail = _activity_detail(processing, code)
    values = []
    for field in ACTIVITY_RELATED_DATA_FIELDS.get(str(code or "").upper(), ()):
        value = _payload_value(detail, field, _to_camel(field))
        values.extend(_text_values(value))
    if values:
        return list(dict.fromkeys(values))

    assets = [*data["assets"], *data["personal_info"], *data["important_data"], *data["core_data"]]
    values.extend(_value(getattr(row, "data_name", None), "") for row in assets)
    if not any(values):
        for system in data["systems"]:
            values.extend(_text_values(getattr(system, "data_scopes", None)))
    return list(dict.fromkeys(value for value in values if value))


def _activity_related_data_summary(data: dict, code: str, values: list[str]) -> str:
    if not values:
        return ""
    if len(values) <= ACTIVITY_DATA_DETAIL_LIMIT:
        return "、".join(values)

    # 数据较多时只概括数据类别，避免在报告正文堆叠大量数据名称。
    categories = []
    if data["assets"]:
        categories.append("一般数据")
    if data["personal_info"]:
        categories.append("个人信息")
    if data["important_data"]:
        categories.append("重要数据")
    if data["core_data"]:
        categories.append("核心数据")
    if categories:
        return f"{'、'.join(categories)}等共{len(values)}项数据"
    return f"共{len(values)}项相关数据"


def _text_values(value) -> list[str]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = re.split(r"[、,，;；\r\n]+", str(value))
    return [" ".join(str(item).split()) for item in raw_values if " ".join(str(item).split())]


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
    items = _security_measure_items(data["security"])
    if not items:
        return "尚未填写安全防护措施调研信息"
    context = {
        "projectId": data["project"].id,
        "assessmentTarget": _report_target(data),
        "organizationName": _data_processor_name(data),
        "securityMeasures": items,
    }
    try:
        summary = llm_gateway_service.summarize_security_measures(context)
    except Exception as exc:
        logger.warning("安全措施段落大模型生成失败，使用本地摘要兜底。project_id=%s error=%s", data["project"].id, exc)
        summary = None
    return _clean_summary_sentence(summary) or _fallback_security_summary(items)


def _security_measure_items(security: dict) -> list[dict]:
    if not isinstance(security, dict):
        return []
    ordered_keys = [key for key in SECURITY_FIELD_NAMES if key in security]
    ordered_keys.extend(key for key in security if key not in SECURITY_FIELD_NAMES)
    items = []
    for key in ordered_keys:
        value = security.get(key)
        if value in (None, "", [], {}):
            continue
        items.append({"field": key, "label": SECURITY_FIELD_NAMES.get(key, key), "value": _list_text(value)})
    return items


def _fallback_security_summary(items: list[dict]) -> str:
    clauses = []
    for item in items:
        label = item["label"]
        value = item["value"]
        if label.endswith("措施"):
            clauses.append(f"已采用{value}等{label}")
        elif label in {"最小权限", "传输加密", "存储加密", "数据脱敏"}:
            clauses.append(f"{label}{value}")
        else:
            clauses.append(f"{label}为{value}")
    return "；".join(clauses)


def _clean_summary_sentence(value) -> str:
    text = str(value or "").strip()
    return text.rstrip("。；;，,. ")


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
