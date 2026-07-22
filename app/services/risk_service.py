import re

from app.common.exceptions import BusinessError, NotFoundError
from app.common.pagination import page_args, paginate_query
from app.common.utils import camelize_keys
from app.extensions import SessionLocal
from app.models import (
    DataAsset,
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
RELATED_ACTIVITY_OPTIONS = ["收集", "传输", "存储", "使用和加工", "提供", "公开", "删除"]
MULTI_VALUE_PATTERN = re.compile(r"[、,，;；\r\n]+")
MERGE_CONFLICT_FIELDS = [
    ("harm_level", "harmLevel"),
    ("possibility_level", "possibilityLevel"),
    ("risk_level", "riskLevel"),
    ("remediation_suggestion", "remediationSuggestion"),
]
MERGE_CONFLICT_MESSAGE = "合并行中存在不一致的评审结果，请评估人重新确认。"
MERGE_UPDATE_REINPUT_MESSAGE = "更新合并数据成功，请重新填写数据安全风险清单页、数据安全风险处置建议页所影响的数据"
MERGE_CONFLICT_FIELD_LABELS = {
    "harmLevel": ("数据安全风险清单", "危害程度"),
    "possibilityLevel": ("数据安全风险清单", "发生可能性"),
    "riskLevel": ("数据安全风险清单", "风险等级"),
    "remediationSuggestion": ("数据安全风险处置建议", "整改建议"),
}
RELATED_REVIEW_RESET_TRIGGER_FIELDS = {"related_data", "related_activities"}

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
        template = templates.get(_template_key(item.sheet_name, item.category, item.subcategory, item.check_point))
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
        _sync_template_projection(summary, template)
        if is_new or overwrite or not summary.manual_adjusted:
            _refresh_risk_fields(
                summary,
                evaluation_record,
                item,
                template,
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


def get_risk_form_options(project_id: str) -> dict:
    get_project(project_id)
    session = SessionLocal()
    assets = (
        session.query(DataAsset.data_category, DataAsset.data_level)
        .filter(DataAsset.project_id == project_id, DataAsset.deleted.is_(False))
        .order_by(DataAsset.created_at.asc())
        .all()
    )
    related_data_options = []
    seen = set()
    for category, level in assets:
        option = _related_data_option(category, level)
        if option and option not in seen:
            seen.add(option)
            related_data_options.append(option)
    return {
        "relatedDataOptions": related_data_options,
        "relatedActivityOptions": list(RELATED_ACTIVITY_OPTIONS),
    }


def get_risk_merge_state(project_id: str) -> dict:
    project = get_project(project_id)
    return {"mergeEnabled": bool(getattr(project, "risk_merge_enabled", False))}


def update_risk_merge_state(project_id: str, payload: dict) -> dict:
    session = SessionLocal()
    project = get_project(project_id)
    merge_enabled = bool(payload.get("merge_enabled", payload.get("mergeEnabled", False)))
    before = {"mergeEnabled": bool(getattr(project, "risk_merge_enabled", False))}
    if merge_enabled:
        missing_row_numbers = _risk_merge_missing_row_numbers(session, project_id)
        if missing_row_numbers:
            row_text = _join_row_numbers(missing_row_numbers)
            raise BusinessError(
                "RISK_MERGE_REQUIRED_FIELDS_MISSING",
                f"第{row_text}行需填写完整后再进行合并",
                data={"rowNos": row_text},
            )
    project.risk_merge_enabled = merge_enabled
    after = {"mergeEnabled": merge_enabled}
    audit("RISK_MERGE_STATE_UPDATE", "Project", project_id, before=before, after=after)
    session.commit()
    return after


def update_risk_merge_data(project_id: str) -> dict:
    project = get_project(project_id)
    session = SessionLocal()
    missing_row_numbers = _risk_merge_missing_row_numbers(session, project_id)
    if missing_row_numbers:
        row_text = _join_row_numbers(missing_row_numbers)
        raise BusinessError(
            "RISK_MERGE_REQUIRED_FIELDS_MISSING",
            f"第{row_text}行需填写完整后再进行合并",
            data={"rowNos": row_text},
        )

    merged_records = _merged_current_records(session, project_id)
    result = {
        "mergeEnabled": bool(getattr(project, "risk_merge_enabled", False)),
        "updatedMergedCount": len(merged_records),
        "hasMergeConflict": False,
        "mergeConflictTabs": [],
    }
    audit("RISK_MERGE_UPDATE", "Project", project_id, after=result)
    session.commit()
    result["_message"] = MERGE_UPDATE_REINPUT_MESSAGE
    return result


def list_risk_items(project_id: str) -> dict:
    project = get_project(project_id)
    session = SessionLocal()
    if getattr(project, "risk_merge_enabled", False):
        return _paginate_records(_merged_current_records(session, project_id), serialize_risk_item)
    return paginate_query(_current_records_query(session, project_id), serialize_risk_item)


def list_risk_suggestions(project_id: str) -> dict:
    project = get_project(project_id)
    session = SessionLocal()
    if getattr(project, "risk_merge_enabled", False):
        return _paginate_records(_merged_current_records(session, project_id), serialize_risk_suggestion)
    return paginate_query(_current_records_query(session, project_id), serialize_risk_suggestion)


def current_records_for_display(session, project) -> list[ProjectRiskSummaryRecord]:
    """返回汇总分析页面当前展示口径的数据，供报告等非分页场景复用。"""
    if getattr(project, "risk_merge_enabled", False):
        return _merged_current_records(session, project.id)
    return _current_records_query(session, project.id).all()


def current_records(session, project_id: str) -> list[ProjectRiskSummaryRecord]:
    """按汇总分析默认顺序返回未合并的当前风险明细。"""
    return _current_records_query(session, project_id).all()


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
    records = _target_summary_records(session, project_id, record_id, payload)
    reset_context, current_records = _related_review_reset_context(session, project_id, records, payload, allowed_fields)
    before_by_id = {record.id: record.to_dict() for record in records}
    changed_risk_factor = False
    for field in allowed_fields:
        if field in payload and field in {"harm_level", "possibility_level"}:
            changed_risk_factor = True
            break

    for record in records:
        for field in allowed_fields:
            if field in payload:
                setattr(record, field, _normalize_update_value(field, payload.get(field)))
        if changed_risk_factor:
            # 数据安全风险等级由“危害程度 + 发生可能性”联动计算。
            # 只要这两个因子发生变化，就以后端矩阵计算结果为准，覆盖前端可能传来的旧 riskLevel。
            calculated_risk_level = _linked_risk_level(session, project, record)
            record.risk_level = calculated_risk_level
        record.manual_adjusted = True

    reset_records = _related_review_reset_records(current_records, reset_context)
    target_record_ids = {record.id for record in records}
    reset_before_by_id = {record.id: record.to_dict() for record in reset_records if record.id not in target_record_ids}
    _clear_review_fields(reset_records)

    for reset_record in reset_records:
        if reset_record.id not in target_record_ids:
            audit(
                "RISK_REVIEW_RESET_BY_RELATED_SCOPE_UPDATE",
                "ProjectRiskSummaryRecord",
                reset_record.id,
                before=reset_before_by_id[reset_record.id],
                after=reset_record.to_dict(),
            )

    for record in records:
        audit(action, "ProjectRiskSummaryRecord", record.id, before=before_by_id[record.id], after=record.to_dict())
    session.commit()
    return serializer(_primary_target_record(records, record_id))


def _linked_risk_level(session, project, record: ProjectRiskSummaryRecord) -> str | None:
    """按危害程度和发生可能性联动计算安全风险等级；任一因子为空时返回空。"""
    if record.harm_level in (None, "", DEFAULT_HARM_LEVEL) or record.possibility_level in (None, "", DEFAULT_POSSIBILITY_LEVEL):
        return None
    return harm_analysis_service.risk_level_from_project_matrix(session, project, record.harm_level, record.possibility_level)


def _current_records_query(session, project_id: str):
    return (
        session.query(ProjectRiskSummaryRecord)
        .join(ProjectAssessmentItem, ProjectAssessmentItem.id == ProjectRiskSummaryRecord.evaluation_item_id)
        .filter(
            ProjectRiskSummaryRecord.project_id == project_id,
            ProjectRiskSummaryRecord.deleted.is_(False),
            ProjectRiskSummaryRecord.current.is_(True),
            ProjectAssessmentItem.project_id == project_id,
            ProjectAssessmentItem.deleted.is_(False),
        )
        .order_by(ProjectAssessmentItem.sort_order.asc(), ProjectAssessmentItem.id.asc())
    )


def _paginate_records(records: list[ProjectRiskSummaryRecord], serializer) -> dict:
    page_no, page_size = page_args()
    start = (page_no - 1) * page_size
    return {
        "list": [serializer(row) for row in records[start : start + page_size]],
        "pageNo": page_no,
        "pageSize": page_size,
        "total": len(records),
    }


def _merged_current_records(session, project_id: str) -> list[ProjectRiskSummaryRecord]:
    groups = {}
    for row in _current_records_query(session, project_id).all():
        groups.setdefault(_risk_merge_key(row), []).append(row)
    return [_merged_record(rows) for rows in groups.values()]


def _risk_merge_key(row: ProjectRiskSummaryRecord) -> tuple:
    return (
        _clean_option_part(row.assessment_category),
        _clean_option_part(row.assessment_subcategory),
        tuple(sorted(_list_values(row.risk_types))),
        _clean_option_part(row.risk_source_type),
        tuple(sorted(_list_values(row.related_activities))),
        tuple(sorted(_list_values(row.related_data))),
    )


def _merged_record(rows: list[ProjectRiskSummaryRecord]) -> ProjectRiskSummaryRecord:
    base = rows[0]
    merged = ProjectRiskSummaryRecord()
    for column in ProjectRiskSummaryRecord.__mapper__.columns:
        setattr(merged, column.key, getattr(base, column.key))
    merged.merged_risk_record_ids = [row.id for row in rows]
    conflict_fields = _merge_conflict_fields(rows)
    merged.has_merge_conflict = bool(conflict_fields)
    merged.merge_conflict_fields = conflict_fields
    if conflict_fields:
        merged.merge_conflict_message = MERGE_CONFLICT_MESSAGE
    if len(rows) > 1:
        merged.risk_description = _join_unique([row.risk_description for row in rows], "\n")
        merged.assessment_item_id = _join_unique([row.assessment_item_id for row in rows], "、")
        merged.evaluation_record = _join_unique([row.evaluation_record for row in rows], "\n")
        merged.evaluation_result = "NON_COMPLIANT"
    return merged


def _merge_conflict_fields(rows: list[ProjectRiskSummaryRecord]) -> list[str]:
    if len(rows) <= 1:
        return []
    conflict_fields = []
    for field, response_field in MERGE_CONFLICT_FIELDS:
        values = {_clean_option_part(getattr(row, field, None)) for row in rows}
        if len(values) > 1:
            conflict_fields.append(response_field)
    return conflict_fields


def _merge_conflict_tabs(conflict_fields: list[str]) -> list[dict]:
    tabs = []
    by_tab = {}
    for field in conflict_fields:
        tab, label = MERGE_CONFLICT_FIELD_LABELS.get(field, ("", ""))
        if not tab:
            continue
        tab_payload = by_tab.setdefault(tab, {"tab": tab, "fields": [], "fieldCodes": []})
        tab_payload["fields"].append(label)
        tab_payload["fieldCodes"].append(field)
    for tab, _labels in _merge_conflict_tab_order():
        if tab in by_tab:
            tabs.append(by_tab[tab])
    return tabs


def _merge_update_message(conflict_tabs: list[dict]) -> str:
    if not conflict_tabs:
        return "更新合并数据成功"
    parts = [f"{item['tab']}标签页下的{'、'.join(item['fields'])}字段" for item in conflict_tabs]
    return f"更新合并数据成功，存在合并冲突，{'，'.join(parts)}存在冲突，请重新填写"


def _merge_conflict_tab_order() -> list[tuple[str, list[str]]]:
    return [
        ("数据安全风险清单", ["harmLevel", "possibilityLevel", "riskLevel"]),
        ("数据安全风险处置建议", ["remediationSuggestion"]),
    ]


def _join_unique(values, separator: str) -> str:
    result = []
    seen = set()
    for value in values:
        text = _clean_option_part(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return separator.join(result)


def _risk_merge_missing_row_numbers(session, project_id: str) -> list[int]:
    row_numbers = []
    for row_no, row in enumerate(_current_records_query(session, project_id).all(), start=1):
        if not _has_related_data(row.related_data) or not _list_values(row.related_activities):
            row_numbers.append(row_no)
    return row_numbers


def _join_row_numbers(row_numbers: list[int]) -> str:
    return "、".join(str(row_no) for row_no in row_numbers)


def _has_related_data(value) -> bool:
    text = _clean_option_part(value)
    return text not in {"", DEFAULT_RELATED_DATA}


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


def _target_summary_records(session, project_id: str, record_id: str, payload: dict) -> list[ProjectRiskSummaryRecord]:
    target_ids = _merged_target_ids(payload)
    if not target_ids:
        return [_get_summary_record(session, project_id, record_id)]

    records = (
        session.query(ProjectRiskSummaryRecord)
        .filter(
            ProjectRiskSummaryRecord.id.in_(target_ids),
            ProjectRiskSummaryRecord.project_id == project_id,
            ProjectRiskSummaryRecord.deleted.is_(False),
            ProjectRiskSummaryRecord.current.is_(True),
        )
        .all()
    )
    records_by_id = {record.id: record for record in records}
    if len(records_by_id) != len(target_ids):
        raise NotFoundError("Risk summary record not found.")
    return [records_by_id[target_id] for target_id in target_ids]


def _merged_target_ids(payload: dict) -> list[str]:
    raw_ids = payload.get("merged_risk_record_ids", payload.get("mergedRiskRecordIds"))
    if raw_ids in (None, "", []):
        return []
    if not isinstance(raw_ids, (list, tuple, set)):
        raise BusinessError("INVALID_MERGED_RISK_RECORD_IDS", "mergedRiskRecordIds must be an array.")
    target_ids = []
    seen = set()
    for raw_id in raw_ids:
        target_id = _clean_option_part(raw_id)
        if target_id and target_id not in seen:
            seen.add(target_id)
            target_ids.append(target_id)
    return target_ids


def _primary_target_record(records: list[ProjectRiskSummaryRecord], record_id: str) -> ProjectRiskSummaryRecord:
    for record in records:
        if record.id == record_id:
            return record
    return records[0]


def _related_review_reset_context(
    session,
    project_id: str,
    records: list[ProjectRiskSummaryRecord],
    payload: dict,
    allowed_fields: set[str],
) -> tuple[list[dict], list[ProjectRiskSummaryRecord]]:
    if not (RELATED_REVIEW_RESET_TRIGGER_FIELDS & allowed_fields & set(payload.keys())):
        return [], []

    current_records = _current_records_query(session, project_id).all()
    context = []
    for record in records:
        old_merge_key = _risk_merge_key(record)
        context.append(
            {
                "record": record,
                "oldRelatedKey": _related_review_dependency_key(record),
                "oldGroupIds": [row.id for row in current_records if _risk_merge_key(row) == old_merge_key],
            }
        )
    return context, current_records


def _related_review_reset_records(
    current_records: list[ProjectRiskSummaryRecord],
    reset_context: list[dict],
) -> list[ProjectRiskSummaryRecord]:
    if not current_records or not reset_context:
        return []

    affected_ids = set()
    for item in reset_context:
        record = item["record"]
        if item["oldRelatedKey"] == _related_review_dependency_key(record):
            continue
        affected_ids.add(record.id)
        affected_ids.update(item["oldGroupIds"])
        new_merge_key = _risk_merge_key(record)
        affected_ids.update(row.id for row in current_records if _risk_merge_key(row) == new_merge_key)
    return [row for row in current_records if row.id in affected_ids]


def _related_review_dependency_key(row: ProjectRiskSummaryRecord) -> tuple:
    return (
        tuple(sorted(_list_values(row.related_activities))),
        tuple(sorted(_list_values(row.related_data))),
    )


def _clear_review_fields(records: list[ProjectRiskSummaryRecord]) -> None:
    for record in records:
        record.harm_level = None
        record.harm_description = None
        record.harm_impact_object = None
        record.harm_example = None
        record.harm_analysis_trace = None
        record.harm_analysis_confidence = None
        record.harm_analysis_input_hash = None
        record.possibility_level = None
        record.risk_level = None
        record.remediation_suggestion = None


def _sync_evaluation_projection(summary: ProjectRiskSummaryRecord, record: EvaluationRecord, item: ProjectAssessmentItem) -> None:
    summary.evaluation_item_id = item.id
    summary.source_item_code = item.item_code
    summary.assessment_item_id = item.assessment_item_id
    summary.assessment_category = item.category
    summary.assessment_subcategory = item.subcategory
    summary.check_point = item.check_point
    summary.evaluation_result = record.evaluation_result
    summary.evaluation_record = record.evaluation_record


def _sync_template_projection(summary: ProjectRiskSummaryRecord, template: RiskSourceTemplate | None) -> None:
    summary.risk_source_type = template.risk_source_type if template else None


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
    merged_ids = getattr(row, "merged_risk_record_ids", None)
    if merged_ids:
        data["mergedRiskRecordIds"] = merged_ids
        data["merged"] = len(merged_ids) > 1
        data["hasMergeConflict"] = bool(getattr(row, "has_merge_conflict", False))
        data["mergeConflictFields"] = getattr(row, "merge_conflict_fields", [])
        if getattr(row, "merge_conflict_message", None):
            data["mergeConflictMessage"] = row.merge_conflict_message
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


def _related_data_option(category: str | None, level: str | None) -> str | None:
    category_text = _clean_option_part(category)
    level_text = _clean_option_part(level)
    if not category_text or not level_text:
        return None
    return f"{category_text}/{level_text}"


def _clean_option_part(value) -> str:
    return " ".join(str(value or "").split())


def _normalize_update_value(field: str, value):
    if field == "related_data":
        return _join_text_values(value)
    if field in {"risk_types", "related_activities"}:
        return _list_values(value)
    return value


def _join_text_values(value) -> str | None:
    values = _list_values(value)
    if not values:
        return None
    return "、".join(values)


def _list_values(value) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, (list, tuple, set)):
        candidates = value
    else:
        candidates = MULTI_VALUE_PATTERN.split(str(value))
    result = []
    seen = set()
    for item in candidates:
        text = _clean_option_part(item)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _risk_source_templates_by_key(session) -> dict[tuple[str, str, str, str], RiskSourceTemplate]:
    rows = (
        session.query(RiskSourceTemplate)
        .filter(RiskSourceTemplate.deleted.is_(False))
        .order_by(RiskSourceTemplate.sort_order.asc())
        .all()
    )
    result = {}
    for row in rows:
        key = _template_key(row.sheet_name, row.category, row.subcategory, row.assessment_item)
        if key[3] and key not in result:
            result[key] = row
    return result


def _template_key(sheet_name: str | None, category: str | None, subcategory: str | None, assessment_item: str | None) -> tuple[str, str, str, str]:
    return (
        _normalize_template_key(sheet_name),
        _normalize_template_key(category),
        _normalize_template_key(subcategory),
        _normalize_template_key(assessment_item),
    )


def _normalize_template_key(value: str | None) -> str:
    return " ".join(str(value or "").split())
