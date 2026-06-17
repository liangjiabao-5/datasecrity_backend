from io import BytesIO
from xml.etree import ElementTree as ET
import zipfile

from openpyxl import Workbook, load_workbook

from app.extensions import SessionLocal
from app.models import (
    AssessedOrganization,
    AssessmentTeamMember,
    BusinessSystem,
    ClientTeamMember,
    DataProcessorBasicSurvey,
    EvaluationRecord,
    FocusPoint,
    GapItem,
    ProcessingActivitySurvey,
    ProjectBasicInfo,
    SecurityProtectionSurvey,
)


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


def workbook_stream(workbook):
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


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
                "projectNumber": "SHOULD-NOT-CHANGE",
                "projectName": "新版项目名称",
                "laws": [{"id": "law-1", "name": "law"}],
                "standards": [{"id": "std-1", "name": "std"}],
                "assessmentPlan": {"startDate": "2026-06-01", "endDate": "2026-06-30"},
                "organization": {"name": "org", "postalCode": "100000"},
                "contacts": [{"name": "alice", "email": "alice@example.com"}],
            },
        )
    )
    assert basic["projectNumber"] == "ENST-TEST-001"
    assert basic["projectName"] == "新版项目名称"
    assert basic["organization"]["name"] == "org"
    assert basic["organization"]["postalCode"] == "100000"
    assert "projectDescription" not in basic
    assert "systemDescription" not in basic
    assert "assessmentTarget" not in basic
    assert "creditCode" not in basic["organization"]
    assert "project_description" not in ProjectBasicInfo.__table__.columns
    assert "system_description" not in ProjectBasicInfo.__table__.columns
    assert "assessment_target" not in ProjectBasicInfo.__table__.columns
    assert "address" not in AssessedOrganization.__table__.columns
    assert "credit_code" not in AssessedOrganization.__table__.columns
    assert "data_security_owner" not in AssessedOrganization.__table__.columns
    assert "description" not in AssessedOrganization.__table__.columns

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

    processor_basic = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/survey/data-processor-basic",
            json={
                "unitName": "数据处理者",
                "unitNature": "企业",
                "unifiedSocialCreditCode": "91330000000000000X",
                "businessScope": "电力业务",
                "isIgnoredByBackend": "ignored",
            },
        )
    )
    assert processor_basic["unitName"] == "数据处理者"
    assert processor_basic["unitNature"] == "企业"
    assert "isIgnoredByBackend" not in processor_basic
    assert unwrap(client.get(f"/api/v1/projects/{project_id}/survey/data-processor-basic"))["businessScope"] == "电力业务"
    session = SessionLocal()
    processor_basic_row = session.query(DataProcessorBasicSurvey).filter_by(project_id=project_id).one()
    assert "payload" not in DataProcessorBasicSurvey.__table__.columns
    assert processor_basic_row.unit_name == "数据处理者"
    assert processor_basic_row.business_scope == "电力业务"

    business_system = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/business-systems",
            json={
                "systemName": "核心业务系统",
                "businessFunction": "业务办理",
                "dataScopes": ["一般数据", "个人信息", "重要数据"],
            },
        )
    )
    assert business_system["dataScopes"] == ["一般数据", "个人信息", "重要数据"]
    business_system_row = session.query(BusinessSystem).filter_by(id=business_system["id"]).one()
    assert business_system_row.data_scopes == "一般数据、个人信息、重要数据"

    activity_survey_default = unwrap(client.get(f"/api/v1/projects/{project_id}/survey/processing-activity-survey"))
    assert activity_survey_default["involvedActivities"] == []
    assert activity_survey_default["collectionChannels"] == ""
    activity_survey = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/survey/processing-activity-survey",
            json={
                "involvedActivities": ["COLLECT", "TRANSFER"],
                "collectionChannels": "线上渠道",
                "collectionMethod": "接口采集",
                "transferProtocol": "HTTPS",
                "storageMethod": "不应保存",
                "collect": {"scenarios": ["旧字段不应返回"]},
            },
        )
    )
    assert activity_survey["involvedActivities"] == ["COLLECT", "TRANSFER"]
    assert "activityTypes" not in activity_survey
    assert "collect" not in activity_survey
    assert activity_survey["collectionChannels"] == "线上渠道"
    assert activity_survey["collectionMethod"] == "接口采集"
    assert activity_survey["transferProtocol"] == "HTTPS"
    assert activity_survey["storageMethod"] == "不应保存"
    activity_survey_row = session.query(ProcessingActivitySurvey).filter_by(project_id=project_id).one()
    assert "payload" not in ProcessingActivitySurvey.__table__.columns
    assert activity_survey_row.involved_activities == "COLLECT,TRANSFER"
    assert activity_survey_row.collection_channels == "线上渠道"
    assert activity_survey_row.collection_method == "接口采集"
    assert activity_survey_row.transfer_protocol == "HTTPS"
    assert activity_survey_row.storage_method == "不应保存"

    protection = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/survey/security-protection",
            json={
                "complianceAssessmentStatus": "已完成等保测评",
                "identityAuthenticationAndAccessControl": "密码和多因素认证",
                "isPowerMonitoringSystem": "否",
                "productionControlAreaProtection": "旧电力监控字段可独立保存",
                "classifiedProtectionDone": "NO",
            },
        )
    )
    assert protection["complianceAssessmentStatus"] == "已完成等保测评"
    assert protection["identityAuthenticationAndAccessControl"] == "密码和多因素认证"
    assert protection["isPowerMonitoringSystem"] == "NO"
    assert protection["productionControlAreaProtection"] == "旧电力监控字段可独立保存"
    assert "classifiedProtectionDone" not in protection
    protection_row = session.query(SecurityProtectionSurvey).filter_by(project_id=project_id).one()
    assert "payload" not in SecurityProtectionSurvey.__table__.columns
    assert protection_row.compliance_assessment_status == "已完成等保测评"
    assert protection_row.identity_authentication_and_access_control == "密码和多因素认证"
    assert protection_row.is_power_monitoring_system == "NO"

    protection_yes = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/survey/security-protection",
            json={
                "isPowerMonitoringSystem": "YES",
                "powerDispatchAuthentication": "已部署调度数字证书",
            },
        )
    )
    assert protection_yes["isPowerMonitoringSystem"] == "YES"
    assert protection_yes["powerDispatchAuthentication"] == "已部署调度数字证书"

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
    assert suggestion["harmAnalysisTrace"]["step4"] == "按保护等级匹配风险危害程度等级：较高。"
    assert suggestion["harmAnalysisTrace"]["step2Reason"] == "影响电力供应连续性，可能对公共利益造成严重危害。"
    assert "符合情况：基本符合" in suggestion["harmAnalysisTrace"]["step2Basis"]
    assert "符合情况：PARTIAL" not in suggestion["harmAnalysisTrace"]["step2Basis"]
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


def test_basic_info_excel_template_import_and_export(client):
    project_id = create_project(client, project_code="ENST-TEST-BASIC-EXCEL")

    template_response = client.get(f"/api/v1/projects/{project_id}/basic-info/export-template")
    assert template_response.status_code == 200
    assert template_response.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    workbook = load_workbook(BytesIO(template_response.data))
    assert workbook.sheetnames == ["项目基本情况", "被评估单位基本信息", "联系人信息"]
    assert [cell.value for cell in workbook["项目基本情况"][1]] == [
        "项目编号",
        "项目名称",
        "评估所依据的法律法规",
        "评估所参考的标准规范",
        "评估开始日期",
        "评估结束日期",
    ]
    assert [cell.value for cell in workbook["被评估单位基本信息"][1]] == [
        "单位名称",
        "邮政编码",
    ]
    assert [cell.value for cell in workbook["联系人信息"][1]] == [
        "姓名",
        "所属部门",
        "移动电话",
        "职务/职称",
        "办公电话",
        "电子邮件",
    ]

    workbook["项目基本情况"].append([
        "ENST-TEST-BASIC-EXCEL",
        "导入后的项目名称",
        "网络安全法、数据安全法",
        "GB/T 35273; GB/T 22239",
        "2026-06-01",
        "2026-06-10",
    ])
    workbook["被评估单位基本信息"].append([
        "被评估单位",
        "310000",
    ])
    workbook["联系人信息"].append([
        "张三",
        "信息部",
        "13800000000",
        "经理",
        "0571-88888888",
        "zhangsan@example.com",
    ])

    imported = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/basic-info/import",
            data={"file": (workbook_stream(workbook), "basic-info.xlsx")},
            content_type="multipart/form-data",
        )
    )
    assert imported["projectNumber"] == "ENST-TEST-BASIC-EXCEL"
    assert imported["projectName"] == "导入后的项目名称"
    assert imported["laws"] == [{"id": "网络安全法", "name": "网络安全法"}, {"id": "数据安全法", "name": "数据安全法"}]
    assert imported["standards"] == [{"id": "GB/T 35273", "name": "GB/T 35273"}, {"id": "GB/T 22239", "name": "GB/T 22239"}]
    assert imported["assessmentPlan"] == {"startDate": "2026-06-01", "endDate": "2026-06-10"}
    assert imported["organization"]["postalCode"] == "310000"
    assert "assessmentTarget" not in imported
    assert "creditCode" not in imported["organization"]
    assert imported["contacts"][0]["email"] == "zhangsan@example.com"

    not_persisted = unwrap(client.get(f"/api/v1/projects/{project_id}/basic-info"))
    assert not_persisted["projectName"] == "Flow Project"
    assert not_persisted["contacts"] == []

    saved = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/basic-info",
            json=imported,
        )
    )
    assert saved["projectName"] == "导入后的项目名称"

    export_response = client.get(f"/api/v1/projects/{project_id}/basic-info/export")
    assert export_response.status_code == 200
    exported = load_workbook(BytesIO(export_response.data))
    assert exported.sheetnames == ["项目基本情况", "被评估单位基本信息", "联系人信息"]
    assert exported["项目基本情况"].cell(row=2, column=1).value == "ENST-TEST-BASIC-EXCEL"
    assert exported["项目基本情况"].cell(row=2, column=2).value == "导入后的项目名称"
    assert exported["项目基本情况"].cell(row=2, column=3).value == "网络安全法、数据安全法"
    assert exported["被评估单位基本信息"].cell(row=2, column=1).value == "被评估单位"
    assert exported["联系人信息"].cell(row=2, column=1).value == "张三"


def test_plan_team_excel_import_export_and_preserves_focus_gap_items(client):
    project_id = create_project(client, project_code="ENST-TEST-PLAN-TEAM-EXCEL")
    unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/plan/assessment-team",
            json={"name": "旧成员", "organization": "旧单位", "role": "旧角色"},
        )
    )
    focus = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/plan/focus-points",
            json={"name": "重点关注", "domain": "制度", "riskLevel": "HIGH"},
        )
    )
    gap = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/plan/gap-items",
            json={"gapItem": "差距项", "dimension": "管理", "currentYearScore": 80},
        )
    )

    template_response = client.get(f"/api/v1/projects/{project_id}/plan/team-export-template")
    assert template_response.status_code == 200
    workbook = load_workbook(BytesIO(template_response.data))
    assert workbook.sheetnames == ["评估团队", "被评估方团队"]
    assert [cell.value for cell in workbook["评估团队"][1]] == ["姓名", "单位", "角色"]
    assert [cell.value for cell in workbook["被评估方团队"][1]] == ["公司/部门", "姓名", "职位", "联系方式"]

    workbook["评估团队"].append(["李四", "评估机构", "组长"])
    workbook["被评估方团队"].append(["信息部", "王五", "经理", "13900000000"])
    imported = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/plan/team-import",
            data={"file": (workbook_stream(workbook), "plan-team.xlsx")},
            content_type="multipart/form-data",
        )
    )
    assert imported["assessmentTeam"] == [
        {"id": imported["assessmentTeam"][0]["id"], "name": "李四", "organization": "评估机构", "role": "组长"}
    ]
    assert imported["clientTeam"] == [
        {"id": imported["clientTeam"][0]["id"], "department": "信息部", "name": "王五", "position": "经理", "contact": "13900000000"}
    ]

    session = SessionLocal()
    assessment_rows = session.query(AssessmentTeamMember).filter_by(project_id=project_id, deleted=False).all()
    client_rows = session.query(ClientTeamMember).filter_by(project_id=project_id, deleted=False).all()
    focus_rows = session.query(FocusPoint).filter_by(project_id=project_id, deleted=False).all()
    gap_rows = session.query(GapItem).filter_by(project_id=project_id, deleted=False).all()
    assert [row.name for row in assessment_rows] == ["李四"]
    assert [row.name for row in client_rows] == ["王五"]
    assert [row.id for row in focus_rows] == [focus["id"]]
    assert [row.id for row in gap_rows] == [gap["id"]]

    export_response = client.get(f"/api/v1/projects/{project_id}/plan/team-export")
    assert export_response.status_code == 200
    exported = load_workbook(BytesIO(export_response.data))
    assert exported.sheetnames == ["评估团队", "被评估方团队"]
    assert exported["评估团队"].cell(row=2, column=1).value == "李四"
    assert exported["被评估方团队"].cell(row=2, column=2).value == "王五"


def test_plan_team_import_reports_header_validation_errors(client):
    project_id = create_project(client, project_code="ENST-TEST-PLAN-TEAM-ERROR")
    workbook = Workbook()
    workbook.active.title = "评估团队"
    workbook["评估团队"].append(["姓名", "单位", "错误列"])
    client_sheet = workbook.create_sheet("被评估方团队")
    client_sheet.append(["公司/部门", "姓名", "职位", "联系方式"])

    response = client.post(
        f"/api/v1/projects/{project_id}/plan/team-import",
        data={"file": (workbook_stream(workbook), "plan-team.xlsx")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["code"] == "IMPORT_VALIDATION_FAILED"
    assert payload["message"] == "导入文件存在格式错误"
    assert payload["data"] == {
        "errors": [
            {
                "sheetName": "评估团队",
                "rowNo": 1,
                "field": "角色",
                "reason": "表头名称与模板不一致，请使用最新模板。",
            }
        ]
    }


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

    vsdx_bytes = b"PK\x03\x04vsdx"
    vsdx = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/business-systems/{record_id}/topology-diagram",
            data={"file": (BytesIO(vsdx_bytes), "topology.vsdx")},
            content_type="multipart/form-data",
        )
    )
    assert vsdx["file"]["fileName"] == "topology.vsdx"
    assert vsdx["businessSystem"]["topologyFileId"] == vsdx["file"]["fileId"]

    stored = unwrap(client.get(f"/api/v1/projects/{project_id}/survey/business-systems?pageNo=1&pageSize=10"))
    assert stored["list"][0]["topologyFileId"] == vsdx["file"]["fileId"]
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
    unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/business-systems",
            json={"systemName": "Flow Project", "businessFunction": "Flow report business"},
        )
    )

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


def test_evaluation_import_reports_invalid_result_and_preserves_records(client):
    project_id = create_project(client)
    unwrap(client.post(f"/api/v1/projects/{project_id}/start"))

    template_response = client.get(f"/api/v1/projects/{project_id}/evaluation/export-template")
    workbook = load_workbook(BytesIO(template_response.data))
    worksheet = workbook.active
    worksheet.cell(row=2, column=7).value = "original record"
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

    worksheet.cell(row=2, column=7).value = "should not overwrite"
    worksheet.cell(row=2, column=8).value = "错误符合情况"
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    failed = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/evaluation/import",
            data={"file": (stream, "evaluation-import.xlsx")},
            content_type="multipart/form-data",
        )
    )

    assert failed["importedCount"] == 0
    assert failed["failedCount"] == 1
    assert failed["errors"] == [
        {
            "rowNo": 2,
            "field": "符合情况",
            "reason": "符合情况列只能填写符合、基本符合、不符合、不适用或对应枚举值 COMPLIANT、PARTIAL、NON_COMPLIANT、NOT_APPLICABLE。",
        }
    ]
    session = SessionLocal()
    record = (
        session.query(EvaluationRecord)
        .filter(EvaluationRecord.project_id == project_id, EvaluationRecord.deleted.is_(False))
        .one()
    )
    assert record.evaluation_record == "original record"
    assert record.evaluation_result == "PARTIAL"


def test_evaluation_import_reports_readonly_column_changes_and_preserves_records(client):
    project_id = create_project(client, project_code="ENST-TEST-IMPORT-READONLY")
    unwrap(client.post(f"/api/v1/projects/{project_id}/start"))

    template_response = client.get(f"/api/v1/projects/{project_id}/evaluation/export-template")
    workbook = load_workbook(BytesIO(template_response.data))
    worksheet = workbook.active
    worksheet.cell(row=2, column=7).value = "original record"
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

    worksheet.cell(row=2, column=2).value = "edited item code"
    worksheet.cell(row=2, column=3).value = "edited sheet"
    worksheet.cell(row=2, column=4).value = "edited category"
    worksheet.cell(row=2, column=5).value = "edited subcategory"
    worksheet.cell(row=2, column=6).value = "edited check point"
    worksheet.cell(row=2, column=7).value = "should not overwrite"
    worksheet.cell(row=2, column=8).value = "NON_COMPLIANT"
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    failed = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/evaluation/import",
            data={"file": (stream, "evaluation-import.xlsx")},
            content_type="multipart/form-data",
        )
    )

    assert failed["importedCount"] == 0
    assert failed["failedCount"] == 5
    assert failed["errors"] == [
        {"rowNo": 2, "field": "检查项编号", "reason": "检查项编号与系统检查项不一致，请使用最新导出模板。"},
        {"rowNo": 2, "field": "工作表", "reason": "工作表与系统检查项不一致，请使用最新导出模板。"},
        {"rowNo": 2, "field": "一级分类", "reason": "一级分类与系统检查项不一致，请使用最新导出模板。"},
        {"rowNo": 2, "field": "二级分类", "reason": "二级分类与系统检查项不一致，请使用最新导出模板。"},
        {"rowNo": 2, "field": "检查要点", "reason": "检查要点与系统检查项不一致，请使用最新导出模板。"},
    ]
    session = SessionLocal()
    record = (
        session.query(EvaluationRecord)
        .filter(EvaluationRecord.project_id == project_id, EvaluationRecord.deleted.is_(False))
        .one()
    )
    assert record.evaluation_record == "original record"
    assert record.evaluation_result == "PARTIAL"
