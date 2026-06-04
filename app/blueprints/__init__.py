from flask import Flask

from app.blueprints.basic import bp as basic_bp
from app.blueprints.evaluation import bp as evaluation_bp
from app.blueprints.knowledge import bp as knowledge_bp
from app.blueprints.placeholders import bp as placeholder_bp
from app.blueprints.plan import bp as plan_bp
from app.blueprints.project import bp as project_bp
from app.blueprints.report import bp as report_bp
from app.blueprints.risk import bp as risk_bp
from app.blueprints.survey import bp as survey_bp


def register_blueprints(app: Flask) -> None:
    app.register_blueprint(project_bp, url_prefix="/api/v1")
    app.register_blueprint(report_bp, url_prefix="/api/v1")
    app.register_blueprint(basic_bp, url_prefix="/api/v1")
    app.register_blueprint(plan_bp, url_prefix="/api/v1")
    app.register_blueprint(survey_bp, url_prefix="/api/v1")
    app.register_blueprint(evaluation_bp, url_prefix="/api/v1")
    app.register_blueprint(risk_bp, url_prefix="/api/v1")
    app.register_blueprint(knowledge_bp, url_prefix="/api/v1")
    app.register_blueprint(placeholder_bp, url_prefix="/api/v1")

    @app.get("/health")
    def health():
        return {"status": "ok"}
