import logging

from flask import Blueprint

from app.common.response import success
from app.common.utils import request_json
from app.services import harm_analysis_service
from app.services import risk_service


bp = Blueprint("risk", __name__)
logger = logging.getLogger(__name__)


@bp.post("/projects/<project_id>/risk-summary/refresh")
def refresh(project_id: str):
    """从当前现场测评记录刷新汇总分析风险数据。"""
    payload = request_json()
    logger.info("收到刷新汇总分析风险数据请求。project_id=%s payload_keys=%s", project_id, sorted(payload.keys()))
    # 调用汇总分析刷新服务：把现场测评中部分符合/不符合的记录同步为风险清单三张表数据。
    return success(risk_service.refresh(project_id, payload))


@bp.get("/projects/<project_id>/risk-sources")
def list_risk_sources(project_id: str):
    """查询汇总分析中的风险源清单。"""
    logger.info("收到查询风险源清单请求。project_id=%s", project_id)
    # 风险源清单与风险清单共用汇总记录表，这里只返回风险源视角字段。
    return success(risk_service.list_risk_sources(project_id))


@bp.put("/projects/<project_id>/risk-sources/<risk_source_id>")
def update_risk_source(project_id: str, risk_source_id: str):
    """修改风险源清单中允许人工编辑的字段。"""
    payload = request_json()
    logger.info(
        "收到修改风险源清单请求。project_id=%s risk_source_id=%s payload_keys=%s",
        project_id,
        risk_source_id,
        sorted(payload.keys()),
    )
    # 只把白名单字段交给服务层处理，避免前端误传字段覆盖系统生成字段。
    return success(risk_service.update_risk_source(project_id, risk_source_id, payload))


@bp.get("/projects/<project_id>/risk-items")
def list_risk_items(project_id: str):
    """查询数据安全风险清单，包含危害程度辅助判定相关字段。"""
    logger.info("收到查询数据安全风险清单请求。project_id=%s", project_id)
    # 返回正式风险清单字段，同时带出危害程度建议确认后的闭环字段。
    return success(risk_service.list_risk_items(project_id))


@bp.put("/projects/<project_id>/risk-items/<risk_item_id>")
def update_risk_item(project_id: str, risk_item_id: str):
    """手工修改风险清单字段，例如危害程度、发生可能性和风险等级。"""
    payload = request_json()
    logger.info(
        "收到手工修改风险清单请求。project_id=%s risk_item_id=%s payload_keys=%s",
        project_id,
        risk_item_id,
        sorted(payload.keys()),
    )
    # 手工修改危害程度或发生可能性时，服务层会按项目风险矩阵尝试重算风险等级。
    return success(risk_service.update_risk_item(project_id, risk_item_id, payload))


@bp.post("/projects/<project_id>/risk-items/<risk_item_id>/harm-analysis/suggest")
def suggest_harm_analysis(project_id: str, risk_item_id: str):
    """生成单条风险危害程度建议；只返回建议，不写入正式风险清单。"""
    payload = request_json()
    logger.info(
        "收到单条危害程度辅助判定请求。project_id=%s risk_item_id=%s payload_keys=%s",
        project_id,
        risk_item_id,
        sorted(payload.keys()),
    )
    # 只生成建议和四步 trace，用户未确认前不改正式风险清单字段。
    return success(harm_analysis_service.suggest(project_id, risk_item_id, payload))


@bp.post("/projects/<project_id>/risk-items/harm-analysis/suggest-batch")
def suggest_harm_analysis_batch(project_id: str):
    """批量生成风险危害程度建议；只返回建议，不写入正式风险清单。"""
    payload = request_json()
    logger.info(
        "收到批量危害程度辅助判定请求。project_id=%s payload_keys=%s 指定风险行数量=%s",
        project_id,
        sorted(payload.keys()),
        len(payload.get("risk_item_ids") or payload.get("risk_record_ids") or []),
    )
    # 批量接口默认处理当前页或指定 ID 列表，仍然只返回建议，由前端逐条确认。
    return success(harm_analysis_service.suggest_batch(project_id, payload))


@bp.post("/projects/<project_id>/risk-items/<risk_item_id>/harm-analysis/apply")
def apply_harm_analysis(project_id: str, risk_item_id: str):
    """把用户确认后的危害程度建议应用到正式风险清单。"""
    payload = request_json()
    logger.info(
        "收到应用危害程度建议请求。project_id=%s risk_item_id=%s payload_keys=%s",
        project_id,
        risk_item_id,
        sorted(payload.keys()),
    )
    # 用户确认后才写入 harmLevel、描述、影响对象、示例和四步判定 trace。
    return success(harm_analysis_service.apply(project_id, risk_item_id, payload))


@bp.get("/projects/<project_id>/risk-suggestions")
def list_risk_suggestions(project_id: str):
    """查询风险处置建议清单。"""
    logger.info("收到查询风险处置建议清单请求。project_id=%s", project_id)
    # 风险处置建议同样来源于汇总记录，只返回整改建议视角字段。
    return success(risk_service.list_risk_suggestions(project_id))


@bp.put("/projects/<project_id>/risk-suggestions/<suggestion_id>")
def update_risk_suggestion(project_id: str, suggestion_id: str):
    """修改风险处置建议文本。"""
    payload = request_json()
    logger.info(
        "收到修改风险处置建议请求。project_id=%s suggestion_id=%s payload_keys=%s",
        project_id,
        suggestion_id,
        sorted(payload.keys()),
    )
    # 仅允许修改处置建议文本，风险描述和风险等级保持汇总分析的正式字段。
    return success(risk_service.update_risk_suggestion(project_id, suggestion_id, payload))
