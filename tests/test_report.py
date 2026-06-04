from __future__ import annotations

import binascii
from io import BytesIO
import struct
from xml.etree import ElementTree as ET
import zipfile
import zlib

from app.services import docx_report_service
from app.services import report_service


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"


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


def test_report_docx_fills_data_tables_images_and_removes_comments(client):
    project_id = create_project(client, "REPORT-DOCX-001")
    unwrap(client.post(f"/api/v1/projects/{project_id}/start"))
    unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/basic-info",
            json={
                "projectDescription": "项目描述",
                "systemDescription": "系统描述",
                "laws": [{"name": "数据安全法"}],
                "standards": [{"name": "数据安全风险评估方法"}],
                "assessmentPlan": {"startDate": "2026-06-01", "endDate": "2026-06-30"},
                "assessmentTarget": "核心业务系统",
                "organization": {
                    "name": "被评估单位",
                    "address": "北京市海淀区",
                    "creditCode": "91110000TEST",
                    "postalCode": "100000",
                    "dataSecurityOwner": "安全负责人",
                    "description": "被评估单位简介",
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
    system = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/business-systems",
            json={"systemName": "核心业务系统", "businessFunction": "办理核心业务"},
        )
    )
    image = _png(1200, 800)
    unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/business-systems/{system['id']}/topology-diagram",
            data={"file": (BytesIO(image), "topology.png")},
            content_type="multipart/form-data",
        )
    )
    unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/business-systems/{system['id']}/data-flow-diagram",
            data={"file": (BytesIO(image), "data-flow.png")},
            content_type="multipart/form-data",
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
                "activityTypes": ["COLLECT", "STORE"],
                "collect": {"scenarios": ["线上采集"], "purpose": "业务办理", "frequency": "实时"},
                "store": {"purpose": "业务留存", "frequency": "持续"},
            },
        )
    )
    unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/survey/security-protection",
            json={"identityAuthMeasures": ["密码", "多因素认证"], "leastPrivilege": "已落实"},
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
        document = ET.fromstring(report.read("word/document.xml"))
        text = "".join(node.text or "" for node in document.iter(f"{{{W}}}t"))
        assert "核心业务系统" in text
        assert "被评估单位" in text
        assert "数据资产1" in text
        assert "存在待整改问题" in text
        assert "业务架构、数据资产及数据收集、数据存储阶段等环节" in text
        assert "核心业务系统主要涉及业务数据的数据收集、数据存储阶段。" in text
        assert "数据收集阶段：处理场景为线上采集；处理目的为业务办理；处理频率为实时。" in text
        assert "数据存储阶段：处理目的为业务留存；处理频率为持续。" in text
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
        scope_cells = tables[5].findall(f"{{{W}}}tr")[1].findall(f"{{{W}}}tc")
        assert [_element_text(cell) for cell in scope_cells] == ["", "核心业务系统", "系统描述", ""]
        asset_rows = tables[8].findall(f"{{{W}}}tr")
        assert len(asset_rows) == 3
        assert all(len(row.findall(f"{{{W}}}tc")) == 11 for row in asset_rows)
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
            12: 7,
            13: 5,
            14: 6,
            15: 6,
            18: 4,
            20: 2,
            21: 2,
            22: 3,
            24: 8,
            25: 6,
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
        extents = []
        for inline in document.iter(f"{{{WP}}}inline"):
            blip = inline.find(f".//{{{A}}}blip")
            if blip is not None and blip.get(f"{{{R}}}embed") in report_image_ids:
                extents.append(inline.find(f"{{{WP}}}extent"))
        assert len(extents) == 2
        assert all(int(extent.get("cx")) <= 6 * 914400 for extent in extents)
        assert all(int(extent.get("cy")) <= 4.5 * 914400 for extent in extents)
        assert len([name for name in names if name.startswith("word/media/report-image-")]) == 2


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


def _element_text(element) -> str:
    return "".join(node.text or "" for node in element.iter(f"{{{W}}}t"))
