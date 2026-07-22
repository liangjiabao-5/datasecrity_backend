import zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from app.services import docx_report_service


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _template_document():
    with zipfile.ZipFile("doc/数据安全风险评估报告模版.docx") as archive:
        return ET.fromstring(archive.read("word/document.xml"))


def test_report_template_adjusted_tables_are_located_by_headers():
    document = _template_document()
    tables = list(document.iter(f"{{{W}}}tbl"))

    harm_table = docx_report_service._find_table_by_headers(tables, "风险危害程度", "危害程度分析")
    score_range_table = docx_report_service._find_table_by_headers(tables, "等级", "综合得分")
    score_result_table = docx_report_service._find_table_by_headers(tables, "评估项k定性判定", "评估项k的得分")

    assert len(harm_table.findall(f"{{{W}}}tr")[0].findall(f"{{{W}}}tc")) == 5
    assert tables.index(score_result_table) < tables.index(score_range_table)


def test_obsolete_system_risk_headings_are_removed():
    document = _template_document()
    docx_report_service._remove_obsolete_risk_system_headings(document)
    paragraphs = [docx_report_service._element_text(node).strip() for node in document.iter(f"{{{W}}}p")]

    assert "XX系统风险源识别" not in paragraphs
    assert "XX系统风险危害程度判定" not in paragraphs
    assert "XX系统安全风险清单" not in paragraphs


def test_harm_rows_include_harm_analysis_column():
    risk = SimpleNamespace(
        risk_types=["数据泄露"],
        risk_description="访问控制不足",
        harm_level="HIGH",
        harm_description="可能导致重要业务数据泄露。",
    )

    assert docx_report_service._harm_rows({"risks": [risk]}) == [
        [1, "数据泄露", "访问控制不足", "高", "可能导致重要业务数据泄露。"]
    ]


def test_activity_narratives_only_include_activity_and_related_data():
    data = {
        "processing": {
            "involved_activities": ["COLLECT", "STORE"],
            "collection_data_scope": "客户信息、订单信息",
            "collection_channels": "线上采集",
            "storage_method": "数据库",
        },
        "legacy_activities": [],
        "assets": [SimpleNamespace(data_name="业务数据")],
        "personal_info": [],
        "important_data": [],
        "core_data": [],
        "systems": [],
    }

    rows = docx_report_service._activity_narratives(data)

    assert rows == [
        "数据收集阶段：处理活动包括数据收集；涉及的数据包括客户信息、订单信息。",
        "数据存储阶段：处理活动包括数据存储；涉及的数据包括业务数据。",
    ]
    assert all("采集渠道" not in row and "存储方式" not in row for row in rows)


def test_activity_narratives_summarize_many_data_items_and_omit_empty_data():
    data = {
        "processing": {"involved_activities": ["STORE", "DELETE"]},
        "legacy_activities": [],
        "assets": [SimpleNamespace(data_name=f"业务数据{i}") for i in range(1, 5)],
        "personal_info": [SimpleNamespace(data_name="手机号")],
        "important_data": [SimpleNamespace(data_name="重要运营数据")],
        "core_data": [],
        "systems": [],
    }

    assert docx_report_service._activity_narratives(data) == [
        "数据存储阶段：处理活动包括数据存储；涉及的数据包括一般数据、个人信息、重要数据等共6项数据。",
        "数据删除阶段：处理活动包括数据删除；涉及的数据包括一般数据、个人信息、重要数据等共6项数据。",
    ]

    data.update({"assets": [], "personal_info": [], "important_data": [], "systems": []})
    assert docx_report_service._activity_narratives(data) == []
