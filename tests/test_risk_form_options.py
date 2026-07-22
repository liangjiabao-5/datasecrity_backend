from app.extensions import SessionLocal
from app.models import ProjectRiskSummaryRecord


COLLECT = "\u6536\u96c6"
TRANSFER = "\u4f20\u8f93"
STORE = "\u5b58\u50a8"
USE_PROCESS = "\u4f7f\u7528\u548c\u52a0\u5de5"
PROVIDE = "\u63d0\u4f9b"
PUBLIC = "\u516c\u5f00"
DELETE = "\u5220\u9664"


def unwrap(response):
    assert response.status_code < 400, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["code"] == "SUCCESS"
    return payload["data"]


def create_project(client, project_code: str) -> str:
    return unwrap(
        client.post(
            "/api/v1/projects",
            json={
                "projectName": "Risk Options Project",
                "projectCode": project_code,
                "assessmentOrg": "Test Org",
                "assessmentTemplateId": "tpl-gb",
                "scoreModelId": "score-v1",
                "harmModelId": "harm-default",
                "riskMatrixId": "matrix-v1",
                "systemType": "MANAGEMENT_SYSTEM",
            },
        )
    )["projectId"]


def create_current_risk_record(client, project_id: str) -> dict:
    unwrap(client.post(f"/api/v1/projects/{project_id}/start"))
    item = unwrap(client.get(f"/api/v1/projects/{project_id}/evaluation/items?pageNo=1&pageSize=1"))["list"][0]
    unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/evaluation/items/{item['id']}/record",
            json={"evaluationResult": "PARTIAL", "evaluationRecord": "record"},
        )
    )
    unwrap(client.post(f"/api/v1/projects/{project_id}/risk-summary/refresh", json={}))
    return unwrap(client.get(f"/api/v1/projects/{project_id}/risk-items?pageNo=1&pageSize=1"))["list"][0]


def test_risk_form_options_aggregate_data_asset_category_and_level(client):
    project_id = create_project(client, "ENST-RISK-OPTIONS")

    personal_contact = "\u4e2a\u4eba\u4fe1\u606f-\u8054\u7cfb\u65b9\u5f0f"
    trade_data = "\u4ea4\u6613\u6570\u636e"
    log_data = "\u65e5\u5fd7\u6570\u636e"
    unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/data-assets",
            json={"dataName": "Customer Data", "dataCategory": personal_contact, "dataLevel": "L2"},
        )
    )
    unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/data-assets",
            json={"dataName": "Duplicate Data", "dataCategory": f"  {personal_contact}  ", "dataLevel": " L2 "},
        )
    )
    unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/data-assets",
            json={"dataName": "Trade Data", "dataCategory": trade_data, "dataLevel": "L3"},
        )
    )
    unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/survey/data-assets",
            json={"dataName": "Incomplete Data", "dataCategory": log_data},
        )
    )

    options = unwrap(client.get(f"/api/v1/projects/{project_id}/risk-form-options"))

    assert options["relatedDataOptions"] == [f"{personal_contact}/L2", f"{trade_data}/L3"]
    assert options["relatedActivityOptions"] == [COLLECT, TRANSFER, STORE, USE_PROCESS, PROVIDE, PUBLIC, DELETE]


def test_risk_source_update_accepts_related_dropdown_fields(client):
    project_id = create_project(client, "ENST-RISK-SOURCE-RELATED")
    risk_record = create_current_risk_record(client, project_id)

    updated = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/risk-sources/{risk_record['id']}",
            json={
                "relatedData": ["customer/L2", "trade/L3"],
                "relatedActivities": [COLLECT, STORE],
            },
        )
    )

    assert updated["relatedData"] == "customer/L2\u3001trade/L3"
    assert updated["relatedActivities"] == [COLLECT, STORE]

    session = SessionLocal()
    stored = session.get(ProjectRiskSummaryRecord, risk_record["id"])
    assert stored.related_data == "customer/L2\u3001trade/L3"
    assert stored.related_activities == [COLLECT, STORE]


def test_risk_item_update_ignores_related_dropdown_fields(client):
    project_id = create_project(client, "ENST-RISK-ITEM-READONLY-RELATED")
    risk_record = create_current_risk_record(client, project_id)

    updated = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/risk-items/{risk_record['id']}",
            json={
                "relatedData": ["customer/L2", "trade/L3"],
                "relatedActivities": [COLLECT, STORE],
            },
        )
    )

    assert updated["relatedData"] == "-"
    assert updated["relatedActivities"] == []

    session = SessionLocal()
    stored = session.get(ProjectRiskSummaryRecord, risk_record["id"])
    assert stored.related_data == "-"
    assert stored.related_activities == []
