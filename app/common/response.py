import uuid

from flask import Flask, g, jsonify
from pydantic import ValidationError

from app.common.exceptions import BusinessError
from app.extensions import SessionLocal


def success(data=None, message: str = "Operation succeeded."):
    trace_id = getattr(g, "trace_id", None) or uuid.uuid4().hex[:10]
    return jsonify({"code": "SUCCESS", "message": message, "data": data, "traceId": trace_id})


def register_error_handlers(app: Flask) -> None:
    @app.before_request
    def attach_trace_id() -> None:
        g.trace_id = uuid.uuid4().hex[:10]

    @app.errorhandler(BusinessError)
    def handle_business_error(exc: BusinessError):
        SessionLocal.rollback()
        return (
            jsonify(
                {
                    "code": exc.code,
                    "message": exc.message,
                    "data": exc.data,
                    "traceId": getattr(g, "trace_id", uuid.uuid4().hex[:10]),
                }
            ),
            exc.status_code,
        )

    @app.errorhandler(ValidationError)
    def handle_validation_error(exc: ValidationError):
        SessionLocal.rollback()
        return (
            jsonify(
                {
                    "code": "VALIDATION_ERROR",
                    "message": str(exc),
                    "data": None,
                    "traceId": getattr(g, "trace_id", uuid.uuid4().hex[:10]),
                }
            ),
            400,
        )

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc: Exception):
        SessionLocal.rollback()
        if app.config.get("TESTING"):
            raise exc
        return (
            jsonify(
                {
                    "code": "INTERNAL_ERROR",
                    "message": "Internal server error.",
                    "data": None,
                    "traceId": getattr(g, "trace_id", uuid.uuid4().hex[:10]),
                }
            ),
            500,
        )
