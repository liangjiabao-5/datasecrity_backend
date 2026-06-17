from __future__ import annotations

import binascii
import json
from io import BytesIO
import re
import struct
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import zipfile
import zlib

from app.services import docx_report_service
from app.services import file_service
from app.services import report_service


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
O = "urn:schemas-microsoft-com:office:office"
V = "urn:schemas-microsoft-com:vml"
OLE_OBJECT_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject"


def unwrap(response):
    assert response.status_code < 400, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["code"] == "SUCCESS"
    return payload["data"]


def create_project(client, project_code: str):
    return unwrap(
        client.post(
            "/api/v1/projects",
            json={
                "projectName": "报告生成测试项目",
                "projectCode": project_code,
                "assessmentOrg": "评估机构",
                "assessmentTemplateId": "tpl-gb",
                "scoreModelId": "score-v1",
                "harmModelId": "harm-default",
                "riskMatrixId": "matrix-v1",
                "systemType": "MANAGEMENT_SYSTEM",
            },
        )
    )["projectId"]


def test_report_docx_fills_data_tables_images_and_removes_comments(client, app, monkeypatch):
    fake_minio = _enable_fake_minio(app, monkeypatch)
    app.config["LLM_ENABLED"] = False
    project_id = create_project(client, "REPORT-DOCX-001")
    unwrap(client.post(f"/api/v1/projects/{project_id}/start"))
    unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/basic-info",
            json={
                "projectName": "核心业务系统",
                "laws": [{"name": "数据安全法"}],
                "standards": [{"name": "数据安全风险评估方法"}],
                "assessmentPlan": {"startDate": "2026-06-01", "endDate": "2026-06-30"},
                "organization": {
                    "name": "项目基本信息旧单位",
                    "postalCode": "100000",
                },
                "contacts": [
                    {
                        "name": "联系人",
                        "department": "安全部",
                        "mobile": "13800000000",
                        "title": "经理",
                        "phone": "010-12345678",
                        "email": "contact@example.com",
                    }
                ],
            },
        )
    )
    unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/plan/assessment-team",
            json={"name": "评估组长", "organization": "评估机构", "role": "组长"},
        )
    )
    unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/plan/client-team",
            json={"name": "配合人员", "department": "安全部", "position": "负责人"},
        )
    )
    stale_system = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/business-systems",
            json={"systemName": "旧业务系统调研记录", "businessFunction": "不应进入报告的旧附件"},
        )
    )
    unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/survey/data-processor-basic",
            json={
                "unitName": "调研数据处理者公司",
                "unifiedSocialCreditCode": "91110000TEST",
                "officeAddress": "调研办公地址",
                "dataSecurityOfficer": "调研负责人",
                "unitNature": "企业",
                "mainBusinessScope": "电力数据处理业务",
                "businessScale": "覆盖10万用户",
            },
        )
    )
    stale_topology_visio = _vsdx_with_preview(_png(400, 300))
    stale_data_flow_visio = _vsdx_with_preview(_png(401, 301))
    unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/business-systems/{stale_system['id']}/topology-diagram",
            data={"file": (BytesIO(stale_topology_visio), "stale-topology.vsdx")},
            content_type="multipart/form-data",
        )
    )
    unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/business-systems/{stale_system['id']}/data-flow-diagram",
            data={"file": (BytesIO(stale_data_flow_visio), "stale-data-flow.vsdx")},
            content_type="multipart/form-data",
        )
    )
    system = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/business-systems",
            json={"systemName": "业务系统调研记录一", "businessFunction": "办理核心业务", "dataScopes": ["业务数据", "个人信息"]},
        )
    )
    topology_preview = _png(900, 600)
    topology_visio = _vsdx_with_preview(topology_preview)
    data_flow_preview = _png(700, 450)
    data_flow_visio = _vsdx_with_preview(data_flow_preview)
    topology_upload = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/business-systems/{system['id']}/topology-diagram",
            data={"file": (BytesIO(topology_visio), "topology.vsdx")},
            content_type="multipart/form-data",
        )
    )
    data_flow_upload = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/business-systems/{system['id']}/data-flow-diagram",
            data={"file": (BytesIO(data_flow_visio), "data-flow.vsdx")},
            content_type="multipart/form-data",
        )
    )
    assert topology_upload["file"]["storageProvider"] == "MINIO"
    assert data_flow_upload["file"]["storageProvider"] == "MINIO"
    assert topology_visio in fake_minio.objects.values()
    assert data_flow_visio in fake_minio.objects.values()
    unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/business-systems",
            json={"systemName": "业务系统调研记录二", "businessFunction": "办理辅助业务"},
        )
    )
    for index in range(2):
        unwrap(
            client.post(
                f"/api/v1/projects/{project_id}/survey/data-assets",
                json={
                    "dataName": f"数据资产{index + 1}",
                    "dataForm": "结构化数据",
                    "dataScope": "生产数据",
                    "dataScale": "10万条",
                    "dataSource": "业务采集",
                    "storageLocation": "数据库",
                    "flowDescription": "系统内部流转",
                    "classified": True,
                    "dataCategory": "业务数据",
                    "dataLevel": "重要",
                    "personalInfo": False,
                },
            )
        )
    unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/personal-info",
            json={"dataName": "手机号", "dataCategory": "联系方式", "scale": "1万条", "sensitivity": "敏感", "dataSource": "用户", "businessFlow": "业务系统"},
        )
    )
    unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/important-data",
            json={"dataName": "重要数据", "dataCategory": "业务数据", "scale": "1万条", "dataSource": "业务系统", "businessFlow": "数据仓库"},
        )
    )
    unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/core-data",
            json={"dataName": "核心数据", "dataCategory": "业务数据", "scale": "1万条", "dataSource": "业务系统", "businessFlow": "核心系统"},
        )
    )
    unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/survey/processing-activity-survey",
            json={
                "involvedActivities": ["COLLECT", "STORE"],
                "activityTypes": ["TRANSFER"],
                "collectionChannels": "线上采集",
                "collectionPurpose": "业务办理",
                "collectionFrequency": "实时",
                "storageMethod": "数据库",
                "storageDuration": "持续",
                "transfer": {"methods": ["不应进入报告"]},
            },
        )
    )
    unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/survey/security-protection",
            json={
                "identityAuthenticationAndAccessControl": "密码、多因素认证",
                "dataSecurityManagement": "最小权限已落实",
            },
        )
    )
    item = unwrap(client.get(f"/api/v1/projects/{project_id}/evaluation/items?pageNo=1&pageSize=1"))["list"][0]
    unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/evaluation/items/{item['id']}/record",
            json={"evaluationResult": "PARTIAL", "evaluationRecord": "存在待整改问题"},
        )
    )
    unwrap(client.post(f"/api/v1/projects/{project_id}/risk-summary/refresh", json={}))

    readiness = unwrap(client.get(f"/api/v1/projects/{project_id}/reports/readiness"))
    assert readiness["ready"] is True
    generated = unwrap(client.post(f"/api/v1/projects/{project_id}/reports/generate", json={"reportName": "完整风险评估报告"}))
    assert generated["status"] == "SUCCESS"
    download = client.get(f"/api/v1/projects/{project_id}/reports/{generated['reportId']}/download")
    assert download.status_code == 200

    with zipfile.ZipFile(BytesIO(download.data)) as report, zipfile.ZipFile("doc/数据安全风险评估报告模版.docx") as template:
        names = report.namelist()
        raw_document = report.read("word/document.xml").decode("utf-8")
        root_index = raw_document.index("<w:document")
        root_start = raw_document[root_index:raw_document.index(">", root_index)]
        if 'mc:Ignorable="' in root_start:
            ignorable_prefixes = root_start.split('mc:Ignorable="', 1)[1].split('"', 1)[0].split()
            assert all(f"xmlns:{prefix}=" in root_start for prefix in ignorable_prefixes)
        document = ET.fromstring(report.read("word/document.xml"))
        text = "".join(node.text or "" for node in document.iter(f"{{{W}}}t"))
        paragraphs = _paragraph_texts(document)
        assert "2026年06月30日" in _paragraph_containing(paragraphs, "报告时间")
        security_paragraph = _paragraph_containing(paragraphs, "在安全措施方面")
        assert "调研数据处理者公司调研数据处理者公司" not in security_paragraph
        assert "密码" in security_paragraph
        assert "多因素认证" in security_paragraph
        assert "最小权限" in security_paragraph
        assert "已落实" in security_paragraph
        technical_paragraph = _paragraph_containing(paragraphs, "数据安全技术方面")
        assert "密码" in technical_paragraph
        assert "多因素认证" in technical_paragraph
        assert "最小权限" in technical_paragraph
        assert "已落实" in technical_paragraph
        for paragraph in _page_field_footer_paragraphs(report):
            paragraph_properties = paragraph.find(f"{{{W}}}pPr")
            justification = paragraph_properties.find(f"{{{W}}}jc") if paragraph_properties is not None else None
            assert justification is not None
            assert justification.get(f"{{{W}}}val") == "center"
        assert "业务系统调研记录一" in text
        assert "调研数据处理者公司" in text
        assert "调研办公地址" in text
        assert "单位名称为调研数据处理者公司" in text
        assert "项目基本信息旧单位" not in text
        assert "数据资产1" in text
        assert "存在待整改问题" in text
        assert "评估企业数据的数据安全风险，覆盖数据收集、数据存储等2个环节。" in text
        assert "业务架构、数据资产及数据收集、数据存储阶段等环节" in text
        assert "业务系统调研记录一主要涉及业务数据的数据收集、数据存储阶段。" in text
        assert "数据传输阶段" not in text
        assert "业务系统调研记录一的综合得分及风险发生可能等级" in text
        assert "1、0.5、0的综合得分及风险发生可能等级" not in text
        harm_caption = re.search(r"表(\d+) 数据安全风险危害程度示例", text)
        assert harm_caption is not None
        harm_table_no = harm_caption.group(1)
        assert f"各级别数据安全风险危害程度示例描述如下表{harm_table_no}所示" in text
        if harm_table_no != "10":
            assert "各级别数据安全风险危害程度示例描述如下表10所示" not in text
        assert "数据收集阶段：采集渠道为线上采集；收集目的为业务办理；收集频率为实时。" in text
        assert "数据存储阶段：存储方式为数据库；存储期限为持续。" in text
        assert "上传文件格式不支持插入 Word" not in text
        assert "已嵌入 Visio 文件：topology.vsdx" not in text
        assert "XX系统" not in text
        assert "XXXX公司" not in text
        assert "XX阶段" not in text
        assert not any(name.startswith("word/comments") for name in names)
        assert not list(document.iter(f"{{{W}}}commentRangeStart"))
        assert not list(document.iter(f"{{{W}}}commentRangeEnd"))
        assert not list(document.iter(f"{{{W}}}commentReference"))
        assert report.read("word/styles.xml") == template.read("word/styles.xml")
        settings = ET.fromstring(report.read("word/settings.xml"))
        update_fields = settings.find(f"{{{W}}}updateFields")
        assert update_fields is not None
        assert update_fields.get(f"{{{W}}}val") == "true"
        assert report.testzip() is None

        tables = list(document.iter(f"{{{W}}}tbl"))
        basic_rows = tables[0].findall(f"{{{W}}}tr")
        leader_cells = basic_rows[15].findall(f"{{{W}}}tc")
        assert _element_text(leader_cells[2]) == ""
        template_document = ET.fromstring(template.read("word/document.xml"))
        template_scope_cells = (
            list(template_document.iter(f"{{{W}}}tbl"))[5]
            .findall(f"{{{W}}}tr")[1]
            .findall(f"{{{W}}}tc")
        )
        scope_rows = tables[5].findall(f"{{{W}}}tr")
        assert len(scope_rows) == 2
        scope_cells = scope_rows[1].findall(f"{{{W}}}tc")
        assert [_element_text(cell) for cell in scope_cells] == [
            _element_text(template_scope_cells[0]),
            "业务系统调研记录一",
            "办理核心业务",
            _element_text(template_scope_cells[3]),
        ]
        asset_rows = tables[8].findall(f"{{{W}}}tr")
        assert len(asset_rows) == 3
        assert all(len(row.findall(f"{{{W}}}tc")) == 11 for row in asset_rows)
        for indicator_table in tables[1:5]:
            indicator_rows = indicator_table.findall(f"{{{W}}}tr")
            assert indicator_rows[1:]
            for row in indicator_rows[1:]:
                cells = row.findall(f"{{{W}}}tc")
                assert _element_text(cells[1]).strip()
                assert _element_text(row).strip() not in {"1", "2", "3", "4"}
        assert not any(
            "处理频率" in _element_text(table) and "数据处理活动" in _element_text(table)
            for table in tables
        )
        expected_columns = {
            1: 3,
            2: 3,
            3: 3,
            4: 3,
            5: 4,
            6: 3,
            7: 3,
            8: 11,
            9: 6,
            10: 5,
            11: 5,
            12: 5,
            13: 6,
            14: 6,
            17: 4,
            19: 2,
            20: 2,
            21: 3,
            23: 8,
            24: 6,
        }
        for table_index, column_count in expected_columns.items():
            assert all(
                len(row.findall(f"{{{W}}}tc")) == column_count
                for row in tables[table_index].findall(f"{{{W}}}tr")
            )
        relationships = ET.fromstring(report.read("word/_rels/document.xml.rels"))
        report_image_ids = {
            relationship.get("Id")
            for relationship in relationships.iter(f"{{{REL}}}Relationship")
            if (relationship.get("Target") or "").startswith("media/report-image-")
        }
        visio_relationships = [
            relationship
            for relationship in relationships.iter(f"{{{REL}}}Relationship")
            if relationship.get("Type") == OLE_OBJECT_REL_TYPE
            and (relationship.get("Target") or "").startswith("embeddings/report-visio-")
        ]
        content_types = ET.fromstring(report.read("[Content_Types].xml"))
        embedded_overrides = {
            item.get("PartName"): item.get("ContentType")
            for item in content_types.iter("{http://schemas.openxmlformats.org/package/2006/content-types}Override")
        }
        assert len(visio_relationships) == 2
        relationship_by_content = {
            report.read(f"word/{relationship.get('Target')}"): relationship
            for relationship in visio_relationships
        }
        assert stale_topology_visio not in relationship_by_content
        assert stale_data_flow_visio not in relationship_by_content
        _assert_visio_relationship(
            report,
            document,
            relationships,
            embedded_overrides,
            relationship_by_content[topology_visio],
            topology_preview,
            "图2 业务系统调研记录一网络拓扑图",
        )
        _assert_visio_relationship(
            report,
            document,
            relationships,
            embedded_overrides,
            relationship_by_content[data_flow_visio],
            data_flow_preview,
            "图3 业务系统调研记录一数据流转图",
        )
        extents = []
        for inline in document.iter(f"{{{WP}}}inline"):
            blip = inline.find(f".//{{{A}}}blip")
            if blip is not None and blip.get(f"{{{R}}}embed") in report_image_ids:
                extents.append(inline.find(f"{{{WP}}}extent"))
        assert len(extents) == 0
        assert all(int(extent.get("cx")) <= 6 * 914400 for extent in extents)
        assert all(int(extent.get("cy")) <= 4.5 * 914400 for extent in extents)
        report_image_names = [name for name in names if name.startswith("word/media/report-image-")]
        assert report_image_names == []
        assert [name for name in names if name.startswith("word/media/report-visio-preview-")] == []


def test_report_missing_content_is_marked_red(client):
    project_id = create_project(client, "REPORT-MISSING-001")
    generated = unwrap(client.post(f"/api/v1/projects/{project_id}/reports/generate", json={}))
    assert generated["status"] == "SUCCESS"

    download = client.get(f"/api/v1/projects/{project_id}/reports/{generated['reportId']}/download")
    assert download.status_code == 200
    with zipfile.ZipFile(BytesIO(download.data)) as report:
        document = ET.fromstring(report.read("word/document.xml"))
        missing_runs = []
        for run in document.iter(f"{{{W}}}r"):
            text = _element_text(run).strip()
            if text == "-" or any(marker in text for marker in ("尚未填写", "尚未配置", "未填写", "未上传")):
                missing_runs.append(run)

        assert missing_runs
        assert any("尚未填写数据处理活动具体情况" in _element_text(run) for run in missing_runs)
        assert any(_element_text(run).strip() == "-" for run in missing_runs)
        for run in missing_runs:
            color = run.find(f"{{{W}}}rPr/{{{W}}}color")
            assert color is not None
            assert color.get(f"{{{W}}}val") == "FF0000"


def test_security_measure_summary_uses_dashscope_prompt(app, monkeypatch):
    from app.services import llm_gateway_service

    app.config["LLM_ENABLED"] = True
    app.config["DASHSCOPE_API_KEY"] = "test-key"
    captured = {}

    def fake_chat_completion(config, messages):
        captured["config"] = config
        captured["messages"] = messages
        prompt = json.loads(messages[1]["content"])
        captured["input"] = prompt["input"]
        return json.dumps({"summary": "已采用密码和多因素认证等身份鉴别措施"}, ensure_ascii=False)

    monkeypatch.setattr(llm_gateway_service, "_chat_completion", fake_chat_completion)
    summary = llm_gateway_service.summarize_security_measures(
        {
            "projectId": "project-1",
            "assessmentTarget": "核心业务系统",
            "organizationName": "被评估单位",
            "securityMeasures": [{"field": "identity_auth_measures", "label": "身份鉴别措施", "value": "密码、多因素认证"}],
        }
    )

    assert summary == "已采用密码和多因素认证等身份鉴别措施"
    assert captured["config"]["api_key"] == "test-key"
    assert captured["input"]["securityMeasures"][0]["value"] == "密码、多因素认证"
    assert "不要重复输出单位名称" in captured["messages"][1]["content"]


def test_visio_attachment_embeds_original_file_as_ole_object_after_anchor(monkeypatch):
    monkeypatch.setattr(
        docx_report_service,
        "_visio_preview_image",
        lambda _content: (_ for _ in ()).throw(AssertionError("OLE embedding must not rewrite Visio as a generated preview")),
    )
    document = ET.Element(f"{{{W}}}document")
    body = ET.SubElement(document, f"{{{W}}}body")
    before = ET.SubElement(body, f"{{{W}}}p")
    _append_text_for_test(before, "网络拓扑图如下所示：")
    paragraph = ET.SubElement(body, f"{{{W}}}p")
    _append_text_for_test(paragraph, "拓扑图")
    after = ET.SubElement(body, f"{{{W}}}p")
    _append_text_for_test(after, "图2 测试评估对象网络拓扑图")
    relationships = ET.Element(f"{{{REL}}}Relationships")
    content_types = ET.Element("{http://schemas.openxmlformats.org/package/2006/content-types}Types")
    entries = {}
    content = _vsdx_without_preview()

    docx_report_service._insert_visio_attachment(
        document,
        paragraph,
        relationships,
        content_types,
        SimpleNamespace(file_name="topology.vsdx"),
        content,
        entries,
    )

    assert [name for name in entries if name.startswith("word/embeddings/report-visio-")] == ["word/embeddings/report-visio-1.vsdx"]
    assert [name for name in entries if name.startswith("word/media/report-visio-preview-")] == []
    relationship_by_target = {relationship.get("Target"): relationship for relationship in relationships}
    visio_relationship = relationship_by_target["embeddings/report-visio-1.vsdx"]
    assert visio_relationship.get("Type") == OLE_OBJECT_REL_TYPE
    assert list(body).index(before) == 0
    assert _element_text(list(body)[1]) == ""
    assert list(body)[2] is after

    object_paragraph = list(body)[1]
    object_node = object_paragraph.find(f".//{{{W}}}object")
    assert object_node is not None
    ole_object = object_node.find(f"{{{O}}}OLEObject")
    assert ole_object is not None
    assert ole_object.get(f"{{{R}}}id") == visio_relationship.get("Id")
    assert ole_object.get("Type") == "Embed"
    assert ole_object.get("ProgID") == "Visio.Drawing.15"
    assert ole_object.get("DrawAspect") == "Content"
    assert ole_object.get("ObjectID") == f"_obj{visio_relationship.get('Id').removeprefix('rId')}"
    shape = object_node.find(f"{{{V}}}shape")
    assert shape is not None
    assert shape.get("id") == ole_object.get("ShapeID")
    assert shape.get("style") == "width:400pt;height:300pt"
    imagedata = shape.find(f"{{{V}}}imagedata")
    assert imagedata is not None
    assert imagedata.get(f"{{{O}}}relid") == visio_relationship.get("Id")
    assert entries["word/embeddings/report-visio-1.vsdx"] == content


def test_visio_preview_shapes_keep_shape_text_without_changing_vsdx(monkeypatch):
    monkeypatch.setattr(docx_report_service, "_render_visio_preview_image", lambda _content: None)
    content = _vsdx_with_text_shape("资产识别")
    original = content[:]

    shapes = docx_report_service._visio_preview_shapes(content)
    preview = docx_report_service._visio_preview_image(content)

    assert any(shape["text"] == "资产识别" for shape in shapes)
    assert preview[0].startswith(b"\x89PNG\r\n\x1a\n")
    assert content == original


def test_visio_preview_shapes_use_connector_geometry_path(monkeypatch):
    monkeypatch.setattr(docx_report_service, "_render_visio_preview_image", lambda _content: None)
    content = _vsdx_with_routed_connector()

    shapes = docx_report_service._visio_preview_shapes(content)

    lines = [shape for shape in shapes if shape["type"] == "line"]
    assert len(lines) == 2
    assert [(line["x1"], line["y1"], line["x2"], line["y2"]) for line in lines] == [
        (1.0, 1.0, 2.0, 1.0),
        (2.0, 1.0, 2.0, 2.0),
    ]


def _assert_visio_relationship(
    report,
    document,
    relationships,
    embedded_overrides: dict,
    visio_relationship,
    expected_preview: bytes,
    expected_following_caption: str,
) -> None:
    assert visio_relationship.get("Type") == OLE_OBJECT_REL_TYPE
    assert visio_relationship.get("Target").startswith("embeddings/report-visio-")
    assert visio_relationship.get("Target").endswith(".vsdx")
    assert embedded_overrides[f"/word/{visio_relationship.get('Target')}"] == "application/vnd.ms-visio.drawing"
    ole_objects = [
        ole_object
        for ole_object in document.iter(f"{{{O}}}OLEObject")
        if ole_object.get(f"{{{R}}}id") == visio_relationship.get("Id")
    ]
    assert len(ole_objects) == 1
    assert ole_objects[0].get("ProgID") == "Visio.Drawing.15"
    assert ole_objects[0].get("Type") == "Embed"
    assert ole_objects[0].get("DrawAspect") == "Content"
    shape_id = ole_objects[0].get("ShapeID")
    assert shape_id == f"_x0000_i{visio_relationship.get('Id').removeprefix('rId')}"
    assert ole_objects[0].get("ObjectID") == f"_obj{visio_relationship.get('Id').removeprefix('rId')}"

    parent_map = _parent_map(document)
    object_node = parent_map.get(ole_objects[0])
    assert object_node is not None
    assert object_node.tag == f"{{{W}}}object"
    shape = object_node.find(f"{{{V}}}shape")
    assert shape is not None
    assert shape.get("id") == shape_id
    assert shape.get("type") == "#_x0000_t75"
    assert shape.get(f"{{{O}}}ole") == ""
    assert shape.get("style") == "width:400pt;height:300pt"
    imagedata = shape.find(f"{{{V}}}imagedata")
    assert imagedata is not None
    assert imagedata.get(f"{{{O}}}relid") == visio_relationship.get("Id")
    assert imagedata.get("src") == f"visio_placeholder{visio_relationship.get('Id').removeprefix('rId')}.png"

    paragraphs = list(document.iter(f"{{{W}}}p"))
    object_paragraph = _paragraph_for(ole_objects[0], parent_map)
    object_index = paragraphs.index(object_paragraph)
    assert _next_non_empty_paragraph_text(paragraphs, object_index) == expected_following_caption


def _paragraph_for(element, parent_map: dict):
    current = element
    while current is not None and current.tag != f"{{{W}}}p":
        current = parent_map.get(current)
    assert current is not None
    return current


def _next_non_empty_paragraph_text(paragraphs: list, start_index: int) -> str:
    for paragraph in paragraphs[start_index + 1:]:
        value = _element_text(paragraph).strip()
        if value:
            return value
    raise AssertionError("No following non-empty paragraph found.")


def _parent_map(root) -> dict:
    return {child: parent for parent in root.iter() for child in parent}


def _append_text_for_test(paragraph, value: str) -> None:
    run = ET.SubElement(paragraph, f"{{{W}}}r")
    text = ET.SubElement(run, f"{{{W}}}t")
    text.text = value


def _enable_fake_minio(app, monkeypatch):
    fake = _FakeMinio()
    app.config.update(
        MINIO_ENDPOINT="minio.local:9000",
        MINIO_ACCESS_KEY="access",
        MINIO_SECRET_KEY="secret",
        MINIO_BUCKET_NAME="datasecurity-test",
        MINIO_SECURE=False,
    )
    monkeypatch.setattr(file_service, "_minio_client", lambda: fake)
    return fake


class _FakeMinio:
    def __init__(self):
        self.buckets = set()
        self.objects = {}

    def bucket_exists(self, bucket_name: str) -> bool:
        return bucket_name in self.buckets

    def make_bucket(self, bucket_name: str) -> None:
        self.buckets.add(bucket_name)

    def put_object(self, bucket_name: str, object_key: str, stream, length: int, content_type: str) -> None:
        self.objects[(bucket_name, object_key)] = stream.read(length)

    def get_object(self, bucket_name: str, object_key: str):
        return _FakeMinioResponse(self.objects[(bucket_name, object_key)])


class _FakeMinioResponse:
    def __init__(self, content: bytes):
        self.content = content

    def read(self) -> bytes:
        return self.content

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


def test_report_failure_records_error_and_retry_succeeds(client, monkeypatch):
    project_id = create_project(client, "REPORT-RETRY-001")
    original = docx_report_service.generate_document
    calls = {"count": 0}

    def flaky_generate(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("report template render failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(docx_report_service, "generate_document", flaky_generate)
    generated = unwrap(client.post(f"/api/v1/projects/{project_id}/reports/generate", json={}))
    assert generated["status"] == "FAILED"
    failed_task = unwrap(client.get(f"/api/v1/projects/{project_id}/reports/tasks/{generated['reportTaskId']}"))
    assert failed_task["status"] == "FAILED"
    assert "report template render failed" in failed_task["errorMessage"]
    reports = unwrap(client.get(f"/api/v1/projects/{project_id}/reports?pageNo=1&pageSize=10"))
    assert reports["list"][0]["status"] == "FAILED"
    assert "report template render failed" in reports["list"][0]["errorMessage"]

    retried = unwrap(client.post(f"/api/v1/projects/{project_id}/reports/{generated['reportId']}/retry"))
    assert retried["status"] == "SUCCESS"
    assert client.get(f"/api/v1/projects/{project_id}/reports/{generated['reportId']}/download").status_code == 200


def test_report_task_can_be_created_pending_then_executed(client, monkeypatch):
    project_id = create_project(client, "REPORT-TASK-001")
    invalid = client.post(f"/api/v1/projects/{project_id}/reports/generate", json={"selectedSections": []})
    assert invalid.status_code == 400
    assert invalid.get_json()["code"] == "INVALID_REPORT_SECTIONS"
    monkeypatch.setattr(report_service, "_enqueue_report_task", lambda *_args: None)
    generated = unwrap(client.post(f"/api/v1/projects/{project_id}/reports/generate", json={}))
    assert generated["status"] == "PENDING"
    pending = unwrap(client.get(f"/api/v1/projects/{project_id}/reports/tasks/{generated['reportTaskId']}"))
    assert pending["status"] == "PENDING"
    cannot_delete = client.delete(f"/api/v1/projects/{project_id}/reports/{generated['reportId']}")
    assert cannot_delete.status_code == 400
    assert cannot_delete.get_json()["code"] == "REPORT_DELETE_NOT_ALLOWED"

    report_service.execute_report_task(project_id, generated["reportId"], generated["reportTaskId"])
    completed = unwrap(client.get(f"/api/v1/projects/{project_id}/reports/tasks/{generated['reportTaskId']}"))
    assert completed["status"] == "SUCCESS"


def _png(width: int, height: int) -> bytes:
    def chunk(name: bytes, content: bytes) -> bytes:
        return struct.pack(">I", len(content)) + name + content + struct.pack(">I", binascii.crc32(name + content) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\x33\x66\x99" * width
    image_data = zlib.compress(row * height)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", image_data) + chunk(b"IEND", b"")


def _vsdx_with_preview(preview: bytes) -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/visio/document.xml" ContentType="application/vnd.ms-visio.drawing.main+xml"/>
</Types>""",
        )
        package.writestr("docProps/thumbnail.png", preview)
        package.writestr("visio/document.xml", "<VisioDocument/>")
    return stream.getvalue()


def _vsdx_without_preview() -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/visio/document.xml" ContentType="application/vnd.ms-visio.drawing.main+xml"/>
</Types>""",
        )
        package.writestr("visio/document.xml", "<VisioDocument/>")
    return stream.getvalue()


def _vsdx_with_text_shape(text: str) -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/visio/pages/page1.xml" ContentType="application/vnd.ms-visio.page+xml"/>
</Types>""",
        )
        package.writestr(
            "visio/pages/page1.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">
  <Shapes>
    <Shape ID="1" NameU="Process">
      <Cell N="PinX" V="2"/>
      <Cell N="PinY" V="1.5"/>
      <Cell N="Width" V="1.8"/>
      <Cell N="Height" V="0.6"/>
      <Text>{text}</Text>
    </Shape>
  </Shapes>
</PageContents>""",
        )
    return stream.getvalue()


def _vsdx_with_routed_connector() -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/visio/pages/page1.xml" ContentType="application/vnd.ms-visio.page+xml"/>
</Types>""",
        )
        package.writestr(
            "visio/pages/page1.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">
  <Shapes>
    <Shape ID="1" NameU="Dynamic connector">
      <Cell N="PinX" V="0"/>
      <Cell N="PinY" V="0"/>
      <Cell N="LocPinX" V="0"/>
      <Cell N="LocPinY" V="0"/>
      <Cell N="BeginX" V="0"/>
      <Cell N="BeginY" V="0"/>
      <Cell N="EndX" V="3"/>
      <Cell N="EndY" V="3"/>
      <Section N="Geometry" IX="0">
        <Row T="MoveTo" IX="1"><Cell N="X" V="1"/><Cell N="Y" V="1"/></Row>
        <Row T="LineTo" IX="2"><Cell N="X" V="2"/><Cell N="Y" V="1"/></Row>
        <Row T="LineTo" IX="3"><Cell N="X" V="2"/><Cell N="Y" V="2"/></Row>
      </Section>
    </Shape>
  </Shapes>
</PageContents>""",
        )
    return stream.getvalue()


def _element_text(element) -> str:
    return "".join(node.text or "" for node in element.iter(f"{{{W}}}t"))


def _paragraph_texts(element) -> list[str]:
    return [_element_text(paragraph) for paragraph in element.iter(f"{{{W}}}p")]


def _paragraph_containing(paragraphs: list[str], needle: str) -> str:
    return next(paragraph for paragraph in paragraphs if needle in paragraph)


def _page_field_footer_paragraphs(report) -> list:
    paragraphs = []
    for name in report.namelist():
        if not name.startswith("word/footer") or not name.endswith(".xml"):
            continue
        footer = ET.fromstring(report.read(name))
        for paragraph in footer.iter(f"{{{W}}}p"):
            field_codes = "".join(node.text or "" for node in paragraph.iter(f"{{{W}}}instrText"))
            if "PAGE" in field_codes:
                paragraphs.append(paragraph)
    return paragraphs
