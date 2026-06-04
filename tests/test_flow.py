from io import BytesIO
from xml.etree import ElementTree as ET
import zipfile

from openpyxl import load_workbook

from app.extensions import SessionLocal
from app.models import EvaluationRecord


def unwrap(response):
    assert response.status_code < 400, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["code"] == "SUCCESS"
    return payload["data"]


def create_project(client, score_model_id="score-v1", project_code="ENST-TEST-001", risk_matrix_id="matrix-v1"):
    return unwrap(
        client.post(
            "/api/v1/projects",
            json={
                "projectName": "Flow Project",
                "projectCode": project_code,
                "assessmentOrg": "Test Org",
                "assessmentTemplateId": "tpl-gb",
                "scoreModelId": score_model_id,
                "harmModelId": "harm-default",
                "riskMatrixId": risk_matrix_id,
                "systemType": "MANAGEMENT_SYSTEM",
            },
        )
    )["projectId"]


def test_project_create_start_and_list(client):
    project_id = create_project(client)
    assert unwrap(client.get("/api/v1/projects/statistics"))["notStarted"] == 1

    started = unwrap(client.post(f"/api/v1/projects/{project_id}/start"))
    assert started["status"] == "IN_PROGRESS"
    assert started["generatedItemCount"] > 0

    restarted = unwrap(client.post(f"/api/v1/projects/{project_id}/start"))
    assert restarted["generatedItemCount"] == 0
    assert restarted["existingItemCount"] > 0

    projects = unwrap(client.get("/api/v1/projects?pageNo=1&pageSize=10"))
    assert projects["total"] == 1


def test_default_risk_matrix_matches_linkage_chart(client):
    from app.services import harm_analysis_service
    from app.services.project_service import get_project

    project_id = create_project(client, project_code="ENST-TEST-MATRIX")
    project = get_project(project_id)
    session = SessionLocal()
    expected = {
        "HIGH": {
            "VERY_HIGH": "MAJOR",
            "HIGH": "MAJOR",
            "RELATIVELY_HIGH": "MEDIUM",
            "MEDIUM": "LOW",
            "LOW": "SLIGHT",
        },
        "MEDIUM": {
            "VERY_HIGH": "MAJOR",
            "HIGH": "HIGH",
            "RELATIVELY_HIGH": "MEDIUM",
            "MEDIUM": "LOW",
            "LOW": "SLIGHT",
        },
        "LOW": {
            "VERY_HIGH": "MEDIUM",
            "HIGH": "MEDIUM",
            "RELATIVELY_HIGH": "LOW",
            "MEDIUM": "SLIGHT",
            "LOW": "SLIGHT",
        },
    }
    for possibility_level, row in expected.items():
        for harm_level, risk_level in row.items():
            assert harm_analysis_service.risk_level_from_project_matrix(session, project, harm_level, possibility_level) == risk_level


def test_risk_level_uses_project_selected_knowledge_matrix(client):
    from app.services import harm_analysis_service
    from app.services.project_service import get_project

    custom_matrix = unwrap(
        client.post(
            "/api/v1/knowledge/risk-matrices",
            json={
                "matrixName": "项目自定义风险评价矩阵",
                "matrixJson": {
                    "HIGH": {
                        "VERY_HIGH": "MAJOR",
                        "HIGH": "MAJOR",
                        "RELATIVELY_HIGH": "MAJOR",
                        "MEDIUM": "HIGH",
                        "LOW": "MEDIUM",
                    },
                    "MEDIUM": {
                        "VERY_HIGH": "HIGH",
                        "HIGH": "HIGH",
                        "RELATIVELY_HIGH": "HIGH",
                        "MEDIUM": "MEDIUM",
                        "LOW": "LOW",
                    },
                    "LOW": {
                        "VERY_HIGH": "MEDIUM",
                        "HIGH": "MEDIUM",
                        "RELATIVELY_HIGH": "MEDIUM",
                        "MEDIUM": "LOW",
                        "LOW": "SLIGHT",
                    },
                },
            },
        )
    )
    project_id = create_project(client, project_code="ENST-TEST-CUSTOM-MATRIX", risk_matrix_id=custom_matrix["id"])
    project = get_project(project_id)
    session = SessionLocal()

    assert harm_analysis_service.risk_level_from_project_matrix(session, project, "RELATIVELY_HIGH", "HIGH") == "MAJOR"


def test_score_uses_selected_score_model_result_values(client):
    score_model = unwrap(
        client.post(
            "/api/v1/knowledge/score-models",
            json={
                "modelName": "Custom Result Value Model",
                "modelType": "CUSTOM",
                "resultScores": {
                    "COMPLIANT": 0.8,
                    "PARTIAL": 0.25,
                    "NON_COMPLIANT": 0.1,
                    "NOT_APPLICABLE": None,
                },
                "possibilityRanges": [
                    {"level": "HIGH", "min": 0, "max": 60, "includeMin": True, "includeMax": False},
                    {"level": "MEDIUM", "min": 60, "max": 80, "includeMin": True, "includeMax": False},
                    {"level": "LOW", "min": 80, "max": 100, "includeMin": True, "includeMax": True},
                ],
            },
        )
    )
    project_id = create_project(client, score_model_id=score_model["id"], project_code="ENST-TEST-PARTIAL")
    unwrap(client.post(f"/api/v1/projects/{project_id}/start"))

    items = unwrap(client.get(f"/api/v1/projects/{project_id}/evaluation/items?pageNo=1&pageSize=3"))
    for item, result in zip(items["list"], ["COMPLIANT", "PARTIAL", "NON_COMPLIANT"]):
        unwrap(
            client.put(
                f"/api/v1/projects/{project_id}/evaluation/items/{item['id']}/record",
                json={"evaluationResult": result, "evaluationRecord": f"{result} record"},
            )
        )

    score = unwrap(client.post(f"/api/v1/projects/{project_id}/evaluation/calculate-score"))
    assert score["score"] == 38.33
    assert score["detail"]["compliantCount"] == 1
    assert score["detail"]["partialCount"] == 1
    assert score["detail"]["nonCompliantCount"] == 1
    assert score["scoreModelVersion"] == score_model["version"]


def test_risk_refresh_uses_score_model_boundary_for_possibility(client):
    score_model = unwrap(
        client.post(
            "/api/v1/knowledge/score-models",
            json={
                "modelName": "Boundary Model",
                "modelType": "CUSTOM",
                "resultScores": {
                    "COMPLIANT": 1,
                    "PARTIAL": 0.5,
                    "NON_COMPLIANT": 0,
                    "NOT_APPLICABLE": None,
                },
                "possibilityRanges": [
                    {"level": "HIGH", "min": 0, "max": 30, "includeMin": True, "includeMax": False},
                    {"level": "MEDIUM", "min": 30, "max": 75, "includeMin": True, "includeMax": False},
                    {"level": "LOW", "min": 75, "max": 100, "includeMin": True, "includeMax": True},
                ],
            },
        )
    )
    project_id = create_project(client, score_model_id=score_model["id"], project_code="ENST-TEST-BOUNDARY")
    unwrap(client.post(f"/api/v1/projects/{project_id}/start"))

    items = unwrap(client.get(f"/api/v1/projects/{project_id}/evaluation/items?pageNo=1&pageSize=2"))
    unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/evaluation/items/{items['list'][0]['id']}/record",
            json={"evaluationResult": "COMPLIANT", "evaluationRecord": "compliant"},
        )
    )
    unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/evaluation/items/{items['list'][1]['id']}/record",
            json={"evaluationResult": "PARTIAL", "evaluationRecord": "partial"},
        )
    )

    score = unwrap(client.post(f"/api/v1/projects/{project_id}/evaluation/calculate-score"))
    assert score["score"] == 75.0
    assert score["possibilityLevel"] == "LOW"

    refreshed = unwrap(client.post(f"/api/v1/projects/{project_id}/risk-summary/refresh", json={}))
    assert refreshed["score"] == 75.0
    assert refreshed["possibilityLevel"] == "LOW"

    sources = unwrap(client.get(f"/api/v1/projects/{project_id}/risk-sources?pageNo=1&pageSize=1"))
    assert sources["pageNo"] == 1
    assert sources["pageSize"] == 1
    assert sources["total"] == 1
    assert sources["list"][0]["possibilityLevel"] == "LOW"


def test_basic_plan_survey_evaluation_and_risk_flow(client):
    project_id = create_project(client)
    unwrap(client.post(f"/api/v1/projects/{project_id}/start"))

    basic = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/basic-info",
            json={
                "projectDescription": "desc",
                "systemDescription": "system",
                "laws": [{"id": "law-1", "name": "law"}],
                "standards": [{"id": "std-1", "name": "std"}],
                "assessmentPlan": {"startDate": "2026-06-01", "endDate": "2026-06-30"},
                "assessmentTarget": "target",
                "organization": {"name": "org"},
                "contacts": [{"name": "alice", "email": "alice@example.com"}],
            },
        )
    )
    assert basic["organization"]["name"] == "org"

    team = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/plan/assessment-team",
            json={"name": "Alice", "organization": "ENST", "role": "leader"},
        )
    )
    assert team["name"] == "Alice"

    asset = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/data-assets",
            json={"dataName": "Customer Data", "dataLevel": "important", "personalInfo": True},
        )
    )
    assert asset["dataName"] == "Customer Data"

    personal = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/personal-info",
            json={
                "dataName": "用户手机号",
                "dataCategory": "个人信息-联系方式",
                "scale": "10万条",
                "dataSource": "用户注册",
                "businessFlow": "注册后进入客户管理系统",
                "sensitivity": "敏感",
            },
        )
    )
    assert personal["dataName"] == "用户手机号"
    assert personal["businessFlow"] == "注册后进入客户管理系统"

    activity_survey_default = unwrap(client.get(f"/api/v1/projects/{project_id}/survey/processing-activity-survey"))
    assert activity_survey_default["activityTypes"] == []
    activity_survey = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/survey/processing-activity-survey",
            json={
                "activityTypes": ["COLLECT", "TRANSFER"],
                "collect": {"scenarios": ["线上采集"], "methods": ["接口采集"]},
                "transfer": {"securityMeasures": ["传输通道加密"]},
            },
        )
    )
    assert activity_survey["collect"]["methods"] == ["接口采集"]

    protection = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/survey/security-protection",
            json={
                "classifiedProtectionAssessment": "已开展",
                "identityAuthMeasures": ["密码", "多因素认证"],
                "threatTypesDetectedLastYear": ["恶意扫描", "弱口令"],
                "incidentDetailDescription": "未发生重大事件",
            },
        )
    )
    assert protection["identityAuthMeasures"] == ["密码", "多因素认证"]

    items = unwrap(client.get(f"/api/v1/projects/{project_id}/evaluation/items?pageNo=1&pageSize=1"))
    item_id = items["list"][0]["id"]
    saved_record = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/evaluation/items/{item_id}/record",
            json={
                "evaluationResult": "PARTIAL",
                "evaluationRecord": "record",
            },
        )
    )
    assert saved_record["evaluationRecord"] == "record"
    assert "problemDescription" not in saved_record
    assert "riskTypes" not in saved_record
    score = unwrap(client.post(f"/api/v1/projects/{project_id}/evaluation/calculate-score"))
    assert score["detail"]["partialCount"] == 1

    refreshed = unwrap(client.post(f"/api/v1/projects/{project_id}/risk-summary/refresh", json={}))
    assert refreshed["createdSources"] == 1

    sources = unwrap(client.get(f"/api/v1/projects/{project_id}/risk-sources?pageNo=1&pageSize=10"))
    assert sources["total"] == 1
    source_id = sources["list"][0]["id"]
    assert sources["list"][0]["riskRecordId"] == source_id
    assert sources["list"][0]["checkPoint"]
    assert sources["list"][0]["evaluationRecord"] == "record"
    assert sources["list"][0]["assessmentCategory"]
    assert sources["list"][0]["assessmentSubcategory"]
    assert sources["list"][0]["riskDescription"]
    assert sources["list"][0]["riskTypes"]
    assert sources["list"][0]["relatedData"] == "-"
    assert sources["list"][0]["harmLevel"] == "-"
    assert sources["list"][0]["possibilityLevel"] == "HIGH"
    assert sources["list"][0]["riskLevel"] is None
    risk_items = unwrap(client.get(f"/api/v1/projects/{project_id}/risk-items?pageNo=1&pageSize=1"))
    assert risk_items["pageSize"] == 1
    assert risk_items["total"] == 1
    risk_suggestions = unwrap(client.get(f"/api/v1/projects/{project_id}/risk-suggestions?pageNo=1&pageSize=1"))
    assert risk_suggestions["pageSize"] == 1
    assert risk_suggestions["total"] == 1

    llm_result = {
        "impactedObject": "PUBLIC_INTEREST",
        "damageDegree": "SERIOUS",
        "reason": "影响电力供应连续性，可能对公共利益造成严重危害。",
        "confidence": 0.82,
    }
    suggestion = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/risk-items/{source_id}/harm-analysis/suggest",
            json={"llmResult": llm_result},
        )
    )
    assert suggestion["harmLevel"] == "RELATIVELY_HIGH"
    assert suggestion["dataSecurityProtectionLevel"] == 3
    assert suggestion["harmImpactObject"] == "社会秩序和公共利益"
    assert suggestion["needsManualReview"] is False
    assert suggestion["harmAnalysisTrace"]["step3"]
    assert suggestion["harmAnalysisTrace"]["step2Reason"] == "影响电力供应连续性，可能对公共利益造成严重危害。"
    assert any("项目系统类别" in item for item in suggestion["harmAnalysisTrace"]["step2Basis"])
    assert any("判定规则" in item for item in suggestion["harmAnalysisTrace"]["step2Basis"])
    assert suggestion["harmAnalysisTrace"]["step2Evidence"] == []

    unchanged = unwrap(client.get(f"/api/v1/projects/{project_id}/risk-items?pageNo=1&pageSize=1"))
    assert unchanged["list"][0]["harmLevel"] == "-"
    assert unchanged["list"][0]["harmAnalysisStatus"] == "PENDING"

    batch = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/risk-items/harm-analysis/suggest-batch",
            json={"riskItemIds": [source_id], "llmResult": llm_result},
        )
    )
    assert batch["total"] == 1
    assert batch["suggestions"][0]["riskRecordId"] == source_id

    applied_harm = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/risk-items/{source_id}/harm-analysis/apply",
            json={"suggestion": suggestion},
        )
    )
    assert applied_harm["harmLevel"] == "RELATIVELY_HIGH"
    assert applied_harm["harmDescription"]
    assert applied_harm["harmExample"]
    assert applied_harm["harmAnalysisTrace"]["dataSecurityProtectionLevel"] == 3
    assert applied_harm["harmAnalysisStatus"] == "CONFIRMED"
    assert applied_harm["riskLevel"] == "MEDIUM"

    original_risk_description = sources["list"][0]["riskDescription"]
    original_source_description = sources["list"][0]["riskSourceDescription"]
    updated_item = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/risk-items/{source_id}",
            json={
                "riskTypes": ["IGNORED"],
                "riskDescription": "ignored risk",
                "riskSourceDescription": "ignored source",
                "relatedData": "ignored data",
                "relatedActivities": ["IGNORED"],
                "harmLevel": "MEDIUM",
                "riskLevel": "MAJOR",
            },
        )
    )
    assert updated_item["riskDescription"] == original_risk_description
    assert updated_item["riskSourceDescription"] == original_source_description
    assert updated_item["relatedData"] == "-"
    assert updated_item["relatedActivities"] == []
    assert updated_item["harmLevel"] == "MEDIUM"
    assert updated_item["riskLevel"] == "LOW"

    updated_suggestion = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/risk-suggestions/{source_id}",
            json={"riskDescription": "ignored", "riskSourceDescription": "ignored", "riskLevel": "HIGH", "remediationSuggestion": "manual suggestion"},
        )
    )
    assert updated_suggestion["riskDescription"] == original_risk_description
    assert updated_suggestion["riskSourceDescription"] == original_source_description
    assert updated_suggestion["riskLevel"] == "LOW"
    assert updated_suggestion["remediationSuggestion"] == "manual suggestion"

    updated = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/risk-sources/{source_id}",
            json={"riskSourceDescription": "manual problem"},
        )
    )
    assert updated["riskSourceDescription"] == "manual problem"


def test_business_system_diagram_upload_updates_file_ids(client):
    project_id = create_project(client, project_code="ENST-TEST-DIAGRAM")
    business_system = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/business-systems",
            json={"systemName": "Customer Platform", "businessFunction": "Customer management"},
        )
    )
    record_id = business_system["id"]

    topology_bytes = b"\x89PNG\r\n\x1a\ntopology"
    topology = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/business-systems/{record_id}/topology-diagram",
            data={"file": (BytesIO(topology_bytes), "topology.png")},
            content_type="multipart/form-data",
        )
    )
    assert topology["file"]["bizType"] == "SURVEY_TOPOLOGY_DIAGRAM"
    assert topology["file"]["storageProvider"] == "LOCAL"
    assert topology["businessSystem"]["topologyFileId"] == topology["file"]["fileId"]

    data_flow_bytes = b"\x89PNG\r\n\x1a\ndata-flow"
    data_flow = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/business-systems/{record_id}/data-flow-diagram",
            data={"file": (BytesIO(data_flow_bytes), "data-flow.png")},
            content_type="multipart/form-data",
        )
    )
    assert data_flow["file"]["bizType"] == "SURVEY_DATA_FLOW_DIAGRAM"
    assert data_flow["businessSystem"]["businessFlowFileId"] == data_flow["file"]["fileId"]

    stored = unwrap(client.get(f"/api/v1/projects/{project_id}/survey/business-systems?pageNo=1&pageSize=10"))
    assert stored["list"][0]["topologyFileId"] == topology["file"]["fileId"]
    assert stored["list"][0]["businessFlowFileId"] == data_flow["file"]["fileId"]

    download = client.get(topology["file"]["downloadUrl"])
    assert download.status_code == 200
    assert download.data == topology_bytes

    invalid = client.post(
        f"/api/v1/projects/{project_id}/survey/business-systems/{record_id}/data-flow-diagram",
        data={"file": (BytesIO(b"not image"), "data-flow.txt")},
        content_type="multipart/form-data",
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["code"] == "INVALID_DIAGRAM_FILE"


def test_harm_analysis_uses_dashscope_gateway_before_fallback(client, monkeypatch):
    from app.services import llm_gateway_service

    project_id = create_project(client, project_code="ENST-TEST-LLM")
    unwrap(client.post(f"/api/v1/projects/{project_id}/start"))
    items = unwrap(client.get(f"/api/v1/projects/{project_id}/evaluation/items?pageNo=1&pageSize=1"))
    item_id = items["list"][0]["id"]
    unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/evaluation/items/{item_id}/record",
            json={"evaluationResult": "PARTIAL", "evaluationRecord": "record"},
        )
    )
    unwrap(client.post(f"/api/v1/projects/{project_id}/risk-summary/refresh", json={}))
    risk_items = unwrap(client.get(f"/api/v1/projects/{project_id}/risk-items?pageNo=1&pageSize=1"))
    risk_item_id = risk_items["list"][0]["id"]

    def fake_suggest_harm_analysis(_project, _record, _system_category, _rule_config):
        assert _record.assessment_category
        assert _record.assessment_subcategory
        assert _record.check_point
        assert _record.evaluation_result == "PARTIAL"
        assert _record.evaluation_record == "record"
        return {
            "impactedObject": "PUBLIC_INTEREST",
            "damageDegree": "SERIOUS",
            "reason": "模型判断该风险会影响公共利益。",
            "confidence": 0.91,
            "needsManualReview": False,
            "llm_status": llm_gateway_service.LLM_SUCCESS,
            "llm_provider": "dashscope",
            "llm_model": "qwen-plus",
        }

    monkeypatch.setattr(llm_gateway_service, "suggest_harm_analysis", fake_suggest_harm_analysis)
    suggestion = unwrap(client.post(f"/api/v1/projects/{project_id}/risk-items/{risk_item_id}/harm-analysis/suggest", json={}))
    assert suggestion["llmStatus"] == "SUCCESS"
    assert suggestion["llmProvider"] == "dashscope"
    assert suggestion["llmModel"] == "qwen-plus"
    assert suggestion["harmLevel"] == "RELATIVELY_HIGH"

    applied = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/risk-items/{risk_item_id}/harm-analysis/apply",
            json={"suggestion": suggestion},
        )
    )
    assert applied["harmLevel"] == "RELATIVELY_HIGH"
    assert applied["riskLevel"] == "MEDIUM"


def test_harm_analysis_prompt_includes_evaluation_context(client, monkeypatch):
    from app.services import llm_gateway_service

    project_id = create_project(client, project_code="ENST-TEST-PROMPT")
    unwrap(client.post(f"/api/v1/projects/{project_id}/start"))
    items = unwrap(client.get(f"/api/v1/projects/{project_id}/evaluation/items?pageNo=1&pageSize=1"))
    item = items["list"][0]
    unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/evaluation/items/{item['id']}/record",
            json={"evaluationResult": "NON_COMPLIANT", "evaluationRecord": "现场记录用于区分严重性"},
        )
    )
    unwrap(client.post(f"/api/v1/projects/{project_id}/risk-summary/refresh", json={}))
    risk_items = unwrap(client.get(f"/api/v1/projects/{project_id}/risk-items?pageNo=1&pageSize=1"))
    risk_item_id = risk_items["list"][0]["id"]
    captured = {}

    def fake_chat_completion(_config, messages):
        import json

        prompt = json.loads(messages[1]["content"])
        captured.update(prompt["input"])
        return json.dumps(
            {
                "impactedObject": "LEGAL_RIGHTS",
                "damageDegree": "SERIOUS",
                "reason": "根据现场测评上下文判断。",
                "confidence": 0.8,
                "needsManualReview": False,
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(llm_gateway_service, "_chat_completion", fake_chat_completion)
    suggestion = unwrap(client.post(f"/api/v1/projects/{project_id}/risk-items/{risk_item_id}/harm-analysis/suggest", json={}))
    assert suggestion["llmStatus"] == "SUCCESS"
    assert captured["assessmentCategory"] == item["category"]
    assert captured["assessmentSubcategory"] == item["subcategory"]
    assert captured["checkPoint"] == item["checkPoint"]
    assert captured["evaluationResult"] == "NON_COMPLIANT"
    assert captured["evaluationRecord"] == "现场记录用于区分严重性"


def test_evaluation_import_export_and_report_management(client):
    project_id = create_project(client)
    unwrap(client.post(f"/api/v1/projects/{project_id}/start"))

    template_response = client.get(f"/api/v1/projects/{project_id}/evaluation/export-template")
    assert template_response.status_code == 200
    assert template_response.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    workbook = load_workbook(BytesIO(template_response.data))
    worksheet = workbook.active
    assert worksheet.cell(row=1, column=7).value == "评估结果"
    assert worksheet.cell(row=1, column=8).value == "符合情况"
    assert worksheet.cell(row=2, column=1).value
    worksheet.cell(row=2, column=7).value = "imported record"
    worksheet.cell(row=2, column=8).value = "PARTIAL"
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)

    imported = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/evaluation/import",
            data={"file": (stream, "evaluation-import.xlsx")},
            content_type="multipart/form-data",
        )
    )
    assert imported["importedCount"] == 1
    assert imported["failedCount"] == 0
    session = SessionLocal()
    records = (
        session.query(EvaluationRecord)
        .filter(EvaluationRecord.project_id == project_id, EvaluationRecord.deleted.is_(False))
        .all()
    )
    assert len(records) == 1
    record_id = records[0].id

    worksheet.cell(row=2, column=7).value = "updated record"
    worksheet.cell(row=2, column=8).value = "NON_COMPLIANT"
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)

    reimported = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/evaluation/import",
            data={"file": (stream, "evaluation-import.xlsx")},
            content_type="multipart/form-data",
        )
    )
    assert reimported["importedCount"] == 1
    records = (
        session.query(EvaluationRecord)
        .filter(EvaluationRecord.project_id == project_id, EvaluationRecord.deleted.is_(False))
        .all()
    )
    assert len(records) == 1
    assert records[0].id == record_id
    assert records[0].evaluation_record == "updated record"
    assert records[0].evaluation_result == "NON_COMPLIANT"

    export_response = client.get(f"/api/v1/projects/{project_id}/evaluation/export")
    assert export_response.status_code == 200
    exported = load_workbook(BytesIO(export_response.data)).active
    assert exported.max_column == 8
    assert exported.cell(row=2, column=7).value == "updated record"
    assert exported.cell(row=2, column=8).value == "NON_COMPLIANT"

    generated = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/reports/generate",
            json={"reportName": "Flow Report", "selectedSections": ["EVALUATION", "RISK_SUMMARY"]},
        )
    )
    assert generated["status"] == "SUCCESS"
    assert generated["reportId"]

    task = unwrap(client.get(f"/api/v1/projects/{project_id}/reports/tasks/{generated['reportTaskId']}"))
    assert task["status"] == "SUCCESS"

    reports = unwrap(client.get(f"/api/v1/projects/{project_id}/reports?pageNo=1&pageSize=10"))
    assert reports["total"] == 1
    assert reports["list"][0]["downloadUrl"]

    download = client.get(f"/api/v1/projects/{project_id}/reports/{generated['reportId']}/download")
    assert download.status_code == 200
    assert download.mimetype == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    with zipfile.ZipFile(BytesIO(download.data)) as report:
        document_xml = report.read("word/document.xml").decode("utf-8")
        assert "Flow Project" in document_xml
        document = ET.fromstring(document_xml)
        paragraph_texts = [
            "".join(node.text or "" for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")).strip()
            for paragraph in document.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p")
        ]
        assert "数据安全基本信息" not in paragraph_texts
        assert "安全风险处理" not in paragraph_texts
        assert not any(name.startswith("word/comments") for name in report.namelist())

    deleted = unwrap(client.delete(f"/api/v1/projects/{project_id}/reports/{generated['reportId']}"))
    assert deleted["reportId"] == generated["reportId"]
    assert unwrap(client.get(f"/api/v1/projects/{project_id}/reports?pageNo=1&pageSize=10"))["total"] == 0


def test_evaluation_import_resolves_blank_item_id_by_context(client):
    project_id = create_project(client)
    unwrap(client.post(f"/api/v1/projects/{project_id}/start"))

    template_response = client.get(f"/api/v1/projects/{project_id}/evaluation/export-template")
    workbook = load_workbook(BytesIO(template_response.data))
    worksheet = workbook.active

    rows_by_code = {}
    duplicate_rows = None
    for row_no in range(2, worksheet.max_row + 1):
        item_code = worksheet.cell(row=row_no, column=2).value
        if item_code in rows_by_code:
            duplicate_rows = (rows_by_code[item_code], row_no)
            break
        rows_by_code[item_code] = row_no
    assert duplicate_rows

    expected_item_ids = {worksheet.cell(row=row_no, column=1).value for row_no in duplicate_rows}
    for row_no in duplicate_rows:
        worksheet.cell(row=row_no, column=1).value = None
        worksheet.cell(row=row_no, column=7).value = f"legacy import row {row_no}"
        worksheet.cell(row=row_no, column=8).value = "PARTIAL"

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    imported = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/evaluation/import",
            data={"file": (stream, "evaluation-import.xlsx")},
            content_type="multipart/form-data",
        )
    )
    assert imported["importedCount"] == 2
    assert imported["failedCount"] == 0

    session = SessionLocal()
    records = (
        session.query(EvaluationRecord)
        .filter(EvaluationRecord.project_id == project_id, EvaluationRecord.deleted.is_(False))
        .all()
    )
    assert {record.item_id for record in records} == expected_item_ids
    assert {record.evaluation_record for record in records} == {f"legacy import row {row_no}" for row_no in duplicate_rows}
