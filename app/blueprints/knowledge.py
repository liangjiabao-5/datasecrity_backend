from flask import Blueprint, request

from app.common.response import success
from app.common.utils import request_json
from app.services import knowledge_service


bp = Blueprint("knowledge", __name__)


@bp.get("/knowledge/assessment-templates")
def list_assessment_templates():
    return success(knowledge_service.list_assessment_templates(request.args))


@bp.post("/knowledge/assessment-templates")
def create_assessment_template():
    return success(knowledge_service.create_assessment_template(request_json()))


@bp.get("/knowledge/assessment-templates/<template_id>")
def get_assessment_template(template_id: str):
    return success(knowledge_service.get_assessment_template(template_id))


@bp.put("/knowledge/assessment-templates/<template_id>")
def update_assessment_template(template_id: str):
    return success(knowledge_service.update_assessment_template(template_id, request_json()))


@bp.delete("/knowledge/assessment-templates/<template_id>")
def delete_assessment_template(template_id: str):
    return success(knowledge_service.delete_assessment_template(template_id))


@bp.get("/knowledge/assessment-templates/<template_id>/export")
def export_assessment_template(template_id: str):
    return success(knowledge_service.export_assessment_template(template_id))


@bp.get("/knowledge/assessment-templates/<template_id>/items")
def list_assessment_template_items(template_id: str):
    return success(knowledge_service.list_template_items(template_id, request.args))


@bp.post("/knowledge/assessment-templates/<template_id>/items")
def create_assessment_template_item(template_id: str):
    return success(knowledge_service.create_template_item(template_id, request_json()))


@bp.put("/knowledge/assessment-templates/<template_id>/items/<item_id>")
def update_assessment_template_item(template_id: str, item_id: str):
    return success(knowledge_service.update_template_item(template_id, item_id, request_json()))


@bp.delete("/knowledge/assessment-templates/<template_id>/items/<item_id>")
def delete_assessment_template_item(template_id: str, item_id: str):
    return success(knowledge_service.delete_template_item(template_id, item_id))


@bp.get("/knowledge/score-models")
def list_score_models():
    return success(knowledge_service.list_score_models(request.args))


@bp.post("/knowledge/score-models")
def create_score_model():
    return success(knowledge_service.create_score_model(request_json()))


@bp.get("/knowledge/score-models/<model_id>")
def get_score_model(model_id: str):
    return success(knowledge_service.get_score_model(model_id))


@bp.put("/knowledge/score-models/<model_id>")
def update_score_model(model_id: str):
    return success(knowledge_service.update_score_model(model_id, request_json()))


@bp.delete("/knowledge/score-models/<model_id>")
def delete_score_model(model_id: str):
    return success(knowledge_service.delete_score_model(model_id))


@bp.post("/knowledge/score-models/validate")
def validate_score_model_payload():
    return success(knowledge_service.validate_score_model(None, request_json()))


@bp.post("/knowledge/score-models/<model_id>/validate")
def validate_score_model(model_id: str):
    return success(knowledge_service.validate_score_model(model_id, request_json()))


@bp.get("/knowledge/harm-models")
def list_harm_models():
    return success(knowledge_service.list_harm_models(request.args))


@bp.post("/knowledge/harm-models")
def create_harm_model():
    return success(knowledge_service.create_harm_model(request_json()))


@bp.get("/knowledge/harm-models/<model_id>")
def get_harm_model(model_id: str):
    return success(knowledge_service.get_harm_model(model_id))


@bp.put("/knowledge/harm-models/<model_id>")
def update_harm_model(model_id: str):
    return success(knowledge_service.update_harm_model(model_id, request_json()))


@bp.delete("/knowledge/harm-models/<model_id>")
def delete_harm_model(model_id: str):
    return success(knowledge_service.delete_harm_model(model_id))


@bp.get("/knowledge/harm-models/<model_id>/rules")
def list_harm_rules(model_id: str):
    return success(knowledge_service.list_harm_rules(model_id))


@bp.post("/knowledge/harm-models/<model_id>/rules")
def create_harm_rule(model_id: str):
    return success(knowledge_service.create_harm_rule(model_id, request_json()))


@bp.put("/knowledge/harm-models/<model_id>/rules/<rule_id>")
def update_harm_rule(model_id: str, rule_id: str):
    return success(knowledge_service.update_harm_rule(model_id, rule_id, request_json()))


@bp.delete("/knowledge/harm-models/<model_id>/rules/<rule_id>")
def delete_harm_rule(model_id: str, rule_id: str):
    return success(knowledge_service.delete_harm_rule(model_id, rule_id))


@bp.get("/knowledge/risk-matrices")
def list_risk_matrices():
    return success(knowledge_service.list_risk_matrices(request.args))


@bp.post("/knowledge/risk-matrices")
def create_risk_matrix():
    return success(knowledge_service.create_risk_matrix(request_json()))


@bp.get("/knowledge/risk-matrices/<matrix_id>")
def get_risk_matrix(matrix_id: str):
    return success(knowledge_service.get_risk_matrix(matrix_id))


@bp.put("/knowledge/risk-matrices/<matrix_id>")
def update_risk_matrix(matrix_id: str):
    return success(knowledge_service.update_risk_matrix(matrix_id, request_json()))


@bp.delete("/knowledge/risk-matrices/<matrix_id>")
def delete_risk_matrix(matrix_id: str):
    return success(knowledge_service.delete_risk_matrix(matrix_id))


@bp.post("/knowledge/risk-matrices/validate")
def validate_risk_matrix_payload():
    return success(knowledge_service.validate_risk_matrix(None, request_json()))


@bp.post("/knowledge/risk-matrices/<matrix_id>/validate")
def validate_risk_matrix(matrix_id: str):
    return success(knowledge_service.validate_risk_matrix(matrix_id, request_json()))


@bp.get("/knowledge/<kind>")
def list_generic(kind: str):
    return success(knowledge_service.list_generic(kind, request.args))


@bp.post("/knowledge/<kind>")
def create_generic(kind: str):
    return success(knowledge_service.create_generic(kind, request_json()))


@bp.get("/knowledge/<kind>/<record_id>")
def get_generic(kind: str, record_id: str):
    return success(knowledge_service.get_generic(kind, record_id))


@bp.put("/knowledge/<kind>/<record_id>")
def update_generic(kind: str, record_id: str):
    return success(knowledge_service.update_generic(kind, record_id, request_json()))


@bp.delete("/knowledge/<kind>/<record_id>")
def delete_generic(kind: str, record_id: str):
    return success(knowledge_service.delete_generic(kind, record_id))
