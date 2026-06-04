from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from flask import request

from app.common.exceptions import BusinessError


def new_id(prefix: str = "id") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def utcnow() -> datetime:
    return datetime.utcnow()


def to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def to_snake(name: str) -> str:
    if name.upper() == name:
        return name
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def normalize_keys(value: Any) -> Any:
    if isinstance(value, list):
        return [normalize_keys(item) for item in value]
    if isinstance(value, dict):
        return {to_snake(key): normalize_keys(item) for key, item in value.items()}
    return value


def camelize_keys(value: Any) -> Any:
    if isinstance(value, list):
        return [camelize_keys(item) for item in value]
    if isinstance(value, dict):
        return {to_camel(key): camelize_keys(item) for key, item in value.items()}
    return value


def request_json() -> dict:
    payload = request.get_json(silent=True)
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise BusinessError("INVALID_JSON", "JSON body must be an object.")
    return normalize_keys(payload)


def require_fields(payload: dict, fields: list[str]) -> None:
    missing = [field for field in fields if payload.get(field) in (None, "")]
    if missing:
        raise BusinessError("REQUIRED_FIELD_MISSING", f"Missing required fields: {', '.join(missing)}")


def json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
