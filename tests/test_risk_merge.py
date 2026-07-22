from app.extensions import SessionLocal
from app.models import ProjectAssessmentItem, ProjectRiskSummaryRecord


COLLECT = "\u6536\u96c6"
STORE = "\u5b58\u50a8"
RELATED_DATA = "\u4e2a\u4eba\u4fe1\u606f/L2"
RISK_TYPE = "\u6570\u636e\u6cc4\u9732\u98ce\u9669"
RISK_SOURCE_TYPE = "\u5236\u5ea6\u6d41\u7a0b\u7f3a\u9677"


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
                "projectName": "Risk Merge Project",
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


def prepare_three_risk_records(client, project_id: str) -> list[ProjectRiskSummaryRecord]:
    unwrap(client.post(f"/api/v1/projects/{project_id}/start"))
    items = unwrap(client.get(f"/api/v1/projects/{project_id}/evaluation/items?pageNo=1&pageSize=3"))["list"]
    for index, item in enumerate(items, start=1):
        unwrap(
            client.put(
                f"/api/v1/projects/{project_id}/evaluation/items/{item['id']}/record",
                json={
                    "evaluationResult": "PARTIAL",
                    "evaluationRecord": f"evaluation record {index}",
                },
            )
        )
    unwrap(client.post(f"/api/v1/projects/{project_id}/risk-summary/refresh", json={}))

    session = SessionLocal()
    records = (
        session.query(ProjectRiskSummaryRecord)
        .filter(
            ProjectRiskSummaryRecord.project_id == project_id,
            ProjectRiskSummaryRecord.deleted.is_(False),
            ProjectRiskSummaryRecord.current.is_(True),
        )
        .order_by(ProjectRiskSummaryRecord.created_at.asc())
        .all()
    )
    assert len(records) == 3
    for index, record in enumerate(records, start=1):
        record.assessment_category = "same category"
        record.assessment_subcategory = "same subcategory"
        record.risk_types = [RISK_TYPE]
        record.risk_source_type = RISK_SOURCE_TYPE
        record.related_data = RELATED_DATA
        record.related_activities = [COLLECT, STORE]
        record.risk_description = f"risk description {index}"
        record.assessment_item_id = f"AQ-{index:03d}"
        record.evaluation_record = f"evaluation record {index}"
        record.remediation_suggestion = f"suggestion {index}"
    records[2].related_data = "\u91cd\u8981\u6570\u636e/L3"
    session.commit()
    return records


def current_risk_records(project_id: str) -> list[ProjectRiskSummaryRecord]:
    SessionLocal.remove()
    session = SessionLocal()
    return (
        session.query(ProjectRiskSummaryRecord)
        .filter(
            ProjectRiskSummaryRecord.project_id == project_id,
            ProjectRiskSummaryRecord.deleted.is_(False),
            ProjectRiskSummaryRecord.current.is_(True),
        )
        .order_by(ProjectRiskSummaryRecord.created_at.asc())
        .all()
    )


def merged_row(client, project_id: str, path: str = "risk-items") -> dict:
    rows = unwrap(client.get(f"/api/v1/projects/{project_id}/{path}?pageNo=1&pageSize=10"))["list"]
    return next(row for row in rows if row.get("merged"))


def test_merge_switch_projects_risk_items_and_suggestions_without_changing_sources(client):
    project_id = create_project(client, "ENST-RISK-MERGE")
    prepare_three_risk_records(client, project_id)

    unmerged_items = unwrap(client.get(f"/api/v1/projects/{project_id}/risk-items?pageNo=1&pageSize=10"))
    unmerged_suggestions = unwrap(client.get(f"/api/v1/projects/{project_id}/risk-suggestions?pageNo=1&pageSize=10"))
    assert unmerged_items["total"] == 3
    assert unmerged_suggestions["total"] == 3

    merge_state = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/risk-summary/merge",
            json={"mergeEnabled": True},
        )
    )
    assert merge_state == {"mergeEnabled": True}

    sources = unwrap(client.get(f"/api/v1/projects/{project_id}/risk-sources?pageNo=1&pageSize=10"))
    merged_items = unwrap(client.get(f"/api/v1/projects/{project_id}/risk-items?pageNo=1&pageSize=10"))
    merged_suggestions = unwrap(client.get(f"/api/v1/projects/{project_id}/risk-suggestions?pageNo=1&pageSize=10"))

    assert sources["total"] == 3
    assert merged_items["total"] == 2
    assert merged_suggestions["total"] == 2
    merged_row = merged_items["list"][0]
    assert merged_row["assessmentItemId"] == "AQ-001\u3001AQ-002"
    assert merged_row["riskDescription"] == "risk description 1\nrisk description 2"
    assert merged_row["evaluationRecord"] == "evaluation record 1\nevaluation record 2"
    assert merged_row["evaluationResult"] == "NON_COMPLIANT"
    assert merged_row["relatedData"] == RELATED_DATA
    assert merged_row["relatedActivities"] == [COLLECT, STORE]
    assert merged_suggestions["list"][0]["assessmentItemId"] == "AQ-001\u3001AQ-002"

    disabled = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/risk-summary/merge",
            json={"mergeEnabled": False},
        )
    )
    assert disabled == {"mergeEnabled": False}
    assert unwrap(client.get(f"/api/v1/projects/{project_id}/risk-items?pageNo=1&pageSize=10"))["total"] == 3


def test_merged_risk_item_update_syncs_review_fields_to_source_records(client):
    project_id = create_project(client, "ENST-RISK-MERGE-ITEM-SYNC")
    records = prepare_three_risk_records(client, project_id)
    source_ids = [records[0].id, records[1].id]

    unwrap(client.put(f"/api/v1/projects/{project_id}/risk-summary/merge", json={"mergeEnabled": True}))
    row = merged_row(client, project_id)
    assert row["mergedRiskRecordIds"] == source_ids

    updated = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/risk-items/{row['riskItemId']}",
            json={
                "harmLevel": "MEDIUM",
                "possibilityLevel": "LOW",
                "riskLevel": "MAJOR",
                "mergedRiskRecordIds": source_ids,
            },
        )
    )

    assert updated["riskLevel"] == "SLIGHT"
    refreshed = {record.id: record for record in current_risk_records(project_id)}
    for source_id in source_ids:
        assert refreshed[source_id].harm_level == "MEDIUM"
        assert refreshed[source_id].possibility_level == "LOW"
        assert refreshed[source_id].risk_level == "SLIGHT"
        assert refreshed[source_id].manual_adjusted is True

    row_after_update = merged_row(client, project_id)
    assert row_after_update["riskLevel"] == "SLIGHT"
    assert not {"harmLevel", "possibilityLevel", "riskLevel"} & set(row_after_update["mergeConflictFields"])


def test_merged_risk_suggestion_update_syncs_remediation_to_source_records(client):
    project_id = create_project(client, "ENST-RISK-MERGE-SUGGESTION-SYNC")
    records = prepare_three_risk_records(client, project_id)
    source_ids = [records[0].id, records[1].id]

    unwrap(client.put(f"/api/v1/projects/{project_id}/risk-summary/merge", json={"mergeEnabled": True}))
    row = merged_row(client, project_id, "risk-suggestions")

    updated = unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/risk-suggestions/{row['suggestionId']}",
            json={
                "remediationSuggestion": "merged remediation suggestion",
                "mergedRiskRecordIds": source_ids,
            },
        )
    )

    assert updated["remediationSuggestion"] == "merged remediation suggestion"
    refreshed = {record.id: record for record in current_risk_records(project_id)}
    for source_id in source_ids:
        assert refreshed[source_id].remediation_suggestion == "merged remediation suggestion"
        assert refreshed[source_id].manual_adjusted is True

    row_after_update = merged_row(client, project_id, "risk-suggestions")
    assert row_after_update["remediationSuggestion"] == "merged remediation suggestion"
    assert row_after_update["hasMergeConflict"] is False
    assert row_after_update["mergeConflictFields"] == []


def test_merged_harm_analysis_apply_syncs_confirmation_to_source_records(client):
    project_id = create_project(client, "ENST-RISK-MERGE-HARM-SYNC")
    records = prepare_three_risk_records(client, project_id)
    source_ids = [records[0].id, records[1].id]
    refreshed = current_risk_records(project_id)
    refreshed[0].possibility_level = "HIGH"
    refreshed[1].possibility_level = "LOW"
    SessionLocal().commit()

    unwrap(client.put(f"/api/v1/projects/{project_id}/risk-summary/merge", json={"mergeEnabled": True}))
    row = merged_row(client, project_id)

    suggestion = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/risk-items/{row['riskItemId']}/harm-analysis/suggest",
            json={
                "llmResult": {
                    "impactedObject": "PUBLIC_INTEREST",
                    "damageDegree": "SERIOUS",
                    "reason": "merged row harm reason",
                    "confidence": 0.82,
                }
            },
        )
    )

    applied = unwrap(
        client.post(
            f"/api/v1/projects/{project_id}/risk-items/{row['riskItemId']}/harm-analysis/apply",
            json={"suggestion": suggestion, "mergedRiskRecordIds": source_ids},
        )
    )

    assert applied["harmLevel"] == "RELATIVELY_HIGH"
    refreshed = {record.id: record for record in current_risk_records(project_id)}
    assert refreshed[source_ids[0]].risk_level == "MEDIUM"
    assert refreshed[source_ids[1]].risk_level == "LOW"
    for source_id in source_ids:
        record = refreshed[source_id]
        assert record.harm_level == "RELATIVELY_HIGH"
        assert record.harm_description == suggestion["harmDescription"]
        assert record.harm_impact_object == suggestion["harmImpactObject"]
        assert record.harm_example == suggestion["harmExample"]
        assert record.harm_analysis_trace
        assert record.harm_analysis_confidence == 0.82
        assert record.harm_analysis_input_hash == suggestion["harmAnalysisInputHash"]
        assert record.manual_adjusted is True


def test_merged_rows_report_conflicts_when_review_fields_are_inconsistent(client):
    project_id = create_project(client, "ENST-RISK-MERGE-CONFLICT")
    prepare_three_risk_records(client, project_id)
    session = SessionLocal()
    records = (
        session.query(ProjectRiskSummaryRecord)
        .filter(
            ProjectRiskSummaryRecord.project_id == project_id,
            ProjectRiskSummaryRecord.deleted.is_(False),
            ProjectRiskSummaryRecord.current.is_(True),
        )
        .order_by(ProjectRiskSummaryRecord.created_at.asc())
        .all()
    )
    records[0].harm_level = "HIGH"
    records[1].harm_level = "LOW"
    records[0].possibility_level = "HIGH"
    records[1].possibility_level = "HIGH"
    records[0].risk_level = "MAJOR"
    records[1].risk_level = "LOW"
    records[0].remediation_suggestion = "same suggestion"
    records[1].remediation_suggestion = "different suggestion"
    session.commit()

    unwrap(client.put(f"/api/v1/projects/{project_id}/risk-summary/merge", json={"mergeEnabled": True}))

    merged_item = unwrap(client.get(f"/api/v1/projects/{project_id}/risk-items?pageNo=1&pageSize=10"))["list"][0]
    merged_suggestion = unwrap(client.get(f"/api/v1/projects/{project_id}/risk-suggestions?pageNo=1&pageSize=10"))["list"][0]

    assert merged_item["hasMergeConflict"] is True
    assert merged_item["mergeConflictFields"] == ["harmLevel", "riskLevel", "remediationSuggestion"]
    assert merged_item["mergeConflictMessage"] == "\u5408\u5e76\u884c\u4e2d\u5b58\u5728\u4e0d\u4e00\u81f4\u7684\u8bc4\u5ba1\u7ed3\u679c\uff0c\u8bf7\u8bc4\u4f30\u4eba\u91cd\u65b0\u786e\u8ba4\u3002"
    assert merged_suggestion["hasMergeConflict"] is True
    assert merged_suggestion["mergeConflictFields"] == ["harmLevel", "riskLevel", "remediationSuggestion"]


def test_update_merge_summary_returns_reinput_message_without_conflict_tabs(client):
    project_id = create_project(client, "ENST-RISK-MERGE-UPDATE")
    prepare_three_risk_records(client, project_id)
    session = SessionLocal()
    records = (
        session.query(ProjectRiskSummaryRecord)
        .filter(
            ProjectRiskSummaryRecord.project_id == project_id,
            ProjectRiskSummaryRecord.deleted.is_(False),
            ProjectRiskSummaryRecord.current.is_(True),
        )
        .order_by(ProjectRiskSummaryRecord.created_at.asc())
        .all()
    )
    records[0].harm_level = "HIGH"
    records[1].harm_level = "LOW"
    records[0].possibility_level = "HIGH"
    records[1].possibility_level = "MEDIUM"
    records[0].risk_level = "MAJOR"
    records[1].risk_level = "LOW"
    records[0].remediation_suggestion = "same suggestion"
    records[1].remediation_suggestion = "different suggestion"
    session.commit()

    unwrap(client.put(f"/api/v1/projects/{project_id}/risk-summary/merge", json={"mergeEnabled": True}))
    response = client.post(f"/api/v1/projects/{project_id}/risk-summary/merge/update")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["code"] == "SUCCESS"
    assert payload["message"] == (
        "\u66f4\u65b0\u5408\u5e76\u6570\u636e\u6210\u529f\uff0c\u8bf7\u91cd\u65b0\u586b\u5199"
        "\u6570\u636e\u5b89\u5168\u98ce\u9669\u6e05\u5355\u9875\u3001"
        "\u6570\u636e\u5b89\u5168\u98ce\u9669\u5904\u7f6e\u5efa\u8bae\u9875"
        "\u6240\u5f71\u54cd\u7684\u6570\u636e"
    )
    assert payload["data"]["hasMergeConflict"] is False
    assert payload["data"]["mergeConflictTabs"] == []


def test_related_data_change_clears_old_and_new_merge_group_review_fields(client):
    project_id = create_project(client, "ENST-RISK-MERGE-RELATED-RESET")
    records = prepare_three_risk_records(client, project_id)
    old_group_id = records[1].id
    changed_id = records[0].id
    new_group_id = records[2].id

    for index, record in enumerate(current_risk_records(project_id), start=1):
        record.harm_level = "HIGH"
        record.harm_description = f"harm description {index}"
        record.harm_impact_object = f"impact object {index}"
        record.harm_example = f"harm example {index}"
        record.harm_analysis_trace = {"step1": f"trace {index}"}
        record.harm_analysis_confidence = 0.8
        record.harm_analysis_input_hash = f"hash-{index}"
        record.possibility_level = "HIGH"
        record.risk_level = "MAJOR"
        record.remediation_suggestion = f"remediation {index}"
    SessionLocal().commit()

    unwrap(client.put(f"/api/v1/projects/{project_id}/risk-summary/merge", json={"mergeEnabled": True}))
    unwrap(
        client.put(
            f"/api/v1/projects/{project_id}/risk-sources/{changed_id}",
            json={"relatedData": "\u91cd\u8981\u6570\u636e/L3"},
        )
    )

    refreshed = {record.id: record for record in current_risk_records(project_id)}
    assert refreshed[changed_id].related_data == "\u91cd\u8981\u6570\u636e/L3"
    for record_id in [changed_id, old_group_id, new_group_id]:
        record = refreshed[record_id]
        assert record.harm_level is None
        assert record.harm_description is None
        assert record.harm_impact_object is None
        assert record.harm_example is None
        assert record.harm_analysis_trace is None
        assert record.harm_analysis_confidence is None
        assert record.harm_analysis_input_hash is None
        assert record.possibility_level is None
        assert record.risk_level is None
        assert record.remediation_suggestion is None


def test_merge_switch_reports_rows_with_incomplete_related_fields(client):
    project_id = create_project(client, "ENST-RISK-MERGE-MISSING")
    prepare_three_risk_records(client, project_id)
    session = SessionLocal()
    records = (
        session.query(ProjectRiskSummaryRecord)
        .filter(
            ProjectRiskSummaryRecord.project_id == project_id,
            ProjectRiskSummaryRecord.deleted.is_(False),
            ProjectRiskSummaryRecord.current.is_(True),
        )
        .order_by(ProjectRiskSummaryRecord.created_at.asc())
        .all()
    )
    records[0].related_data = "-"
    records[1].related_activities = []
    session.commit()

    response = client.put(
        f"/api/v1/projects/{project_id}/risk-summary/merge",
        json={"mergeEnabled": True},
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["code"] == "RISK_MERGE_REQUIRED_FIELDS_MISSING"
    assert payload["message"] == "\u7b2c1\u30012\u884c\u9700\u586b\u5199\u5b8c\u6574\u540e\u518d\u8fdb\u884c\u5408\u5e76"
    assert payload["data"] == {"rowNos": "1\u30012"}
    assert unwrap(client.get(f"/api/v1/projects/{project_id}/risk-summary/merge")) == {"mergeEnabled": False}


def test_risk_lists_follow_evaluation_item_order_before_and_after_merge(client):
    project_id = create_project(client, "ENST-RISK-ASSESSMENT-ORDER")
    records = prepare_three_risk_records(client, project_id)
    session = SessionLocal()
    items_by_id = {
        item.id: item
        for item in session.query(ProjectAssessmentItem)
        .filter(ProjectAssessmentItem.id.in_([record.evaluation_item_id for record in records]))
        .all()
    }

    # Deliberately make assessment order differ from risk-record creation order.
    items_by_id[records[0].evaluation_item_id].sort_order = 30
    items_by_id[records[1].evaluation_item_id].sort_order = 10
    items_by_id[records[2].evaluation_item_id].sort_order = 20
    session.commit()
    SessionLocal.remove()

    expected_ids = [records[1].id, records[2].id, records[0].id]
    for path in ["risk-sources", "risk-items", "risk-suggestions"]:
        rows = unwrap(client.get(f"/api/v1/projects/{project_id}/{path}?pageNo=1&pageSize=10"))["list"]
        assert [row["riskRecordId"] for row in rows] == expected_ids

    unwrap(client.put(f"/api/v1/projects/{project_id}/risk-summary/merge", json={"mergeEnabled": True}))
    for path in ["risk-items", "risk-suggestions"]:
        rows = unwrap(client.get(f"/api/v1/projects/{project_id}/{path}?pageNo=1&pageSize=10"))["list"]
        assert rows[0]["mergedRiskRecordIds"] == [records[1].id, records[0].id]
        assert rows[1]["riskRecordId"] == records[2].id
