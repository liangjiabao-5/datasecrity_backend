from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

from flask import current_app, has_app_context

from app.models import Project, ProjectRiskSummaryRecord


logger = logging.getLogger(__name__)

LLM_DISABLED = "DISABLED"
LLM_SUCCESS = "SUCCESS"
LLM_FALLBACK = "FALLBACK"


def suggest_harm_analysis(
    project: Project,
    record: ProjectRiskSummaryRecord,
    system_category: str,
    rule_config: dict,
) -> dict | None:
    """调用 DashScope 判断单条风险行的侵害客体和侵害程度。"""
    # 第一步：读取模型配置。这里不打印 API Key，只记录是否已配置。
    config = _llm_config()
    if not config["enabled"] or not config["api_key"]:
        logger.info(
            "跳过 DashScope 危害程度分析：大模型未启用或 API Key 未配置。enabled=%s api_key_configured=%s project_id=%s risk_item_id=%s",
            config.get("enabled"),
            bool(config.get("api_key")),
            project.id,
            record.id,
        )
        return None

    logger.info(
        "开始调用 DashScope 进行危害程度分析。project_id=%s risk_item_id=%s model=%s",
        project.id,
        record.id,
        config["model"],
    )

    # 第二步：构造兼容 OpenAI 协议的 messages，要求模型只返回 JSON 对象。
    prompt = _harm_analysis_prompt(project, record, system_category, rule_config)
    logger.info(
        "DashScope 危害程度分析提示词已构造。project_id=%s risk_item_id=%s system_category=%s risk_type_count=%s",
        project.id,
        record.id,
        system_category,
        len(record.risk_types or []),
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是数据安全风险评估辅助助手。请只基于输入事实和规则约束判断风险可能侵害的客体与侵害程度。"
                "不得虚构现场证据，不得输出结论前言，必须返回 JSON 对象。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                prompt,
                ensure_ascii=False,
            ),
        },
    ]

    # 第三步：发起 HTTP 请求并解析模型返回；这里只记录返回长度，不记录完整模型内容。
    content = _chat_completion(config, messages)
    logger.info("DashScope 已返回文本结果，准备解析 JSON。project_id=%s risk_item_id=%s content_length=%s", project.id, record.id, len(content))
    parsed = _parse_json_object(content)
    parsed["llm_status"] = LLM_SUCCESS
    parsed["llm_provider"] = "dashscope"
    parsed["llm_model"] = config["model"]
    logger.info(
        "DashScope 危害程度分析调用完成。project_id=%s risk_item_id=%s impactedObject=%s damageDegree=%s confidence=%s",
        project.id,
        record.id,
        parsed.get("impactedObject") or parsed.get("impacted_object"),
        parsed.get("damageDegree") or parsed.get("damage_degree"),
        parsed.get("confidence"),
    )
    return parsed


def _llm_config() -> dict:
    """从 Flask 配置中读取 DashScope 兼容 OpenAI 协议的调用参数。"""
    if not has_app_context():
        logger.warning("当前没有 Flask app context，无法读取 DashScope 配置。")
        return {"enabled": False, "api_key": None}
    # API Key 先做占位符识别，避免 .env.example 中的示例值被当成真实密钥导致 401。
    api_key = _normalized_api_key(current_app.config.get("DASHSCOPE_API_KEY"))
    config = {
        "enabled": bool(current_app.config.get("LLM_ENABLED", True)),
        "base_url": str(current_app.config.get("DASHSCOPE_BASE_URL") or "").rstrip("/"),
        "model": current_app.config.get("DASHSCOPE_MODEL") or "qwen-plus",
        "api_key": api_key,
        "timeout": int(current_app.config.get("DASHSCOPE_TIMEOUT_MS") or 30000) / 1000,
    }
    logger.info(
        "DashScope 配置读取完成。enabled=%s base_url=%s model=%s api_key_configured=%s timeout=%s",
        config["enabled"],
        config["base_url"],
        config["model"],
        bool(config["api_key"]),
        config["timeout"],
    )
    return config


def _normalized_api_key(value: Any) -> str | None:
    """清洗 DashScope API Key，并把示例占位符视为未配置。"""
    api_key = str(value or "").strip()
    if not api_key:
        logger.info("DashScope API Key 为空。")
        return None
    lowered = api_key.lower()
    if lowered.startswith("replace-") or "your-dashscope-api-key" in lowered:
        logger.warning("DashScope API Key 仍是示例占位符，已按未配置处理。")
        return None
    logger.info("DashScope API Key 已配置。")
    return api_key


def _harm_analysis_prompt(
    project: Project,
    record: ProjectRiskSummaryRecord,
    system_category: str,
    rule_config: dict,
) -> dict:
    """根据风险行、现场测评上下文和危害模型规则构造 JSON 提示词。"""
    object_priority = rule_config.get("object_priority") or ["NATIONAL_SECURITY", "PUBLIC_INTEREST", "LEGAL_RIGHTS"]
    system_config = (rule_config.get("system_categories") or {}).get(system_category) or {}
    logger.info(
        "开始构造 DashScope 危害程度分析提示词。project_id=%s risk_item_id=%s assessment_category=%s evaluation_result=%s",
        project.id,
        record.id,
        record.assessment_category,
        record.evaluation_result,
    )
    return {
        "task": "判断该风险行的数据安全被破坏时，优先侵害的客体和侵害程度。",
        "input": {
            "projectId": project.id,
            "systemType": project.system_type,
            "systemCategory": system_category,
            "systemCategoryName": system_config.get("name") or system_category,
            "assessmentCategory": record.assessment_category,
            "assessmentSubcategory": record.assessment_subcategory,
            "checkPoint": _limit(record.check_point, 1200),
            "evaluationResult": record.evaluation_result,
            "evaluationRecord": _limit(record.evaluation_record, 1200),
            "riskTypes": record.risk_types or [],
            "riskDescription": _limit(record.risk_description, 1200),
            "riskSourceDescription": _limit(record.risk_source_description, 1200),
        },
        "rules": {
            "impactObjectPriority": object_priority,
            "impactObjectEnum": rule_config.get("object_names") or {},
            "damageDegreeEnum": rule_config.get("damage_degree_names") or {},
            "allowedForSystemCategory": system_config.get("impact_degrees") or {},
            "judgementOrder": [
                "结合现场测评分类、检查要点、评估结果区分风险业务背景和严重性",
                "先判断是否侵害国家安全",
                "再判断是否侵害社会秩序或公共利益",
                "最后判断是否侵害公民、法人和其他组织合法权益",
                "结合数据泄露、丢失、篡改等风险影响判断侵害程度",
            ],
        },
        "outputSchema": {
            "impactedObject": "NATIONAL_SECURITY | PUBLIC_INTEREST | LEGAL_RIGHTS",
            "damageDegree": "GENERAL | SERIOUS | EXTREMELY_SERIOUS",
            "reason": "不超过200字的判断理由",
            "evidence": ["从输入中引用的关键事实，不超过3条"],
            "confidence": "0到1之间的小数",
            "needsManualReview": "无法判断或规则冲突时为 true",
        },
    }


def _chat_completion(config: dict, messages: list[dict]) -> str:
    """向 DashScope 发送兼容 OpenAI 协议的 chat/completions 请求。"""
    url = f"{config['base_url']}/chat/completions"
    # 请求体固定 temperature=0，降低同一输入下危害程度建议的随机波动。
    payload = {
        "model": config["model"],
        "messages": messages,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    # API Key 只写入请求头，不进入日志，避免泄露密钥。
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        # 发送外部 HTTP 请求，超时时间来自 DASHSCOPE_TIMEOUT_MS。
        logger.info("DashScope HTTP 请求开始。url=%s model=%s timeout=%s", url, config["model"], config["timeout"])
        with urllib.request.urlopen(request, timeout=config["timeout"]) as response:
            body = response.read().decode("utf-8")
            logger.info("DashScope HTTP 请求成功。status=%s response_length=%s", response.status, len(body))
    except urllib.error.HTTPError as exc:
        # HTTPError 通常代表鉴权、限流、模型名或请求格式问题，日志中截断 detail 防止内容过长。
        detail = exc.read().decode("utf-8", errors="ignore")
        logger.warning("DashScope HTTP 请求失败。status=%s detail=%s", exc.code, _limit(detail, 300))
        raise RuntimeError(f"DashScope HTTP 请求失败，状态码 {exc.code}：{_limit(detail, 300)}") from exc
    except urllib.error.URLError as exc:
        # URLError 通常代表网络、DNS、代理或连接超时问题。
        logger.warning("DashScope 网络请求失败。reason=%s", exc.reason)
        raise RuntimeError(f"DashScope 网络请求失败：{exc.reason}") from exc

    # DashScope 返回结构遵循 choices[0].message.content，这里只取文本交给 JSON 解析器。
    data = json.loads(body)
    choices = data.get("choices") or []
    if not choices:
        logger.warning("DashScope 响应缺少 choices。")
        raise RuntimeError("DashScope 响应缺少 choices。")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content:
        logger.warning("DashScope 响应缺少 message.content。")
        raise RuntimeError("DashScope 响应缺少 message.content。")
    logger.info("DashScope message.content 提取完成。content_length=%s", len(content))
    return content


def _parse_json_object(content: str) -> dict:
    """从模型返回内容中解析 JSON 对象；兼容模型偶尔包裹解释文本的情况。"""
    text = str(content or "").strip()
    try:
        # 理想情况：模型严格按 response_format 返回一个 JSON 对象。
        value = json.loads(text)
        logger.info("DashScope 返回内容已按标准 JSON 解析。")
    except json.JSONDecodeError:
        # 兜底情况：模型在 JSON 前后带了解释文本，则截取第一个对象再解析。
        logger.warning("DashScope 返回内容不是纯 JSON，尝试截取第一个 JSON 对象。")
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            logger.warning("DashScope 返回内容中未找到 JSON 对象。")
            raise RuntimeError("DashScope 返回内容不是 JSON 对象。")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        logger.warning("DashScope 返回 JSON 不是对象。value_type=%s", type(value).__name__)
        raise RuntimeError("DashScope 返回 JSON 必须是对象。")
    logger.info("DashScope JSON 对象解析完成。keys=%s", sorted(value.keys()))
    return value


def _limit(value: Any, max_length: int) -> str:
    """限制提示词或日志片段长度，避免超长文本进入请求或日志。"""
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    logger.info("文本超过限制长度，已截断。raw_length=%s max_length=%s", len(text), max_length)
    return text[:max_length] + "..."
