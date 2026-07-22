from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.common.exceptions import BusinessError, NotFoundError
from app.extensions import SessionLocal
from app.models import HarmModel, HarmModelRule, Project, ProjectRiskSummaryRecord, RiskMatrix
from app.services.audit_service import audit
from app.services import llm_gateway_service
from app.services.project_service import get_project
from app.services.seed_service import DEFAULT_RISK_MATRIX, ELECTRIC_HARM_LEVEL_RULES, ELECTRIC_HARM_RULE_CONFIG


logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_CATEGORY = "MANAGEMENT_INFO_SYSTEM"
DEFAULT_IMPACT_OBJECT = "LEGAL_RIGHTS"
DEFAULT_DAMAGE_DEGREE = "GENERAL"
DEFAULT_CONFIDENCE = 0.68
EMPTY_LEVELS = {None, "", "-"}
HARM_LEVEL_ALIASES = {
    "MAJOR": "VERY_HIGH",
    "VERY_HIGH": "VERY_HIGH",
    "很高": "VERY_HIGH",
    "HIGH": "HIGH",
    "高": "HIGH",
    "RELATIVELY_HIGH": "RELATIVELY_HIGH",
    "较高": "RELATIVELY_HIGH",
    "MEDIUM": "MEDIUM",
    "中": "MEDIUM",
    "LOW": "LOW",
    "低": "LOW",
    "SLIGHT": "LOW",
}
POSSIBILITY_LEVEL_ALIASES = {
    "HIGH": "HIGH",
    "高": "HIGH",
    "MEDIUM": "MEDIUM",
    "中": "MEDIUM",
    "LOW": "LOW",
    "低": "LOW",
}
RESULT_NAMES = {
    "COMPLIANT": "符合",
    "PARTIAL": "基本符合",
    "NON_COMPLIANT": "不符合",
    "NOT_APPLICABLE": "不适用",
}
HARM_LEVEL_NAMES = {
    "VERY_HIGH": "很高",
    "HIGH": "高",
    "RELATIVELY_HIGH": "较高",
    "MEDIUM": "中",
    "LOW": "低",
}


def suggest(project_id: str, risk_item_id: str, payload: dict | None = None) -> dict:
    """为单条风险清单生成危害程度建议；只返回建议，不写入正式风险数据。"""
    logger.info("开始生成单条风险危害程度建议。project_id=%s risk_item_id=%s", project_id, risk_item_id)
    project = get_project(project_id)
    session = SessionLocal()
    record = _get_risk_record(session, project_id, risk_item_id)
    logger.info(
        "已读取风险行上下文。project_id=%s risk_item_id=%s assessment_category=%s assessment_subcategory=%s evaluation_result=%s",
        project_id,
        risk_item_id,
        record.assessment_category,
        record.assessment_subcategory,
        record.evaluation_result,
    )
    suggestion = _build_suggestion(session, project, record, payload or {})
    logger.info(
        "单条风险危害程度建议生成完成。project_id=%s risk_item_id=%s harm_level=%s llm_status=%s needs_review=%s",
        project_id,
        risk_item_id,
        suggestion.get("harmLevel"),
        suggestion.get("llmStatus"),
        suggestion.get("needsManualReview"),
    )
    return suggestion


def suggest_batch(project_id: str, payload: dict | None = None) -> dict:
    """为多条当前风险行批量生成危害程度建议；只返回建议，不做写入。"""
    project = get_project(project_id)
    payload = payload or {}
    session = SessionLocal()
    risk_item_ids = payload.get("risk_item_ids") or payload.get("risk_record_ids") or []
    logger.info(
        "开始批量生成风险危害程度建议。project_id=%s 指定风险行数量=%s limit=%s",
        project_id,
        len(risk_item_ids),
        payload.get("limit"),
    )
    query = session.query(ProjectRiskSummaryRecord).filter(
        ProjectRiskSummaryRecord.project_id == project_id,
        ProjectRiskSummaryRecord.deleted.is_(False),
        ProjectRiskSummaryRecord.current.is_(True),
    )
    if risk_item_ids:
        logger.info("批量建议限定指定风险行。project_id=%s risk_item_ids=%s", project_id, risk_item_ids)
        query = query.filter(ProjectRiskSummaryRecord.id.in_(risk_item_ids))
    rows = query.order_by(ProjectRiskSummaryRecord.created_at.asc()).all()
    logger.info("批量建议查询到当前风险行。project_id=%s row_count=%s", project_id, len(rows))
    limit = payload.get("limit")
    if limit:
        rows = rows[: int(limit)]
        logger.info("批量建议按 limit 截断风险行。project_id=%s limit=%s truncated_count=%s", project_id, limit, len(rows))
    suggestions = [_build_suggestion(session, project, row, payload) for row in rows]
    logger.info("批量风险危害程度建议生成完成。project_id=%s total=%s", project_id, len(suggestions))
    return {"suggestions": suggestions, "total": len(suggestions)}


def apply(project_id: str, risk_item_id: str, payload: dict | None = None) -> dict:
    """将用户确认后的危害程度建议应用到正式风险清单。"""
    logger.info("开始应用用户确认的风险危害程度建议。project_id=%s risk_item_id=%s", project_id, risk_item_id)
    # 第一步：读取项目和风险行，确保只能修改当前项目下的当前风险记录。
    project = get_project(project_id)
    payload = payload or {}
    session = SessionLocal()
    record = _get_risk_record(session, project_id, risk_item_id)
    target_records = _target_risk_records(session, project_id, risk_item_id, payload)

    # 第二步：读取前端传回的建议；如果没有传建议，则现场重新生成一次建议。
    # request_json 会把前端 camelCase 转为 snake_case，但服务内部实时生成的建议仍是 camelCase，
    # 因此后续统一通过 _payload_value 兼容两种字段名。
    suggestion_payload = payload.get("suggestion") or payload
    if not suggestion_payload or not _payload_value(suggestion_payload, "harm_level", "harmLevel"):
        logger.info("应用接口未收到完整建议，准备实时生成建议后应用。project_id=%s risk_item_id=%s", project_id, risk_item_id)
        suggestion_payload = _build_suggestion(session, project, record, payload)

    # 第三步：校验输入 hash，避免风险描述或现场测评信息变化后仍应用旧建议。
    current_hash = _analysis_input_hash(project, record)
    provided_hash = _payload_value(suggestion_payload, "harm_analysis_input_hash", "harmAnalysisInputHash", "input_hash", "inputHash")
    logger.info(
        "应用建议前校验输入摘要。project_id=%s risk_item_id=%s provided_hash=%s current_hash=%s",
        project_id,
        risk_item_id,
        _short_hash(provided_hash),
        _short_hash(current_hash),
    )
    if provided_hash and provided_hash != current_hash:
        logger.warning("拒绝应用危害程度建议：建议已过期。project_id=%s risk_item_id=%s", project_id, risk_item_id)
        raise BusinessError("HARM_ANALYSIS_STALE", "风险行在建议生成后已发生变化，请重新生成危害程度建议。")

    if _payload_value(suggestion_payload, "needs_manual_review", "needsManualReview"):
        logger.warning("拒绝应用危害程度建议：模型标记需要人工复核。project_id=%s risk_item_id=%s", project_id, risk_item_id)
        raise BusinessError("HARM_ANALYSIS_REVIEW_REQUIRED", "该危害程度建议需要人工复核，不能直接应用。")

    harm_level = _normalize_harm_level(_payload_value(suggestion_payload, "harm_level", "harmLevel"))
    if not harm_level:
        logger.warning(
            "拒绝应用危害程度建议：危害程度等级无效。project_id=%s risk_item_id=%s raw_harm_level=%s",
            project_id,
            risk_item_id,
            _payload_value(suggestion_payload, "harm_level", "harmLevel"),
        )
        raise BusinessError("HARM_ANALYSIS_INVALID", "危害程度建议必须包含有效的危害程度等级。")

    # 第四步：把用户确认后的危害程度闭环字段写入正式风险清单。
    logger.info(
        "开始写入危害程度字段。project_id=%s risk_item_id=%s harm_level=%s impact_object=%s confidence=%s",
        project_id,
        risk_item_id,
        harm_level,
        _payload_value(suggestion_payload, "harm_impact_object", "harmImpactObject"),
        _payload_value(suggestion_payload, "confidence"),
    )
    for target_record in target_records:
        before = target_record.to_dict()
        _apply_harm_confirmation(session, project, target_record, suggestion_payload, harm_level, current_hash)
        target_record.manual_adjusted = True
        # 记录审计日志，便于后续追踪“谁在什么输入下确认了哪条危害程度建议”。
        audit("HARM_ANALYSIS_APPLY", "ProjectRiskSummaryRecord", target_record.id, before=before, after=target_record.to_dict())
    logger.info(
        "危害程度字段写入数据库前审计记录已生成。project_id=%s risk_item_id=%s target_count=%s",
        project_id,
        risk_item_id,
        len(target_records),
    )
    session.commit()
    logger.info("危害程度字段已提交到数据库。project_id=%s risk_item_id=%s", project_id, risk_item_id)

    from app.services.risk_service import serialize_risk_item

    # 返回风险清单统一序列化结果，保证前端看到的是正式风险行的最新状态。
    primary_record = _primary_target_record(target_records, risk_item_id)
    logger.info(
        "用户确认的风险危害程度建议应用完成。project_id=%s risk_item_id=%s harm_level=%s risk_level=%s",
        project_id,
        risk_item_id,
        primary_record.harm_level,
        primary_record.risk_level,
    )
    return serialize_risk_item(primary_record)


def risk_level_from_project_matrix(session, project: Project, harm_level: str | None, possibility_level: str | None) -> str | None:
    """根据项目选中的风险评价矩阵，由危害程度和发生可能性查出最终风险等级。"""
    harm = _normalize_harm_level(harm_level)
    possibility = _normalize_possibility_level(possibility_level)
    if not harm or possibility not in {"HIGH", "MEDIUM", "LOW"}:
        logger.info(
            "跳过风险矩阵匹配：危害程度或发生可能性无效。project_id=%s harm_level=%s possibility_level=%s",
            project.id,
            harm_level,
            possibility_level,
        )
        return None
    matrix_json = DEFAULT_RISK_MATRIX
    if project.risk_matrix_id:
        matrix = session.get(RiskMatrix, project.risk_matrix_id)
        if matrix and not matrix.deleted and isinstance(matrix.matrix_json, dict):
            matrix_json = matrix.matrix_json
            logger.info("使用项目选择的知识库风险评价矩阵进行匹配。project_id=%s risk_matrix_id=%s", project.id, project.risk_matrix_id)
        else:
            logger.warning("项目风险评价矩阵不可用，使用默认矩阵。project_id=%s risk_matrix_id=%s", project.id, project.risk_matrix_id)
    else:
        logger.info("项目未配置风险评价矩阵，使用默认矩阵。project_id=%s", project.id)
    row = matrix_json.get(possibility) or {}
    risk_level = row.get(harm)
    logger.info(
        "风险矩阵匹配完成。project_id=%s harm_level=%s possibility_level=%s risk_level=%s",
        project.id,
        harm,
        possibility,
        risk_level,
    )
    return risk_level


def _apply_harm_confirmation(
    session,
    project: Project,
    record: ProjectRiskSummaryRecord,
    suggestion_payload: dict,
    harm_level: str,
    input_hash: str,
) -> None:
    record.harm_level = harm_level
    record.harm_description = _payload_value(suggestion_payload, "harm_description", "harmDescription")
    record.harm_impact_object = _payload_value(suggestion_payload, "harm_impact_object", "harmImpactObject")
    record.harm_example = _payload_value(suggestion_payload, "harm_example", "harmExample")
    record.harm_analysis_trace = _payload_value(suggestion_payload, "harm_analysis_trace", "harmAnalysisTrace") or _trace_from_payload(suggestion_payload)
    record.harm_analysis_confidence = _float_or_default(
        _payload_value(suggestion_payload, "harm_analysis_confidence", "harmAnalysisConfidence", "confidence"),
        None,
    )
    record.harm_analysis_input_hash = input_hash

    # 第五步：如果风险发生可能性已存在，则通过项目风险矩阵同步重算风险等级。
    if record.possibility_level not in EMPTY_LEVELS:
        calculated_risk = risk_level_from_project_matrix(session, project, record.harm_level, record.possibility_level)
        if calculated_risk:
            record.risk_level = calculated_risk
            logger.info(
                "应用危害程度后已重算风险等级。project_id=%s risk_item_id=%s possibility_level=%s risk_level=%s",
                project.id,
                record.id,
                record.possibility_level,
                calculated_risk,
            )
        else:
            logger.warning(
                "应用危害程度后未能通过风险矩阵重算风险等级。project_id=%s risk_item_id=%s harm_level=%s possibility_level=%s",
                project.id,
                record.id,
                record.harm_level,
                record.possibility_level,
            )
    else:
        record.risk_level = None
        logger.info("风险发生可能性为空，应用危害程度后清空风险等级。project_id=%s risk_item_id=%s", project.id, record.id)


def _build_suggestion(session, project: Project, record: ProjectRiskSummaryRecord, payload: dict) -> dict:
    """把大模型/兜底规则输出转换成完整的四步危害程度建议。"""
    logger.info("开始构建危害程度建议闭环。project_id=%s risk_item_id=%s", project.id, record.id)
    # 读取项目选择的危害程度分析模型；没有配置时使用内置电力行业规则。
    rule_config = _rule_config(session, project.harm_model_id)

    # 第一步：把项目 systemType 规范化为危害模型中的系统类别。
    system_category, system_category_name, system_category_review = _system_category(project.system_type, rule_config)

    # 第二步：获取模型输出。优先使用前端传入结果，其次调用 DashScope，最后规则兜底。
    model_output = _model_output(payload, project, record, system_category, rule_config)

    # 第三步：规范化模型输出的侵害客体、侵害程度和置信度。
    impacted_object = _normalize_level(model_output.get("impacted_object")) or DEFAULT_IMPACT_OBJECT
    damage_degree = _normalize_level(model_output.get("damage_degree")) or DEFAULT_DAMAGE_DEGREE
    confidence = _float_or_default(model_output.get("confidence"), DEFAULT_CONFIDENCE)
    logger.info(
        "模型输出已规范化。project_id=%s risk_item_id=%s impacted_object=%s damage_degree=%s confidence=%s llm_status=%s",
        project.id,
        record.id,
        impacted_object,
        damage_degree,
        confidence,
        model_output.get("llm_status"),
    )

    # 第四步：校验该“系统类别 + 侵害客体 + 侵害程度”组合是否被危害模型允许。
    conflicts = _rule_conflicts(rule_config, system_category, impacted_object, damage_degree)
    if system_category_review:
        conflicts.append(system_category_review)

    # 第五步：按规则矩阵匹配数据安全保护等级，再映射到风险危害程度等级。
    protection_level = _protection_level(rule_config, impacted_object, damage_degree)
    harm_level = _harm_level(rule_config, protection_level)
    harm_level_name = _harm_level_name(harm_level)

    # 第六步：读取等级示例，并结合当前风险行和模型结论生成逐条风险专属的危害程度分析。
    level_rule = _level_rule(session, project.harm_model_id, harm_level)
    object_name = _object_name(rule_config, impacted_object)
    damage_name = _damage_name(rule_config, damage_degree)
    harm_description = _specific_harm_analysis(record, model_output, object_name, damage_name, harm_level_name)
    harm_example = _damage_example(rule_config, impacted_object, damage_degree) or (level_rule.get("example") if level_rule else None)
    needs_review = bool(model_output.get("needs_manual_review")) or bool(conflicts) or not harm_level
    if conflicts:
        logger.warning(
            "危害程度建议存在规则冲突，需要人工复核。project_id=%s risk_item_id=%s conflicts=%s",
            project.id,
            record.id,
            conflicts,
        )
    logger.info(
        "危害程度四步规则映射完成。project_id=%s risk_item_id=%s system_category=%s impacted_object=%s damage_degree=%s protection_level=%s harm_level=%s",
        project.id,
        record.id,
        system_category,
        impacted_object,
        damage_degree,
        protection_level,
        harm_level,
    )

    # 第七步：生成用户可读的四步判定过程；机器字段保留在 trace 中供前端调试或折叠展示。
    # 第二步需要让前端解释“模型为什么这样判”，因此单独返回结论、原因和依据列表。
    step2_reason = model_output.get("reason") or _default_step2_reason(object_name, damage_name)
    step2_evidence = model_output.get("evidence") or []
    step2_basis = _step2_basis(record, system_category_name, step2_evidence)
    logger.info(
        "第二步受侵害客体及侵害程度判定说明已生成。project_id=%s risk_item_id=%s basis_count=%s evidence_count=%s",
        project.id,
        record.id,
        len(step2_basis),
        len(step2_evidence),
    )
    trace = {
        "step1": f"被评估对象系统类别为{system_category_name}，按该类别约束可侵害客体与侵害程度。",
        "step2": f"判定优先受侵害客体为{object_name}，侵害程度为{damage_name}。",
        "step2Reason": step2_reason,
        "step2Basis": step2_basis,
        "step2Evidence": step2_evidence,
        "step3": f"按侵害客体和侵害程度匹配数据安全保护等级：第{protection_level}级。" if protection_level else "未能匹配到数据安全保护等级。",
        "step4": f"按保护等级匹配风险危害程度等级：{harm_level_name}。" if harm_level else "未能匹配到风险危害程度等级。",
        "systemCategory": system_category,
        "impactedObject": impacted_object,
        "damageDegree": damage_degree,
        "dataSecurityProtectionLevel": protection_level,
        "harmLevel": harm_level,
        "harmLevelName": harm_level_name,
        "reason": step2_reason,
        "evidence": step2_evidence,
        "conflicts": conflicts,
        "ruleId": level_rule.get("id") if level_rule else None,
        "llmStatus": model_output.get("llm_status"),
        "llmProvider": model_output.get("llm_provider"),
        "llmModel": model_output.get("llm_model"),
        "llmError": model_output.get("llm_error"),
    }

    return {
        "riskRecordId": record.id,
        "riskItemId": record.id,
        "systemCategory": system_category,
        "impactedObject": impacted_object,
        "damageDegree": damage_degree,
        "dataSecurityProtectionLevel": protection_level,
        "harmLevel": harm_level,
        "harmLevelName": harm_level_name,
        "harmDescription": harm_description,
        "harmImpactObject": object_name,
        "harmExample": harm_example,
        "harmAnalysisTrace": trace,
        "harmAnalysisConfidence": confidence,
        "harmAnalysisInputHash": _analysis_input_hash(project, record),
        "reason": step2_reason,
        "evidence": step2_evidence,
        "confidence": confidence,
        "llmStatus": model_output.get("llm_status"),
        "llmProvider": model_output.get("llm_provider"),
        "llmModel": model_output.get("llm_model"),
        "llmError": model_output.get("llm_error"),
        "needsManualReview": needs_review,
        "reviewReasons": conflicts,
    }


def _get_risk_record(session, project_id: str, risk_item_id: str) -> ProjectRiskSummaryRecord:
    """在项目范围内查询当前有效的风险汇总记录。"""
    record = (
        session.query(ProjectRiskSummaryRecord)
        .filter(
            ProjectRiskSummaryRecord.id == risk_item_id,
            ProjectRiskSummaryRecord.project_id == project_id,
            ProjectRiskSummaryRecord.deleted.is_(False),
            ProjectRiskSummaryRecord.current.is_(True),
        )
        .first()
    )
    if not record:
        logger.warning("未找到当前有效风险汇总记录。project_id=%s risk_item_id=%s", project_id, risk_item_id)
        raise NotFoundError("未找到当前有效的风险汇总记录。")
    return record


def _target_risk_records(session, project_id: str, risk_item_id: str, payload: dict) -> list[ProjectRiskSummaryRecord]:
    target_ids = _merged_target_ids(payload)
    if not target_ids:
        return [_get_risk_record(session, project_id, risk_item_id)]

    rows = (
        session.query(ProjectRiskSummaryRecord)
        .filter(
            ProjectRiskSummaryRecord.id.in_(target_ids),
            ProjectRiskSummaryRecord.project_id == project_id,
            ProjectRiskSummaryRecord.deleted.is_(False),
            ProjectRiskSummaryRecord.current.is_(True),
        )
        .all()
    )
    rows_by_id = {row.id: row for row in rows}
    if len(rows_by_id) != len(target_ids):
        logger.warning(
            "合并行危害程度应用包含无效源记录。project_id=%s risk_item_id=%s merged_ids=%s",
            project_id,
            risk_item_id,
            target_ids,
        )
        raise NotFoundError("未找到当前有效的风险汇总记录。")
    return [rows_by_id[target_id] for target_id in target_ids]


def _merged_target_ids(payload: dict) -> list[str]:
    raw_ids = payload.get("merged_risk_record_ids", payload.get("mergedRiskRecordIds"))
    if raw_ids in (None, "", []):
        return []
    if not isinstance(raw_ids, (list, tuple, set)):
        raise BusinessError("INVALID_MERGED_RISK_RECORD_IDS", "mergedRiskRecordIds must be an array.")
    target_ids = []
    seen = set()
    for raw_id in raw_ids:
        target_id = _clean_identifier(raw_id)
        if target_id and target_id not in seen:
            seen.add(target_id)
            target_ids.append(target_id)
    return target_ids


def _primary_target_record(records: list[ProjectRiskSummaryRecord], risk_item_id: str) -> ProjectRiskSummaryRecord:
    for record in records:
        if record.id == risk_item_id:
            return record
    return records[0]


def _clean_identifier(value: Any) -> str:
    return " ".join(str(value or "").split())


def _rule_config(session, harm_model_id: str | None) -> dict:
    """读取项目选择的危害模型规则；如果不存在或未配置规则，则使用内置电力行业规则。"""
    if harm_model_id:
        model = session.get(HarmModel, harm_model_id)
        if model and not model.deleted and isinstance(model.rule_config, dict) and model.rule_config:
            logger.info("已加载项目配置的危害模型规则。harm_model_id=%s", harm_model_id)
            return model.rule_config
        logger.warning("项目配置的危害模型不可用，使用内置规则兜底。harm_model_id=%s", harm_model_id)
    else:
        logger.info("项目未配置危害模型，使用内置规则兜底。")
    return ELECTRIC_HARM_RULE_CONFIG


def _system_category(system_type: str | None, rule_config: dict) -> tuple[str, str, str | None]:
    """把项目系统类别映射为危害模型可识别的系统类别编码。"""
    aliases = rule_config.get("system_type_aliases") or {}
    raw = str(system_type or "").strip()
    normalized = aliases.get(raw) or aliases.get(raw.upper()) or raw.upper()
    categories = rule_config.get("system_categories") or {}
    if normalized not in categories:
        fallback = DEFAULT_SYSTEM_CATEGORY if DEFAULT_SYSTEM_CATEGORY in categories else next(iter(categories.keys()), DEFAULT_SYSTEM_CATEGORY)
        name = (categories.get(fallback) or {}).get("name") or fallback
        logger.warning("项目系统类别未命中危害模型配置，使用默认类别。raw_system_type=%s fallback=%s", raw or "-", fallback)
        return fallback, name, f"项目系统类别 {raw or '-'} 未在危害模型中配置，已按 {name} 兜底。"
    name = (categories.get(normalized) or {}).get("name") or normalized
    logger.info("项目系统类别映射完成。raw_system_type=%s normalized=%s display_name=%s", raw or "-", normalized, name)
    return normalized, name, None


def _model_output(
    payload: dict,
    project: Project,
    record: ProjectRiskSummaryRecord,
    system_category: str,
    rule_config: dict,
) -> dict:
    """获取危害程度分析的原始判断结果：前端传入优先，其次调用 DashScope，最后使用规则兜底。"""
    provided = payload.get("model_output") or payload.get("llm_result")
    if provided:
        logger.info("使用前端传入的大模型结果进行危害程度分析。project_id=%s risk_item_id=%s", project.id, record.id)
        result = _normalize_model_output(provided)
        result["llm_status"] = "PROVIDED"
        return result
    if not payload.get("disable_llm"):
        try:
            logger.info("准备调用外部大模型进行危害程度分析。project_id=%s risk_item_id=%s", project.id, record.id)
            llm_result = llm_gateway_service.suggest_harm_analysis(project, record, system_category, rule_config)
            if llm_result:
                logger.info("外部大模型已返回危害程度分析结果。project_id=%s risk_item_id=%s", project.id, record.id)
                return _normalize_model_output(llm_result)
            logger.info("外部大模型未启用或未配置，准备使用规则兜底。project_id=%s risk_item_id=%s", project.id, record.id)
        except Exception as exc:
            logger.warning(
                "外部大模型调用失败，改用规则兜底进行危害程度分析。project_id=%s risk_item_id=%s error=%s",
                project.id,
                record.id,
                exc,
            )
            fallback = _heuristic_model_output(project, record, system_category, rule_config)
            fallback["llm_status"] = llm_gateway_service.LLM_FALLBACK
            fallback["llm_error"] = str(exc)
            return fallback
    logger.info("本次请求禁用外部大模型，使用规则兜底进行危害程度分析。project_id=%s risk_item_id=%s", project.id, record.id)
    fallback = _heuristic_model_output(project, record, system_category, rule_config)
    fallback["llm_status"] = llm_gateway_service.LLM_DISABLED
    return fallback


def _normalize_model_output(value: dict) -> dict:
    """把大模型输出的 snake_case/camelCase 字段统一为内部字段格式。"""
    if not isinstance(value, dict):
        logger.warning("大模型输出格式非法：不是 JSON 对象。value_type=%s", type(value).__name__)
        raise BusinessError("HARM_ANALYSIS_INVALID_MODEL_OUTPUT", "大模型输出必须是 JSON 对象。")
    logger.info(
        "开始规范化大模型输出。impacted_object=%s impactedObject=%s damage_degree=%s damageDegree=%s",
        value.get("impacted_object"),
        value.get("impactedObject"),
        value.get("damage_degree"),
        value.get("damageDegree"),
    )
    return {
        "impacted_object": _normalize_level(value.get("impacted_object") or value.get("impactedObject")),
        "damage_degree": _normalize_level(value.get("damage_degree") or value.get("damageDegree")),
        "reason": value.get("reason"),
        "harm_analysis": (
            value.get("harm_analysis")
            or value.get("harmAnalysis")
            or value.get("harm_description")
            or value.get("harmDescription")
        ),
        "evidence": value.get("evidence") or [],
        "confidence": _float_or_default(value.get("confidence"), DEFAULT_CONFIDENCE),
        "needs_manual_review": bool(value.get("needs_manual_review") or value.get("needsManualReview")),
        "llm_status": value.get("llm_status") or value.get("llmStatus"),
        "llm_provider": value.get("llm_provider") or value.get("llmProvider"),
        "llm_model": value.get("llm_model") or value.get("llmModel"),
        "llm_error": value.get("llm_error") or value.get("llmError"),
    }


def _heuristic_model_output(project: Project, record: ProjectRiskSummaryRecord, system_category: str, rule_config: dict) -> dict:
    """当外部大模型不可用或被禁用时，根据关键词和现场测评上下文进行规则兜底判断。"""
    text = _analysis_text(project, record)
    severe_result = record.evaluation_result == "NON_COMPLIANT"
    logger.info(
        "开始规则兜底判断危害程度。project_id=%s risk_item_id=%s system_category=%s evaluation_result=%s",
        project.id,
        record.id,
        system_category,
        record.evaluation_result,
    )
    impact_object = DEFAULT_IMPACT_OBJECT
    if any(keyword in text for keyword in ["国家安全", "社会动荡", "电网瓦解", "省市", "大部分地区"]):
        impact_object = "NATIONAL_SECURITY"
    elif any(keyword in text for keyword in ["社会秩序", "公共利益", "公众", "电力生产", "供应中断", "服务中断", "地市", "灾备", "恢复", "应急", "连续性"]):
        impact_object = "PUBLIC_INTEREST"

    damage_degree = DEFAULT_DAMAGE_DEGREE
    if any(keyword in text for keyword in ["特别严重", "重大", "社会动荡", "巨大经济损失", "大部分地区"]):
        damage_degree = "EXTREMELY_SERIOUS"
    elif any(keyword in text for keyword in ["严重", "大量", "中断", "重要", "脱库", "篡改", "丢失", "泄露"]):
        damage_degree = "SERIOUS"
    elif severe_result and any(keyword in text for keyword in ["核心", "关键", "备份", "恢复", "权限", "账号", "脱敏", "监测", "审计"]):
        damage_degree = "SERIOUS"

    allowed = _allowed_degrees(rule_config, system_category, impact_object)
    if allowed and damage_degree not in allowed:
        logger.info(
            "规则兜底结果超出系统类别允许范围，按允许范围降级。project_id=%s risk_item_id=%s original_damage_degree=%s allowed=%s",
            project.id,
            record.id,
            damage_degree,
            allowed,
        )
        damage_degree = allowed[-1]
    logger.info(
        "规则兜底判断完成。project_id=%s risk_item_id=%s impacted_object=%s damage_degree=%s",
        project.id,
        record.id,
        impact_object,
        damage_degree,
    )
    return {
        "impacted_object": impact_object,
        "damage_degree": damage_degree,
        "reason": _heuristic_reason(record, rule_config, impact_object, damage_degree),
        "evidence": _evidence(record),
        "confidence": DEFAULT_CONFIDENCE,
        "needs_manual_review": False,
    }


def _specific_harm_analysis(
    record: ProjectRiskSummaryRecord,
    model_output: dict,
    object_name: str,
    damage_name: str,
    harm_level_name: str | None,
) -> str:
    """优先使用模型的逐条分析；旧模型未返回时根据本行事实生成专属分析。"""
    model_analysis = " ".join(str(model_output.get("harm_analysis") or "").split())
    if model_analysis:
        return model_analysis if len(model_analysis) <= 500 else model_analysis[:500].rstrip() + "…"

    problem = _first_analysis_fragment(
        record.risk_source_description,
        record.evaluation_record,
        record.check_point,
        record.risk_description,
        max_length=100,
    )
    consequence = _analysis_fragment(record.risk_description, 120)
    related_data = _analysis_fragment(record.related_data, 100) if _has_text(record.related_data) else None
    related_activities = _analysis_fragment(_joined_text(record.related_activities), 80) if _has_text(record.related_activities) else None
    reason = _analysis_fragment(model_output.get("reason"), 160)

    sentences = []
    if problem:
        sentences.append(f"当前风险行的具体问题是“{problem}”。")
    else:
        sentences.append("当前风险行尚未提供可定位到具体控制缺陷的现场事实。")

    if related_data and related_activities:
        sentences.append(f"该问题涉及“{related_data}”在“{related_activities}”环节的处理。")
    elif related_data:
        sentences.append(f"该问题涉及“{related_data}”，但尚未明确具体数据处理活动。")
    elif related_activities:
        sentences.append(f"该问题发生在“{related_activities}”环节，但尚未明确涉及的数据及类型、级别。")
    else:
        sentences.append("涉及的数据及类型、级别和数据处理活动尚未明确，当前结论需结合现有风险事实审慎判断。")

    if consequence and consequence != problem:
        sentences.append(f"若该问题未得到控制，可能出现“{consequence}”所述的数据安全后果。")
    if reason:
        sentences.append(f"分析依据为：{reason.rstrip('。')}。")
    level_text = harm_level_name or "待复核"
    sentences.append(f"综合判断，风险优先影响{object_name}，侵害程度为{damage_name}，本条风险的危害程度为{level_text}。")
    return "".join(sentences)


def _heuristic_reason(record: ProjectRiskSummaryRecord, rule_config: dict, impact_object: str, damage_degree: str) -> str:
    """用当前风险行事实生成规则兜底原因，避免所有风险共用同一句说明。"""
    fact = _first_analysis_fragment(
        record.risk_source_description,
        record.evaluation_record,
        record.risk_description,
        record.check_point,
        max_length=100,
    ) or "未提供具体现场事实"
    scope_parts = []
    if _has_text(record.related_data):
        scope_parts.append(f"涉及数据为{_analysis_fragment(record.related_data, 80)}")
    if _has_text(record.related_activities):
        scope_parts.append(f"处理活动为{_analysis_fragment(_joined_text(record.related_activities), 60)}")
    scope = f"，{'、'.join(scope_parts)}" if scope_parts else "，涉及数据和处理活动尚未明确"
    return (
        f"根据本行事实“{fact}”{scope}，结合风险类型及可能造成的泄露、丢失、篡改或业务中断后果，"
        f"规则判断优先受影响对象为{_object_name(rule_config, impact_object)}，侵害程度为{_damage_name(rule_config, damage_degree)}"
    )


def _first_analysis_fragment(*values: Any, max_length: int) -> str | None:
    for value in values:
        if _has_text(value):
            return _analysis_fragment(value, max_length)
    return None


def _analysis_fragment(value: Any, max_length: int) -> str:
    text = " ".join(str(value or "").split()).strip("；;。 ")
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip() + "…"


def _has_text(value: Any) -> bool:
    if isinstance(value, (list, tuple, set)):
        return any(_has_text(item) for item in value)
    return str(value or "").strip() not in {"", "-"}


def _joined_text(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return "、".join(str(item).strip() for item in value if _has_text(item))
    return str(value or "").strip()


def _rule_conflicts(rule_config: dict, system_category: str, impact_object: str, damage_degree: str) -> list[str]:
    """校验模型输出是否符合危害模型规则约束。"""
    conflicts = []
    if impact_object not in (rule_config.get("object_names") or {}):
        conflicts.append(f"侵害客体 {impact_object} 不在危害模型中。")
    if damage_degree not in (rule_config.get("damage_degree_names") or {}):
        conflicts.append(f"侵害程度 {damage_degree} 不在危害模型中。")
    allowed = _allowed_degrees(rule_config, system_category, impact_object)
    if damage_degree not in allowed:
        conflicts.append(f"系统类别 {system_category} 不允许 {impact_object}/{damage_degree} 的组合。")
    if conflicts:
        logger.warning(
            "危害模型规则校验发现冲突。system_category=%s impact_object=%s damage_degree=%s conflicts=%s",
            system_category,
            impact_object,
            damage_degree,
            conflicts,
        )
    else:
        logger.info(
            "危害模型规则校验通过。system_category=%s impact_object=%s damage_degree=%s",
            system_category,
            impact_object,
            damage_degree,
        )
    return conflicts


def _allowed_degrees(rule_config: dict, system_category: str, impact_object: str) -> list[str]:
    """获取某个系统类别和侵害客体允许出现的侵害程度列表。"""
    category = (rule_config.get("system_categories") or {}).get(system_category) or {}
    impact_degrees = category.get("impact_degrees") or {}
    return impact_degrees.get(impact_object) or []


def _protection_level(rule_config: dict, impact_object: str, damage_degree: str) -> int | None:
    """根据侵害客体和侵害程度匹配数据安全保护等级。"""
    matrix = rule_config.get("protection_level_matrix") or {}
    row = matrix.get(impact_object) or {}
    value = row.get(damage_degree)
    if value is None:
        logger.warning("未匹配到数据安全保护等级。impact_object=%s damage_degree=%s", impact_object, damage_degree)
        return None
    try:
        protection_level = int(value)
        logger.info(
            "已匹配数据安全保护等级。impact_object=%s damage_degree=%s protection_level=%s",
            impact_object,
            damage_degree,
            protection_level,
        )
        return protection_level
    except (TypeError, ValueError):
        logger.warning("数据安全保护等级配置非法。impact_object=%s damage_degree=%s raw_value=%s", impact_object, damage_degree, value)
        return None


def _harm_level(rule_config: dict, protection_level: int | None) -> str | None:
    """根据数据安全保护等级匹配风险危害程度等级。"""
    if protection_level is None:
        logger.warning("保护等级为空，无法匹配风险危害程度等级。")
        return None
    level = (rule_config.get("harm_level_by_protection_level") or {}).get(str(protection_level))
    harm_level = _normalize_harm_level(level)
    logger.info("已匹配风险危害程度等级。protection_level=%s raw_level=%s harm_level=%s", protection_level, level, harm_level)
    return harm_level


def _level_rule(session, harm_model_id: str | None, harm_level: str | None) -> dict | None:
    """读取最终危害程度等级对应的描述、影响对象和示例。"""
    if not harm_level:
        logger.warning("危害程度等级为空，无法读取等级描述规则。")
        return None
    row = None
    if harm_model_id:
        row = (
            session.query(HarmModelRule)
            .filter(
                HarmModelRule.harm_model_id == harm_model_id,
                HarmModelRule.level == harm_level,
                HarmModelRule.deleted.is_(False),
            )
            .order_by(HarmModelRule.sort_order.asc())
            .first()
        )
    if row:
        logger.info("已从数据库读取危害程度等级展示规则。harm_model_id=%s harm_level=%s rule_id=%s", harm_model_id, harm_level, row.id)
        return row.to_dict()
    for item in ELECTRIC_HARM_LEVEL_RULES:
        if item["level"] == harm_level:
            logger.info("数据库未找到等级规则，使用内置展示规则。harm_model_id=%s harm_level=%s", harm_model_id, harm_level)
            return {
                "id": f"builtin-{harm_level.lower()}",
                "level": item["level"],
                "description": item["description"],
                "impactObject": item["impact_object"],
                "example": item["example"],
            }
    logger.warning("未找到危害程度等级展示规则。harm_model_id=%s harm_level=%s", harm_model_id, harm_level)
    return None


def _object_name(rule_config: dict, impact_object: str) -> str:
    """把侵害客体枚举值转换为中文展示名称。"""
    return (rule_config.get("object_names") or {}).get(impact_object) or impact_object


def _damage_name(rule_config: dict, damage_degree: str) -> str:
    """把侵害程度枚举值转换为中文展示名称。"""
    return (rule_config.get("damage_degree_names") or {}).get(damage_degree) or damage_degree


def _damage_example(rule_config: dict, impact_object: str, damage_degree: str) -> str | None:
    """根据侵害客体和侵害程度查找示例文本。"""
    return ((rule_config.get("damage_examples") or {}).get(impact_object) or {}).get(damage_degree)


def _analysis_input_hash(project: Project, record: ProjectRiskSummaryRecord) -> str:
    """对所有判定输入计算摘要，防止风险内容变化后仍应用旧建议。"""
    payload = {
        "projectId": project.id,
        "systemType": project.system_type,
        "harmModelId": project.harm_model_id,
        "riskTypes": record.risk_types or [],
        "assessmentCategory": record.assessment_category,
        "assessmentSubcategory": record.assessment_subcategory,
        "checkPoint": record.check_point,
        "evaluationResult": record.evaluation_result,
        "evaluationRecord": record.evaluation_record,
        "riskDescription": record.risk_description,
        "riskSourceDescription": record.risk_source_description,
        "relatedData": record.related_data,
        "relatedActivities": record.related_activities or [],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    logger.info("已计算危害程度建议输入摘要。project_id=%s risk_item_id=%s input_hash=%s", project.id, record.id, _short_hash(digest))
    return digest


def _analysis_text(project: Project, record: ProjectRiskSummaryRecord) -> str:
    """拼接规则兜底所需的风险字段和现场测评上下文文本。"""
    parts: list[str] = [project.system_type or ""]
    parts.extend(str(item) for item in (record.risk_types or []))
    parts.extend(
        [
            record.assessment_category or "",
            record.assessment_subcategory or "",
            record.check_point or "",
            record.evaluation_result or "",
            record.evaluation_record or "",
            record.risk_description or "",
            record.risk_source_description or "",
            record.related_data or "",
            _joined_text(record.related_activities),
        ]
    )
    text = " ".join(parts)
    logger.info("已构造规则兜底分析文本。project_id=%s risk_item_id=%s text_length=%s", project.id, record.id, len(text))
    return text


def _evidence(record: ProjectRiskSummaryRecord) -> list[str]:
    """提取用于前端展示和模型解释的关键依据。"""
    evidence = []
    if record.risk_types:
        evidence.append(f"风险类型：{', '.join(str(item) for item in record.risk_types)}")
    if record.assessment_category or record.assessment_subcategory:
        evidence.append(f"现场测评分类：{record.assessment_category or '-'} / {record.assessment_subcategory or '-'}")
    if record.check_point:
        evidence.append(f"检查要点：{record.check_point}")
    if record.evaluation_result:
        evidence.append(f"符合情况：{_evaluation_result_name(record.evaluation_result)}")
    if record.evaluation_record:
        evidence.append(f"评估结果：{record.evaluation_record}")
    if record.risk_description:
        evidence.append(f"风险描述：{record.risk_description}")
    if record.risk_source_description:
        evidence.append(f"风险源描述：{record.risk_source_description}")
    if _has_text(record.related_data):
        evidence.append(f"涉及的数据及类型、级别：{record.related_data}")
    if _has_text(record.related_activities):
        evidence.append(f"涉及的数据处理活动：{_joined_text(record.related_activities)}")
    logger.info("已提取危害程度判定依据。risk_item_id=%s evidence_count=%s", record.id, len(evidence))
    return evidence


def _default_step2_reason(object_name: str, damage_name: str) -> str:
    """当模型没有返回理由时，生成第二步可展示的默认判断说明。"""
    reason = (
        f"模型未返回单独理由，后端按规则兜底说明：先按国家安全、社会秩序和公共利益、"
        f"公民法人和其他组织合法权益的优先级识别受侵害客体，再结合数据泄露、丢失、篡改、"
        f"中断等可能影响判断侵害程度，因此本次判定为{object_name}/{damage_name}。"
    )
    logger.info("已生成第二步默认判定理由。object_name=%s damage_name=%s", object_name, damage_name)
    return reason


def _step2_basis(record: ProjectRiskSummaryRecord, system_category_name: str, evidence: list[str]) -> list[str]:
    """汇总第二步判定使用的输入字段和规则依据，供前端展示“模型通过什么判定”。"""
    basis = [f"项目系统类别：{system_category_name}"]
    if record.risk_types:
        basis.append(f"风险类型：{', '.join(str(item) for item in record.risk_types)}")
    if record.risk_description:
        basis.append(f"风险描述：{record.risk_description}")
    if record.risk_source_description:
        basis.append(f"风险源描述：{record.risk_source_description}")
    if _has_text(record.related_data):
        basis.append(f"涉及的数据及类型、级别：{record.related_data}")
    if _has_text(record.related_activities):
        basis.append(f"涉及的数据处理活动：{_joined_text(record.related_activities)}")
    if record.assessment_category or record.assessment_subcategory:
        basis.append(f"现场测评分类：{record.assessment_category or '-'} / {record.assessment_subcategory or '-'}")
    if record.check_point:
        basis.append(f"检查要点：{record.check_point}")
    if record.evaluation_result:
        basis.append(f"符合情况：{_evaluation_result_name(record.evaluation_result)}")
    if record.evaluation_record:
        basis.append(f"评估结果：{record.evaluation_record}")
    basis.append("判定规则：先判断是否侵害国家安全，再判断是否侵害社会秩序或公共利益，最后判断是否侵害公民、法人和其他组织合法权益；同时结合数据泄露、丢失、篡改、中断等影响范围判断侵害程度。")

    # 如果模型额外返回了引用证据，也并入展示依据，避免前端遗漏模型真正引用的事实。
    for item in evidence:
        if item and item not in basis:
            basis.append(str(item))
    logger.info("已汇总第二步判定依据。risk_item_id=%s basis_count=%s", record.id, len(basis))
    return basis


def _trace_from_payload(payload: dict) -> dict:
    """当应用请求只传机器字段时，重建最小判定过程。"""
    logger.info(
        "根据应用请求字段重建最小四步判定 trace。system_category=%s harm_level=%s",
        _payload_value(payload, "system_category", "systemCategory"),
        _payload_value(payload, "harm_level", "harmLevel"),
    )
    harm_level = _payload_value(payload, "harm_level", "harmLevel")
    harm_level_name = _harm_level_name(harm_level)
    return {
        "systemCategory": _payload_value(payload, "system_category", "systemCategory"),
        "impactedObject": _payload_value(payload, "impacted_object", "impactedObject"),
        "damageDegree": _payload_value(payload, "damage_degree", "damageDegree"),
        "dataSecurityProtectionLevel": _payload_value(payload, "data_security_protection_level", "dataSecurityProtectionLevel"),
        "harmLevel": harm_level,
        "harmLevelName": harm_level_name,
        "step2Reason": _payload_value(payload, "step2_reason", "step2Reason", "reason"),
        "step2Basis": _payload_value(payload, "step2_basis", "step2Basis") or [],
        "step2Evidence": _payload_value(payload, "step2_evidence", "step2Evidence", "evidence") or [],
        "reason": _payload_value(payload, "reason"),
        "evidence": _payload_value(payload, "evidence") or [],
    }


def _payload_value(payload: dict, *keys: str) -> Any:
    """按候选字段名读取请求值，用于兼容前端 camelCase 和后端 snake_case。"""
    for key in keys:
        if key in payload:
            return payload.get(key)
    return None


def _evaluation_result_name(value: Any) -> str:
    key = str(value or "").strip()
    return RESULT_NAMES.get(key, key)


def _harm_level_name(value: Any) -> str | None:
    harm_level = _normalize_harm_level(value)
    if not harm_level:
        return None
    return HARM_LEVEL_NAMES.get(harm_level, harm_level)


def _normalize_harm_level(value: Any) -> str | None:
    """把外部传入的危害程度别名规范为风险矩阵使用的五档等级。"""
    logger.info("开始规范化危害程度等级。raw_value=%s", value)
    level = _normalize_level(value)
    normalized = HARM_LEVEL_ALIASES.get(level)
    logger.info("危害程度等级规范化完成。raw_value=%s normalized=%s", value, normalized)
    return normalized


def _normalize_possibility_level(value: Any) -> str | None:
    """把风险发生可能性规范为风险矩阵使用的三档等级。"""
    logger.info("开始规范化风险发生可能性。raw_value=%s", value)
    level = _normalize_level(value)
    normalized = POSSIBILITY_LEVEL_ALIASES.get(level)
    logger.info("风险发生可能性规范化完成。raw_value=%s normalized=%s", value, normalized)
    return normalized


def _normalize_level(value: Any) -> str | None:
    """规范化枚举值：去空格并转换为大写。"""
    if value in EMPTY_LEVELS:
        logger.info("枚举值为空，规范化结果为空。raw_value=%s", value)
        return None
    normalized = str(value).strip().upper()
    logger.info("枚举值规范化完成。raw_value=%s normalized=%s", value, normalized)
    return normalized


def _float_or_default(value: Any, default: float | None) -> float | None:
    """安全解析置信度等浮点值；解析失败时返回默认值。"""
    if value in (None, ""):
        logger.info("浮点值为空，使用默认值。default=%s", default)
        return default
    try:
        parsed = float(value)
        logger.info("浮点值解析完成。raw_value=%s parsed=%s", value, parsed)
        return parsed
    except (TypeError, ValueError):
        logger.warning("浮点值解析失败，使用默认值。raw_value=%s default=%s", value, default)
        return default


def _short_hash(value: str | None) -> str | None:
    """缩短 hash 仅用于日志展示，避免日志过长。"""
    if not value:
        return None
    return str(value)[:12]
