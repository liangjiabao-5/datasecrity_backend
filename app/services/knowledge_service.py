from __future__ import annotations

from sqlalchemy import or_

from app.common.exceptions import BusinessError, NotFoundError
from app.common.pagination import paginate_query
from app.common.utils import require_fields
from app.extensions import SessionLocal
from app.models import (
    AssessmentTemplate,
    AssessmentTemplateItem,
    HarmModel,
    HarmModelRule,
    Project,
    RemediationSuggestionTemplate,
    RiskMatrix,
    ScoreModel,
    ScoreModelRange,
)
from app.services.audit_service import audit


RISK_LEVELS = {"MAJOR", "HIGH", "MEDIUM", "LOW", "SLIGHT"}
MATRIX_POSSIBILITY_LEVELS = {"HIGH", "MEDIUM", "LOW"}
MATRIX_HARM_LEVELS = {"VERY_HIGH", "HIGH", "RELATIVELY_HIGH", "MEDIUM", "LOW"}
HARM_IMPACT_OBJECTS = {"NATIONAL_SECURITY", "PUBLIC_INTEREST", "LEGAL_RIGHTS"}
HARM_DAMAGE_DEGREES = {"GENERAL", "SERIOUS", "EXTREMELY_SERIOUS"}

GENERIC_RESOURCES = {
    "remediation-suggestions": {
        "model": RemediationSuggestionTemplate,
        "required": ["suggestion_title"],
        "search": [RemediationSuggestionTemplate.suggestion_title],
    },
}


def list_generic(kind: str, args) -> dict:
    resource = _resource(kind)
    session = SessionLocal()
    model = resource["model"]
    query = session.query(model).filter(model.deleted.is_(False))
    query = _apply_common_filters(query, model, resource["search"], args)
    return paginate_query(query.order_by(model.created_at.desc()), lambda row: row.to_dict())


def get_generic(kind: str, record_id: str) -> dict:
    return _get(SessionLocal(), _resource(kind)["model"], record_id).to_dict()


def create_generic(kind: str, payload: dict) -> dict:
    resource = _resource(kind)
    require_fields(payload, resource["required"])
    model = resource["model"]
    record = model(**_pick(payload, model))
    session = SessionLocal()
    session.add(record)
    audit(f"KNOWLEDGE_{model.__name__.upper()}_CREATE", model.__name__, record.id, after=record.to_dict())
    session.commit()
    return record.to_dict()


def update_generic(kind: str, record_id: str, payload: dict) -> dict:
    session = SessionLocal()
    model = _resource(kind)["model"]
    record = _get(session, model, record_id)
    before = record.to_dict()
    for field, value in _pick(payload, model).items():
        setattr(record, field, value)
    if hasattr(record, "version"):
        record.version = (record.version or 1) + 1
    audit(f"KNOWLEDGE_{model.__name__.upper()}_UPDATE", model.__name__, record.id, before=before, after=record.to_dict())
    session.commit()
    return record.to_dict()


def delete_generic(kind: str, record_id: str) -> dict:
    session = SessionLocal()
    model = _resource(kind)["model"]
    record = _get(session, model, record_id)
    before = record.to_dict()
    record.deleted = True
    audit(f"KNOWLEDGE_{model.__name__.upper()}_DELETE", model.__name__, record.id, before=before, after={"deleted": True})
    session.commit()
    return {"id": record.id, "deleted": True}


def list_assessment_templates(args) -> dict:
    session = SessionLocal()
    query = session.query(AssessmentTemplate).filter(AssessmentTemplate.deleted.is_(False))
    query = _apply_common_filters(
        query,
        AssessmentTemplate,
        [AssessmentTemplate.template_name],
        args,
    )
    return paginate_query(query.order_by(AssessmentTemplate.created_at.desc()), lambda row: row.to_dict())


def get_assessment_template(template_id: str) -> dict:
    session = SessionLocal()
    template = _get(session, AssessmentTemplate, template_id)
    data = template.to_dict()
    data["items"] = [_serialize_template_item(item) for item in _template_items(session, template_id)]
    return data


def create_assessment_template(payload: dict) -> dict:
    require_fields(payload, ["template_name"])
    session = SessionLocal()
    template = AssessmentTemplate(**_pick(payload, AssessmentTemplate))
    template.status = template.status or "ENABLED"
    template.version = template.version or 1
    session.add(template)
    _replace_template_items(session, template, payload.get("items") or [])
    audit("KNOWLEDGE_ASSESSMENT_TEMPLATE_CREATE", "AssessmentTemplate", template.id, after=template.to_dict())
    session.commit()
    return get_assessment_template(template.id)


def update_assessment_template(template_id: str, payload: dict) -> dict:
    session = SessionLocal()
    template = _get(session, AssessmentTemplate, template_id)
    before = template.to_dict()
    for field, value in _pick(payload, AssessmentTemplate).items():
        setattr(template, field, value)
    template.version = (template.version or 1) + 1
    if "items" in payload:
        _replace_template_items(session, template, payload.get("items") or [])
    audit("KNOWLEDGE_ASSESSMENT_TEMPLATE_UPDATE", "AssessmentTemplate", template.id, before=before, after=template.to_dict())
    session.commit()
    return get_assessment_template(template.id)


def delete_assessment_template(template_id: str) -> dict:
    return _delete_referenced_or_logical(
        AssessmentTemplate,
        template_id,
        Project.assessment_template_id,
        "KNOWLEDGE_ASSESSMENT_TEMPLATE_DELETE",
    )


def export_assessment_template(template_id: str) -> dict:
    data = get_assessment_template(template_id)
    data["exportFormat"] = "JSON"
    return data


def list_template_items(template_id: str, args) -> dict:
    _get(SessionLocal(), AssessmentTemplate, template_id)
    session = SessionLocal()
    query = session.query(AssessmentTemplateItem).filter(
        AssessmentTemplateItem.template_id == template_id,
        AssessmentTemplateItem.deleted.is_(False),
    )
    if args.get("keyword"):
        keyword = f"%{args.get('keyword')}%"
        query = query.filter(
            or_(
                AssessmentTemplateItem.category.like(keyword),
                AssessmentTemplateItem.subcategory.like(keyword),
                AssessmentTemplateItem.check_point.like(keyword),
                AssessmentTemplateItem.standard_item_id.like(keyword),
            )
        )
    return paginate_query(query.order_by(AssessmentTemplateItem.sort_order.asc()), _serialize_template_item)


def create_template_item(template_id: str, payload: dict) -> dict:
    session = SessionLocal()
    template = _get(session, AssessmentTemplate, template_id)
    item = AssessmentTemplateItem(template_id=template.id, **_template_item_payload(payload))
    session.add(item)
    _sync_template_item_count(session, template)
    audit("KNOWLEDGE_TEMPLATE_ITEM_CREATE", "AssessmentTemplateItem", item.id, after=item.to_dict())
    session.commit()
    return _serialize_template_item(item)


def update_template_item(template_id: str, item_id: str, payload: dict) -> dict:
    session = SessionLocal()
    _get(session, AssessmentTemplate, template_id)
    item = _get_template_item(session, template_id, item_id)
    before = item.to_dict()
    for field, value in _template_item_payload(payload).items():
        setattr(item, field, value)
    audit("KNOWLEDGE_TEMPLATE_ITEM_UPDATE", "AssessmentTemplateItem", item.id, before=before, after=item.to_dict())
    session.commit()
    return _serialize_template_item(item)


def delete_template_item(template_id: str, item_id: str) -> dict:
    session = SessionLocal()
    template = _get(session, AssessmentTemplate, template_id)
    item = _get_template_item(session, template_id, item_id)
    before = item.to_dict()
    item.deleted = True
    _sync_template_item_count(session, template)
    audit("KNOWLEDGE_TEMPLATE_ITEM_DELETE", "AssessmentTemplateItem", item.id, before=before, after={"deleted": True})
    session.commit()
    return {"id": item.id, "deleted": True}


def list_score_models(args) -> dict:
    session = SessionLocal()
    query = session.query(ScoreModel).filter(ScoreModel.deleted.is_(False))
    query = _apply_common_filters(query, ScoreModel, [ScoreModel.model_name], args)
    return paginate_query(query.order_by(ScoreModel.created_at.desc()), lambda row: _serialize_score_model(row, include_ranges=True))


def get_score_model(model_id: str) -> dict:
    return _serialize_score_model(_get(SessionLocal(), ScoreModel, model_id), include_ranges=True)


def create_score_model(payload: dict) -> dict:
    require_fields(payload, ["model_name"])
    ranges = payload.get("possibility_ranges") or []
    validate_score_ranges(ranges)
    session = SessionLocal()
    model = ScoreModel(**_pick(payload, ScoreModel))
    model.status = model.status or "ENABLED"
    model.version = model.version or 1
    session.add(model)
    _replace_score_ranges(session, model, ranges)
    audit("KNOWLEDGE_SCORE_MODEL_CREATE", "ScoreModel", model.id, after=model.to_dict())
    session.commit()
    return get_score_model(model.id)


def update_score_model(model_id: str, payload: dict) -> dict:
    session = SessionLocal()
    model = _get(session, ScoreModel, model_id)
    before = _serialize_score_model(model, include_ranges=True)
    ranges = payload.get("possibility_ranges")
    if ranges is not None:
        validate_score_ranges(ranges)
    for field, value in _pick(payload, ScoreModel).items():
        setattr(model, field, value)
    model.version = (model.version or 1) + 1
    if ranges is not None:
        _replace_score_ranges(session, model, ranges)
    audit("KNOWLEDGE_SCORE_MODEL_UPDATE", "ScoreModel", model.id, before=before, after=model.to_dict())
    session.commit()
    return get_score_model(model.id)


def delete_score_model(model_id: str) -> dict:
    return _delete_referenced_or_logical(ScoreModel, model_id, Project.score_model_id, "KNOWLEDGE_SCORE_MODEL_DELETE")


def validate_score_model(model_id: str | None, payload: dict | None = None) -> dict:
    if payload and "possibility_ranges" in payload:
        ranges = payload.get("possibility_ranges") or []
    else:
        session = SessionLocal()
        _get(session, ScoreModel, model_id)
        ranges = [_range_to_payload(row) for row in _score_ranges(session, model_id)]
    validate_score_ranges(ranges)
    return {"valid": True}


def validate_score_ranges(ranges: list[dict]) -> None:
    if not ranges:
        raise BusinessError("SCORE_RANGE_REQUIRED", "Possibility ranges are required.")
    normalized = sorted(ranges, key=lambda item: float(item.get("min", item.get("min_score", 0))))
    previous_max = None
    for item in normalized:
        min_score = float(item.get("min", item.get("min_score")))
        max_score = float(item.get("max", item.get("max_score")))
        if min_score < 0 or max_score > 100 or min_score >= max_score:
            raise BusinessError("SCORE_RANGE_INVALID", "Score range must be within 0-100 and min must be less than max.")
        if previous_max is None:
            if min_score != 0:
                raise BusinessError("SCORE_RANGE_NOT_COVERED", "Score ranges must start at 0.")
        elif min_score != previous_max:
            if min_score < previous_max:
                raise BusinessError("SCORE_RANGE_OVERLAP", "Adjacent score ranges must not overlap.")
            raise BusinessError("SCORE_RANGE_GAP", "Score ranges must be continuous with no gaps.")
        previous_max = max_score
    last_max = float(normalized[-1].get("max", normalized[-1].get("max_score")))
    if last_max != 100:
        raise BusinessError("SCORE_RANGE_NOT_COVERED", "Score ranges must end at 100.")


def list_harm_models(args) -> dict:
    session = SessionLocal()
    query = session.query(HarmModel).filter(HarmModel.deleted.is_(False))
    query = _apply_common_filters(query, HarmModel, [HarmModel.model_name, HarmModel.description], args)
    return paginate_query(query.order_by(HarmModel.created_at.desc()), _serialize_harm_model)


def get_harm_model(model_id: str) -> dict:
    return _serialize_harm_model(_get(SessionLocal(), HarmModel, model_id), include_rules=True)


def create_harm_model(payload: dict) -> dict:
    require_fields(payload, ["model_name"])
    if "rule_config" in payload:
        validate_harm_rule_config(payload.get("rule_config"))
    session = SessionLocal()
    model = HarmModel(**_pick(payload, HarmModel))
    model.status = model.status or "ENABLED"
    model.version = model.version or 1
    session.add(model)
    for rule in payload.get("rules") or []:
        session.add(HarmModelRule(harm_model_id=model.id, **_pick(rule, HarmModelRule)))
    audit("KNOWLEDGE_HARM_MODEL_CREATE", "HarmModel", model.id, after=model.to_dict())
    session.commit()
    return get_harm_model(model.id)


def update_harm_model(model_id: str, payload: dict) -> dict:
    session = SessionLocal()
    model = _get(session, HarmModel, model_id)
    before = _serialize_harm_model(model, include_rules=True)
    if "rule_config" in payload:
        validate_harm_rule_config(payload.get("rule_config"))
    for field, value in _pick(payload, HarmModel).items():
        setattr(model, field, value)
    model.version = (model.version or 1) + 1
    audit("KNOWLEDGE_HARM_MODEL_UPDATE", "HarmModel", model.id, before=before, after=model.to_dict())
    session.commit()
    return get_harm_model(model.id)


def delete_harm_model(model_id: str) -> dict:
    return _delete_referenced_or_logical(HarmModel, model_id, Project.harm_model_id, "KNOWLEDGE_HARM_MODEL_DELETE")


def list_harm_rules(model_id: str) -> dict:
    _get(SessionLocal(), HarmModel, model_id)
    session = SessionLocal()
    query = session.query(HarmModelRule).filter(HarmModelRule.harm_model_id == model_id, HarmModelRule.deleted.is_(False))
    return paginate_query(query.order_by(HarmModelRule.sort_order.asc()), lambda row: row.to_dict())


def create_harm_rule(model_id: str, payload: dict) -> dict:
    require_fields(payload, ["level"])
    session = SessionLocal()
    _get(session, HarmModel, model_id)
    rule = HarmModelRule(harm_model_id=model_id, **_pick(payload, HarmModelRule))
    session.add(rule)
    audit("KNOWLEDGE_HARM_RULE_CREATE", "HarmModelRule", rule.id, after=rule.to_dict())
    session.commit()
    return rule.to_dict()


def update_harm_rule(model_id: str, rule_id: str, payload: dict) -> dict:
    session = SessionLocal()
    _get(session, HarmModel, model_id)
    rule = _get_harm_rule(session, model_id, rule_id)
    before = rule.to_dict()
    for field, value in _pick(payload, HarmModelRule).items():
        setattr(rule, field, value)
    audit("KNOWLEDGE_HARM_RULE_UPDATE", "HarmModelRule", rule.id, before=before, after=rule.to_dict())
    session.commit()
    return rule.to_dict()


def delete_harm_rule(model_id: str, rule_id: str) -> dict:
    session = SessionLocal()
    _get(session, HarmModel, model_id)
    rule = _get_harm_rule(session, model_id, rule_id)
    before = rule.to_dict()
    rule.deleted = True
    audit("KNOWLEDGE_HARM_RULE_DELETE", "HarmModelRule", rule.id, before=before, after={"deleted": True})
    session.commit()
    return {"id": rule.id, "deleted": True}


def list_risk_matrices(args) -> dict:
    session = SessionLocal()
    query = session.query(RiskMatrix).filter(RiskMatrix.deleted.is_(False))
    query = _apply_common_filters(query, RiskMatrix, [RiskMatrix.matrix_name], args)
    return paginate_query(query.order_by(RiskMatrix.created_at.desc()), lambda row: row.to_dict())


def get_risk_matrix(matrix_id: str) -> dict:
    return _get(SessionLocal(), RiskMatrix, matrix_id).to_dict()


def create_risk_matrix(payload: dict) -> dict:
    require_fields(payload, ["matrix_name"])
    matrix_json = payload.get("matrix_json") or payload.get("matrix") or {}
    validate_risk_matrix_payload(matrix_json)
    session = SessionLocal()
    matrix = RiskMatrix(**_pick(payload, RiskMatrix))
    matrix.matrix_json = matrix_json
    matrix.status = matrix.status or "ENABLED"
    matrix.version = matrix.version or 1
    session.add(matrix)
    audit("KNOWLEDGE_RISK_MATRIX_CREATE", "RiskMatrix", matrix.id, after=matrix.to_dict())
    session.commit()
    return matrix.to_dict()


def update_risk_matrix(matrix_id: str, payload: dict) -> dict:
    session = SessionLocal()
    matrix = _get(session, RiskMatrix, matrix_id)
    before = matrix.to_dict()
    matrix_json = payload.get("matrix_json") or payload.get("matrix")
    if matrix_json is not None:
        validate_risk_matrix_payload(matrix_json)
    for field, value in _pick(payload, RiskMatrix).items():
        setattr(matrix, field, value)
    if matrix_json is not None:
        matrix.matrix_json = matrix_json
    matrix.version = (matrix.version or 1) + 1
    audit("KNOWLEDGE_RISK_MATRIX_UPDATE", "RiskMatrix", matrix.id, before=before, after=matrix.to_dict())
    session.commit()
    return matrix.to_dict()


def delete_risk_matrix(matrix_id: str) -> dict:
    return _delete_referenced_or_logical(RiskMatrix, matrix_id, Project.risk_matrix_id, "KNOWLEDGE_RISK_MATRIX_DELETE")


def validate_risk_matrix(matrix_id: str | None, payload: dict | None = None) -> dict:
    if payload and ("matrix_json" in payload or "matrix" in payload):
        matrix_json = payload.get("matrix_json") or payload.get("matrix")
    else:
        matrix_json = _get(SessionLocal(), RiskMatrix, matrix_id).matrix_json
    validate_risk_matrix_payload(matrix_json)
    return {"valid": True}


def validate_risk_matrix_payload(matrix_json: dict) -> None:
    if not isinstance(matrix_json, dict) or not matrix_json:
        raise BusinessError("RISK_MATRIX_REQUIRED", "Risk matrix must be a non-empty object.")
    invalid_rows = set(matrix_json.keys()) - MATRIX_POSSIBILITY_LEVELS
    if invalid_rows:
        raise BusinessError("RISK_MATRIX_INVALID_AXIS", f"Invalid possibility level(s): {', '.join(sorted(invalid_rows))}.")
    for possibility_level, row in matrix_json.items():
        if not isinstance(row, dict) or not row:
            raise BusinessError("RISK_MATRIX_INVALID", f"Risk matrix row {possibility_level} must be a non-empty object.")
        invalid_columns = set(row.keys()) - MATRIX_HARM_LEVELS
        if invalid_columns:
            raise BusinessError("RISK_MATRIX_INVALID_AXIS", f"Invalid harm level(s): {', '.join(sorted(invalid_columns))}.")
        for harm_level, risk_level in row.items():
            if risk_level not in RISK_LEVELS:
                raise BusinessError("RISK_MATRIX_INVALID_LEVEL", f"Invalid risk level: {risk_level}.")


def validate_harm_rule_config(rule_config: dict | None) -> None:
    if rule_config in (None, {}):
        return
    if not isinstance(rule_config, dict):
        raise BusinessError("HARM_RULE_CONFIG_INVALID", "Harm rule config must be an object.")

    system_categories = rule_config.get("system_categories") or {}
    if not isinstance(system_categories, dict) or not system_categories:
        raise BusinessError("HARM_RULE_CONFIG_REQUIRED", "Harm rule config must include system_categories.")
    for category, config in system_categories.items():
        if not isinstance(config, dict):
            raise BusinessError("HARM_RULE_CONFIG_INVALID", f"System category {category} must be an object.")
        impact_degrees = config.get("impact_degrees") or {}
        if not isinstance(impact_degrees, dict):
            raise BusinessError("HARM_RULE_CONFIG_INVALID", f"System category {category} impact_degrees must be an object.")
        invalid_objects = set(impact_degrees.keys()) - HARM_IMPACT_OBJECTS
        if invalid_objects:
            raise BusinessError("HARM_RULE_CONFIG_INVALID_OBJECT", f"Invalid impact object(s): {', '.join(sorted(invalid_objects))}.")
        for impact_object, degrees in impact_degrees.items():
            if not isinstance(degrees, list):
                raise BusinessError("HARM_RULE_CONFIG_INVALID", f"Impact degrees for {impact_object} must be a list.")
            invalid_degrees = set(degrees) - HARM_DAMAGE_DEGREES
            if invalid_degrees:
                raise BusinessError("HARM_RULE_CONFIG_INVALID_DEGREE", f"Invalid damage degree(s): {', '.join(sorted(invalid_degrees))}.")

    matrix = rule_config.get("protection_level_matrix") or {}
    if not isinstance(matrix, dict) or set(matrix.keys()) != HARM_IMPACT_OBJECTS:
        raise BusinessError("HARM_RULE_CONFIG_MATRIX_INVALID", "Protection level matrix must include all impact objects.")
    for impact_object, row in matrix.items():
        if not isinstance(row, dict) or set(row.keys()) != HARM_DAMAGE_DEGREES:
            raise BusinessError("HARM_RULE_CONFIG_MATRIX_INVALID", f"Protection level matrix row {impact_object} must include all damage degrees.")
        try:
            invalid_levels = [level for level in row.values() if int(level) not in {1, 2, 3, 4, 5}]
        except (TypeError, ValueError) as exc:
            raise BusinessError("HARM_RULE_CONFIG_MATRIX_INVALID", "Protection levels must be integers between 1 and 5.") from exc
        if invalid_levels:
            raise BusinessError("HARM_RULE_CONFIG_MATRIX_INVALID", "Protection levels must be between 1 and 5.")

    level_map = rule_config.get("harm_level_by_protection_level") or {}
    if set(str(key) for key in level_map.keys()) != {"1", "2", "3", "4", "5"}:
        raise BusinessError("HARM_RULE_CONFIG_LEVEL_MAP_INVALID", "Harm level map must include protection levels 1-5.")
    invalid_harm_levels = set(level_map.values()) - MATRIX_HARM_LEVELS
    if invalid_harm_levels:
        raise BusinessError("HARM_RULE_CONFIG_LEVEL_MAP_INVALID", f"Invalid harm level(s): {', '.join(sorted(invalid_harm_levels))}.")


def _resource(kind: str) -> dict:
    if kind not in GENERIC_RESOURCES:
        raise NotFoundError("Knowledge resource not found.")
    return GENERIC_RESOURCES[kind]


def _get(session, model, record_id: str):
    record = session.get(model, record_id)
    if not record or record.deleted:
        raise NotFoundError(f"{model.__name__} not found.")
    return record


def _apply_common_filters(query, model, search_columns, args):
    status = args.get("status")
    keyword = args.get("keyword")
    enabled_only = str(args.get("enabledOnly", "")).lower() == "true"
    if enabled_only and hasattr(model, "status"):
        query = query.filter(model.status == "ENABLED")
    elif status and hasattr(model, "status"):
        query = query.filter(model.status == status)
    if keyword and search_columns:
        like = f"%{keyword}%"
        query = query.filter(or_(*[column.like(like) for column in search_columns]))
    return query


def _pick(payload: dict, model) -> dict:
    columns = {column.key for column in model.__mapper__.columns}
    ignored = {"id", "created_at", "created_by", "updated_at", "updated_by", "deleted", "tenant_id"}
    return {key: value for key, value in payload.items() if key in columns and key not in ignored}


def _template_item_payload(payload: dict) -> dict:
    data = _pick(payload, AssessmentTemplateItem)
    sheet = data.get("sheet_name") or "-"
    category = data.get("category") or "-"
    subcategory = data.get("subcategory") or "-"
    data["category_id"] = data.get("category_id") or "|".join([sheet, category, subcategory])
    return data


def _replace_template_items(session, template: AssessmentTemplate, items: list[dict]) -> None:
    if not items:
        template.item_count = _sync_template_item_count(session, template)
        return
    session.query(AssessmentTemplateItem).filter(
        AssessmentTemplateItem.template_id == template.id,
        AssessmentTemplateItem.deleted.is_(False),
    ).update({"deleted": True})
    for index, item in enumerate(items, start=1):
        payload = _template_item_payload(item)
        payload["sort_order"] = payload.get("sort_order") or index
        session.add(AssessmentTemplateItem(template_id=template.id, **payload))
    template.item_count = len(items)


def _sync_template_item_count(session, template: AssessmentTemplate) -> int:
    count = session.query(AssessmentTemplateItem).filter(
        AssessmentTemplateItem.template_id == template.id,
        AssessmentTemplateItem.deleted.is_(False),
    ).count()
    template.item_count = count
    return count


def _template_items(session, template_id: str) -> list[AssessmentTemplateItem]:
    return (
        session.query(AssessmentTemplateItem)
        .filter(AssessmentTemplateItem.template_id == template_id, AssessmentTemplateItem.deleted.is_(False))
        .order_by(AssessmentTemplateItem.sort_order.asc())
        .all()
    )


def _get_template_item(session, template_id: str, item_id: str) -> AssessmentTemplateItem:
    item = (
        session.query(AssessmentTemplateItem)
        .filter(
            AssessmentTemplateItem.id == item_id,
            AssessmentTemplateItem.template_id == template_id,
            AssessmentTemplateItem.deleted.is_(False),
        )
        .first()
    )
    if not item:
        raise NotFoundError("Assessment template item not found.")
    return item


def _serialize_template_item(item: AssessmentTemplateItem) -> dict:
    return item.to_dict()


def _replace_score_ranges(session, model: ScoreModel, ranges: list[dict]) -> None:
    session.query(ScoreModelRange).filter(
        ScoreModelRange.score_model_id == model.id,
        ScoreModelRange.deleted.is_(False),
    ).update({"deleted": True})
    for item in ranges:
        session.add(
            ScoreModelRange(
                score_model_id=model.id,
                level=item["level"],
                min_score=float(item.get("min", item.get("min_score"))),
                max_score=float(item.get("max", item.get("max_score"))),
                include_min=bool(item.get("include_min", item.get("includeMin", True))),
                include_max=bool(item.get("include_max", item.get("includeMax", False))),
            )
        )


def _score_ranges(session, model_id: str) -> list[ScoreModelRange]:
    return (
        session.query(ScoreModelRange)
        .filter(ScoreModelRange.score_model_id == model_id, ScoreModelRange.deleted.is_(False))
        .order_by(ScoreModelRange.min_score.asc())
        .all()
    )


def _serialize_score_model(model: ScoreModel, include_ranges: bool = False) -> dict:
    data = model.to_dict()
    if include_ranges:
        data["possibilityRanges"] = [_serialize_range(row) for row in _score_ranges(SessionLocal(), model.id)]
    return data


def _serialize_range(row: ScoreModelRange) -> dict:
    return {
        "id": row.id,
        "level": row.level,
        "min": row.min_score,
        "max": row.max_score,
        "includeMin": row.include_min,
        "includeMax": row.include_max,
    }


def _range_to_payload(row: ScoreModelRange) -> dict:
    return {
        "level": row.level,
        "min": row.min_score,
        "max": row.max_score,
        "include_min": row.include_min,
        "include_max": row.include_max,
    }


def _serialize_harm_model(model: HarmModel, include_rules: bool = False) -> dict:
    data = model.to_dict()
    if include_rules:
        session = SessionLocal()
        rules = (
            session.query(HarmModelRule)
            .filter(HarmModelRule.harm_model_id == model.id, HarmModelRule.deleted.is_(False))
            .order_by(HarmModelRule.sort_order.asc())
            .all()
        )
        data["rules"] = [rule.to_dict() for rule in rules]
    return data


def _get_harm_rule(session, model_id: str, rule_id: str) -> HarmModelRule:
    rule = (
        session.query(HarmModelRule)
        .filter(HarmModelRule.id == rule_id, HarmModelRule.harm_model_id == model_id, HarmModelRule.deleted.is_(False))
        .first()
    )
    if not rule:
        raise NotFoundError("Harm model rule not found.")
    return rule


def _delete_referenced_or_logical(model, record_id: str, project_field, action: str) -> dict:
    session = SessionLocal()
    record = _get(session, model, record_id)
    before = record.to_dict()
    referenced = session.query(Project).filter(project_field == record_id, Project.deleted.is_(False)).first() is not None
    if referenced:
        record.status = "DISABLED"
        result = {"id": record.id, "deleted": False, "disabledDueToReference": True}
        after = {"status": "DISABLED", "disabledDueToReference": True}
    else:
        record.deleted = True
        result = {"id": record.id, "deleted": True, "disabledDueToReference": False}
        after = {"deleted": True}
    audit(action, model.__name__, record.id, before=before, after=after)
    session.commit()
    return result
