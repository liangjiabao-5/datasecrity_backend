from app.common.exceptions import NotFoundError
from app.common.pagination import paginate_query
from app.common.utils import camelize_keys
from app.extensions import SessionLocal
from app.models import (
    EvaluationRecord,
    ProjectAssessmentItem,
    ProjectRiskSummaryRecord,
    RiskSourceTemplate,
)
from app.services import harm_analysis_service
from app.services.audit_service import audit
from app.services.evaluation_service import calculate_project_score_snapshot
from app.services.project_service import get_project


AUTO_REMEDIATION_SUGGESTION = "建议针对该风险明确责任人、整改措施和完成期限，完善相应数据安全控制。"
DEFAULT_RELATED_DATA = "-"
DEFAULT_HARM_LEVEL = "-"
DEFAULT_POSSIBILITY_LEVEL = "-"

RISK_SOURCE_FIELDS = {
    "risk_types",
    "risk_description",
    "risk_source_description",
    "related_data",
    "related_activities",
}
RISK_ITEM_FIELDS = {
    "harm_level",
    "possibility_level",
    "risk_level",
}
RISK_SUGGESTION_FIELDS = {"remediation_suggestion"}


def refresh(project_id: str, payload: dict) -> dict:
    project = get_project(project_id)
    overwrite = bool(payload.get("overwrite_manual_changes", False))
    session = SessionLocal()
    score_snapshot = calculate_project_score_snapshot(session, project)
    possibility_level = score_snapshot["possibilityLevel"]

    assessment_rows = (
        session.query(EvaluationRecord, ProjectAssessmentItem)
        .join(ProjectAssessmentItem, ProjectAssessmentItem.id == EvaluationRecord.item_id)
        .filter(
            EvaluationRecord.project_id == project_id,
            EvaluationRecord.deleted.is_(False),
            EvaluationRecord.evaluation_result.in_(["PARTIAL", "NON_COMPLIANT"]),
            ProjectAssessmentItem.project_id == project_id,
            ProjectAssessmentItem.deleted.is_(False),
        )
        .order_by(ProjectAssessmentItem.sort_order.asc())
        .all()
    )
    existing_records = (
        session.query(ProjectRiskSummaryRecord)
        .filter(ProjectRiskSummaryRecord.project_id == project_id, ProjectRiskSummaryRecord.deleted.is_(False))
        .all()
    )
    records_by_item = {row.evaluation_item_id: row for row in existing_records}
    current_item_ids = {item.id for _record, item in assessment_rows}

    templates = _risk_source_templates_by_key(session)

    created = 0
    updated = 0
    retained_manual = 0
    inactive = 0

    for evaluation_record, item in assessment_rows:
        summary = records_by_item.get(item.id)
        is_new = summary is None
        if is_new:
            summary = ProjectRiskSummaryRecord(project_id=project_id, evaluation_item_id=item.id)
            session.add(summary)
            records_by_item[item.id] = summary
            created += 1
        else:
            updated += 1

        summary.current = True
        _sync_evaluation_projection(summary, evaluation_record, item)
        if is_new or overwrite or not summary.manual_adjusted:
            _refresh_risk_fields(
                summary,
                evaluation_record,
                item,
                templates.get(_template_key(item.sheet_name, item.category, item.subcategory, item.check_point)),
                possibility_level,
            )
        else:
            retained_manual += 1

    for summary in existing_records:
        if summary.evaluation_item_id not in current_item_ids and summary.current:
            summary.current = False
            inactive += 1

    audit(
        "RISK_SUMMARY_REFRESH",
        "Project",
        project_id,
        after={
            "createdRecords": created,
            "updatedRecords": updated,
            "inactiveRecords": inactive,
            "retainedManualRecords": retained_manual,
            "overwriteManualChanges": overwrite,
            "score": score_snapshot["score"],
            "possibilityLevel": possibility_level,
        },
    )
    session.commit()
    return {
        "createdRecords": created,
        "updatedRecords": updated,
        "inactiveRecords": inactive,
        "retainedManualRecords": retained_manual,
        "createdSources": created,
        "createdItems": created,
        "createdSuggestions": created,
        "score": score_snapshot["score"],
        "possibilityLevel": possibility_level,
        "scoreModelVersion": score_snapshot["scoreModelVersion"],
    }


def list_risk_sources(project_id: str) -> dict:
    get_project(project_id)
    session = SessionLocal()
    return paginate_query(_current_records_query(session, project_id), serialize_risk_source)


def list_risk_items(project_id: str) -> dict:
    get_project(project_id)
    session = SessionLocal()
    return paginate_query(_current_records_query(session, project_id), serialize_risk_item)


def list_risk_suggestions(project_id: str) -> dict:
    get_project(project_id)
    session = SessionLocal()
    return paginate_query(_current_records_query(session, project_id), serialize_risk_suggestion)


def update_risk_source(project_id: str, risk_source_id: str, payload: dict) -> dict:
    return _update_summary_record(project_id, risk_source_id, payload, RISK_SOURCE_FIELDS, serialize_risk_source, "RISK_SOURCE_UPDATE")


def update_risk_item(project_id: str, risk_item_id: str, payload: dict) -> dict:
    return _update_summary_record(project_id, risk_item_id, payload, RISK_ITEM_FIELDS, serialize_risk_item, "RISK_ITEM_UPDATE")


def update_risk_suggestion(project_id: str, suggestion_id: str, payload: dict) -> dict:
    return _update_summary_record(
        project_id,
        suggestion_id,
        payload,
        RISK_SUGGESTION_FIELDS,
        serialize_risk_suggestion,
        "RISK_SUGGESTION_UPDATE",
    )


def serialize_risk_source(row: ProjectRiskSummaryRecord) -> dict:
    return _serialize_common(row)


def serialize_risk_item(row: ProjectRiskSummaryRecord) -> dict:
    return _serialize_common(row)


def serialize_risk_suggestion(row: ProjectRiskSummaryRecord) -> dict:
    return _serialize_common(row)


def _update_summary_record(project_id: str, record_id: str, payload: dict, allowed_fields: set[str], serializer, action: str) -> dict:
    project = get_project(project_id)
    session = SessionLocal()
    record = _get_summary_record(session, project_id, record_id)
    before = record.to_dict()
    changed_risk_factor = False
    for field in allowed_fields:
        if field in payload:
            setattr(record, field, payload.get(field))
            if field in {"harm_level", "possibility_level"}:
                changed_risk_factor = True
    if changed_risk_factor:
        # 数据安全风险等级由“危害程度 + 发生可能性”联动计算。
        # 只要这两个因子发生变化，就以后端矩阵计算结果为准，覆盖前端可能传来的旧 riskLevel。
        calculated_risk_level = _linked_risk_level(session, project, record)
        record.risk_level = calculated_risk_level
    record.manual_adjusted = True
    audit(action, "ProjectRiskSummaryRecord", record.id, before=before, after=record.to_dict())
    session.commit()
    return serializer(record)


def _linked_risk_level(session, project, record: ProjectRiskSummaryRecord) -> str | None:
    """按危害程度和发生可能性联动计算安全风险等级；任一因子为空时返回空。"""
    if record.harm_level in (None, "", DEFAULT_HARM_LEVEL) or record.possibility_level in (None, "", DEFAULT_POSSIBILITY_LEVEL):
        return None
    return harm_analysis_service.risk_level_from_project_matrix(session, project, record.harm_level, record.possibility_level)


def _current_records_query(session, project_id: str):
    return (
        session.query(ProjectRiskSummaryRecord)
        .filter(
            ProjectRiskSummaryRecord.project_id == project_id,
            ProjectRiskSummaryRecord.deleted.is_(False),
            ProjectRiskSummaryRecord.current.is_(True),
        )
        .order_by(ProjectRiskSummaryRecord.created_at.asc())
    )


def _get_summary_record(session, project_id: str, record_id: str) -> ProjectRiskSummaryRecord:
    record = (
        session.query(ProjectRiskSummaryRecord)
        .filter(
            ProjectRiskSummaryRecord.id == record_id,
            ProjectRiskSummaryRecord.project_id == project_id,
            ProjectRiskSummaryRecord.deleted.is_(False),
            ProjectRiskSummaryRecord.current.is_(True),
        )
        .first()
    )
    if not record:
        raise NotFoundError("Risk summary record not found.")
    return record


def _sync_evaluation_projection(summary: ProjectRiskSummaryRecord, record: EvaluationRecord, item: ProjectAssessmentItem) -> None:
    summary.evaluation_item_id = item.id
    summary.source_item_code = item.item_code
    summary.assessment_category = item.category
    summary.assessment_subcategory = item.subcategory
    summary.check_point = item.check_point
    summary.evaluation_result = record.evaluation_result
    summary.evaluation_record = record.evaluation_record


def _refresh_risk_fields(
    summary: ProjectRiskSummaryRecord,
    record: EvaluationRecord,
    item: ProjectAssessmentItem,
    template: RiskSourceTemplate | None,
    possibility_level: str | None,
) -> None:
    summary.risk_types = template.risk_types if template else []
    summary.risk_description = template.risk_description if template and template.risk_description else _risk_description(record, item)
    summary.risk_source_description = (
        template.risk_source_description if template and template.risk_source_description else record.evaluation_record or item.check_point
    )
    summary.related_data = DEFAULT_RELATED_DATA
    summary.related_activities = []
    summary.harm_level = DEFAULT_HARM_LEVEL
    summary.harm_description = None
    summary.harm_impact_object = None
    summary.harm_example = None
    summary.harm_analysis_trace = None
    summary.harm_analysis_confidence = None
    summary.harm_analysis_input_hash = None
    summary.possibility_level = possibility_level or DEFAULT_POSSIBILITY_LEVEL
    summary.risk_level = None
    summary.remediation_suggestion = (
        template.remediation_suggestion if template and template.remediation_suggestion else AUTO_REMEDIATION_SUGGESTION
    )


def _serialize_common(row: ProjectRiskSummaryRecord) -> dict:
    data = row.to_dict()
    data["riskRecordId"] = row.id
    data["riskSourceId"] = row.id
    data["riskItemId"] = row.id
    data["suggestionId"] = row.id
    data["riskTypes"] = row.risk_types or []
    data["relatedActivities"] = row.related_activities or []
    if row.harm_analysis_trace:
        data["harmAnalysisTrace"] = camelize_keys(row.harm_analysis_trace)
    if row.harm_analysis_trace:
        data["harmAnalysisStatus"] = "CONFIRMED"
    elif row.harm_level in (None, "", DEFAULT_HARM_LEVEL):
        data["harmAnalysisStatus"] = "PENDING"
    else:
        data["harmAnalysisStatus"] = "MANUAL"
    return data


def _risk_description(record: EvaluationRecord, item: ProjectAssessmentItem) -> str:
    basis = record.evaluation_record or item.check_point or "现场测评发现不符合项"
    return f"现场测评发现风险项：{basis}"


def _risk_source_templates_by_key(session) -> dict[tuple[str, str, str, str], RiskSourceTemplate]:
    rows = (
        session.query(RiskSourceTemplate)
        .filter(RiskSourceTemplate.deleted.is_(False))
        .order_by(RiskSourceTemplate.sort_order.asc())
        .all()
    )
    return {
        _template_key(row.sheet_name, row.category, row.subcategory, row.assessment_item): row
        for row in rows
    }


def _template_key(sheet_name: str | None, category: str | None, subcategory: str | None, assessment_item: str | None) -> tuple[str, str, str, str]:
    return (
        _normalize_template_key(sheet_name),
        _normalize_template_key(category),
        _normalize_template_key(subcategory),
        _normalize_template_key(assessment_item),
    )


def _normalize_template_key(value: str | None) -> str:
    return " ".join(str(value or "").split())
