from __future__ import annotations

import json

from flask import Flask
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


SessionLocal = scoped_session(
    sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, future=True)
)

_engine = None


def init_database(app: Flask) -> None:
    global _engine
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    kwargs = {"future": True, "pool_pre_ping": True}

    if uri.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if uri in {"sqlite://", "sqlite:///:memory:"}:
            kwargs["poolclass"] = StaticPool

    _engine = create_engine(uri, **kwargs)
    SessionLocal.configure(bind=_engine)

    @app.teardown_appcontext
    def cleanup_session(_exc: BaseException | None = None) -> None:
        SessionLocal.remove()


def create_schema() -> None:
    if _engine is None:
        raise RuntimeError("Database engine is not initialized.")
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=_engine)
    _apply_lightweight_schema_updates(drop_legacy_tables=True)


def apply_runtime_schema_updates() -> None:
    if _engine is None:
        raise RuntimeError("Database engine is not initialized.")
    import app.models  # noqa: F401

    if "data_processor_basic_survey" in Base.metadata.tables:
        Base.metadata.tables["data_processor_basic_survey"].create(bind=_engine, checkfirst=True)
    _apply_lightweight_schema_updates(drop_legacy_tables=False)


def drop_schema() -> None:
    if _engine is None:
        raise RuntimeError("Database engine is not initialized.")
    import app.models  # noqa: F401

    Base.metadata.drop_all(bind=_engine)


def _apply_lightweight_schema_updates(drop_legacy_tables: bool = False) -> None:
    """Apply tiny additive updates until formal migration scripts are adopted."""
    inspector = inspect(_engine)
    tables = set(inspector.get_table_names())
    data_processor_basic_columns = [
        ("unit_name", "VARCHAR(200)"),
        ("unified_social_credit_code", "VARCHAR(100)"),
        ("office_address", "VARCHAR(500)"),
        ("legal_representative", "VARCHAR(100)"),
        ("staff_size", "VARCHAR(100)"),
        ("business_scope", "TEXT"),
        ("data_security_officer", "VARCHAR(100)"),
        ("contact_info", "VARCHAR(200)"),
        ("unit_nature", "VARCHAR(100)"),
        ("specific_processor_type", "VARCHAR(200)"),
        ("power_industry_category", "VARCHAR(200)"),
        ("business_operation_area", "VARCHAR(200)"),
        ("data_processing_location", "VARCHAR(500)"),
        ("main_business_scope", "TEXT"),
        ("business_scale", "VARCHAR(200)"),
        ("administrative_license", "TEXT"),
    ]
    processing_activity_survey_columns = [
        ("involved_activities", "TEXT"),
        ("collection_channels", "TEXT"),
        ("collection_method", "TEXT"),
        ("collection_data_scope", "TEXT"),
        ("collection_purpose", "TEXT"),
        ("collection_frequency", "TEXT"),
        ("collection_external_sources", "TEXT"),
        ("collection_contracts", "TEXT"),
        ("collection_related_systems", "TEXT"),
        ("collection_public_device_usage", "TEXT"),
        ("storage_method", "TEXT"),
        ("data_center", "TEXT"),
        ("storage_system", "TEXT"),
        ("external_storage_provider", "TEXT"),
        ("storage_location", "TEXT"),
        ("storage_duration", "TEXT"),
        ("backup_redundancy_strategy", "TEXT"),
        ("online_channel", "TEXT"),
        ("offline_transfer", "TEXT"),
        ("transfer_protocol", "TEXT"),
        ("data_interface", "TEXT"),
        ("use_purpose", "TEXT"),
        ("use_method", "TEXT"),
        ("use_scope", "TEXT"),
        ("use_scenario", "TEXT"),
        ("algorithm_rules", "TEXT"),
        ("processing_details", "TEXT"),
        ("algorithm_recommendation_service", "TEXT"),
        ("entrusted_or_joint_processing", "TEXT"),
        ("provide_purpose", "TEXT"),
        ("provide_method", "TEXT"),
        ("provide_scope", "TEXT"),
        ("data_recipients", "TEXT"),
        ("provide_contracts", "TEXT"),
        ("provided_personal_info_and_important_data", "TEXT"),
        ("public_purpose", "TEXT"),
        ("public_method", "TEXT"),
        ("public_scope", "TEXT"),
        ("public_audience_size", "TEXT"),
        ("public_data_types", "TEXT"),
        ("public_data_scale", "TEXT"),
        ("deletion_scenarios", "TEXT"),
        ("deletion_method", "TEXT"),
        ("data_archive", "TEXT"),
        ("media_destruction", "TEXT"),
        ("cross_border_presence", "TEXT"),
        ("cross_border_description", "TEXT"),
    ]
    security_protection_columns = [
        ("compliance_assessment_status", "TEXT"),
        ("data_security_management", "TEXT"),
        ("network_security_devices_and_policies", "TEXT"),
        ("identity_authentication_and_access_control", "TEXT"),
        ("vulnerability_management", "TEXT"),
        ("remote_management_software", "TEXT"),
        ("account_password_management", "TEXT"),
        ("security_technology_application", "TEXT"),
        ("is_power_monitoring_system", "VARCHAR(20)"),
        ("production_control_area_protection", "TEXT"),
        ("security_access_area_setup", "TEXT"),
        ("power_monitoring_dedicated_network", "TEXT"),
        ("zone_isolation_device_usage", "TEXT"),
        ("wide_area_network_connection_security", "TEXT"),
        ("power_dispatch_authentication", "TEXT"),
        ("network_service_security_control", "TEXT"),
        ("security_access_area_security_control", "TEXT"),
        ("zone_boundary_protection", "TEXT"),
        ("product_security_reliability", "TEXT"),
        ("operator_security_monitoring_warning", "TEXT"),
        ("security_incidents_and_threats", "TEXT"),
        ("detected_threats", "TEXT"),
        ("public_threat_alerts", "TEXT"),
        ("other_security_threats", "TEXT"),
    ]
    additions = {
        "risk_matrix": [("remark", "TEXT")],
        "personal_info_asset": [
            ("data_name", "VARCHAR(200)"),
            ("data_category", "VARCHAR(200)"),
            ("data_source", "VARCHAR(200)"),
            ("business_flow", "TEXT"),
        ],
        "important_data_asset": [
            ("data_name", "VARCHAR(200)"),
            ("data_category", "VARCHAR(200)"),
            ("scale", "VARCHAR(100)"),
            ("data_source", "VARCHAR(200)"),
            ("business_flow", "TEXT"),
        ],
        "core_data_asset": [
            ("data_name", "VARCHAR(200)"),
            ("data_category", "VARCHAR(200)"),
            ("scale", "VARCHAR(100)"),
            ("data_source", "VARCHAR(200)"),
            ("business_flow", "TEXT"),
        ],
        "project_risk_summary_record": [
            ("evaluation_item_id", "VARCHAR(64)"),
            ("source_item_code", "VARCHAR(100)"),
            ("assessment_category", "VARCHAR(200)"),
            ("assessment_subcategory", "VARCHAR(200)"),
            ("check_point", "TEXT"),
            ("evaluation_result", "VARCHAR(40)"),
            ("evaluation_record", "TEXT"),
            ("risk_types", "JSON"),
            ("risk_description", "TEXT"),
            ("risk_source_description", "TEXT"),
            ("related_data", "TEXT"),
            ("related_activities", "JSON"),
            ("harm_level", "VARCHAR(40)"),
            ("harm_description", "TEXT"),
            ("harm_impact_object", "VARCHAR(200)"),
            ("harm_example", "TEXT"),
            ("harm_analysis_trace", "JSON"),
            ("harm_analysis_confidence", "FLOAT"),
            ("harm_analysis_input_hash", "VARCHAR(64)"),
            ("possibility_level", "VARCHAR(40)"),
            ("risk_level", "VARCHAR(40)"),
            ("remediation_suggestion", "TEXT"),
            ("manual_adjusted", "BOOLEAN DEFAULT FALSE NOT NULL"),
            ("current", "BOOLEAN DEFAULT TRUE NOT NULL"),
        ],
        "risk_source_template": [
            ("sheet_name", "VARCHAR(100)"),
            ("category", "VARCHAR(200)"),
            ("subcategory", "VARCHAR(200)"),
            ("assessment_item", "TEXT"),
            ("evaluation_record", "TEXT"),
            ("evaluation_result", "VARCHAR(40)"),
            ("risk_description", "TEXT"),
            ("remediation_suggestion", "TEXT"),
            ("risk_source_description", "TEXT"),
            ("risk_source_type", "TEXT"),
            ("risk_types", "JSON"),
            ("sort_order", "INTEGER"),
        ],
        "harm_model": [
            ("rule_config", "JSON"),
        ],
        "file_object": [
            ("storage_provider", "VARCHAR(40) DEFAULT 'LOCAL' NOT NULL"),
            ("bucket_name", "VARCHAR(200)"),
        ],
        "business_system": [
            ("data_scopes", "TEXT"),
        ],
        "important_data_asset": [
            ("data_name", "VARCHAR(200)"),
            ("data_category", "VARCHAR(200)"),
            ("scale", "VARCHAR(100)"),
            ("data_source", "VARCHAR(200)"),
            ("business_flow", "TEXT"),
            ("processing_activity_types", "TEXT"),
        ],
        "core_data_asset": [
            ("data_name", "VARCHAR(200)"),
            ("data_category", "VARCHAR(200)"),
            ("scale", "VARCHAR(100)"),
            ("data_source", "VARCHAR(200)"),
            ("business_flow", "TEXT"),
            ("processing_activity_types", "TEXT"),
        ],
        "data_processing_activity": [
            ("protection_measures", "TEXT"),
        ],
        "data_processor_basic_survey": data_processor_basic_columns,
        "processing_activity_survey": processing_activity_survey_columns,
        "security_protection_survey": security_protection_columns,
    }
    drop_columns = {
        "project_basic_info": ["project_description", "system_description", "assessment_target"],
        "assessed_organization": ["address", "credit_code", "data_security_owner", "description"],
        "data_processor_basic_survey": ["payload"],
        "processing_activity_survey": ["payload"],
        "security_protection_survey": ["payload"],
    }
    dropped_tables = ["risk_suggestion", "risk_item", "risk_source"]

    with _engine.begin() as connection:
        for table, columns_to_add in additions.items():
            if table not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for column_name, column_type in columns_to_add:
                if column_name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_type}"))
        _modify_text_columns_if_supported(connection)
        _backfill_payload_columns(
            connection,
            "data_processor_basic_survey",
            [column_name for column_name, _column_type in data_processor_basic_columns],
        )
        _backfill_payload_columns(
            connection,
            "processing_activity_survey",
            [column_name for column_name, _column_type in processing_activity_survey_columns],
            list_fields={"involved_activities": ","},
        )
        _backfill_payload_columns(
            connection,
            "security_protection_survey",
            [column_name for column_name, _column_type in security_protection_columns],
        )
        _normalize_text_list_column(connection, "business_system", "data_scopes", "、")
        _normalize_text_list_column(connection, "important_data_asset", "processing_activity_types", ",")
        _normalize_text_list_column(connection, "core_data_asset", "processing_activity_types", ",")
        _normalize_text_list_column(connection, "data_processing_activity", "protection_measures", "、")
        for table, columns_to_drop in drop_columns.items():
            if table not in tables:
                continue
            existing = {column["name"] for column in inspect(_engine).get_columns(table)}
            for column_name in columns_to_drop:
                if column_name in existing:
                    connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {column_name}"))
        if drop_legacy_tables:
            for table in dropped_tables:
                connection.execute(text(f"DROP TABLE IF EXISTS {table}"))


def _modify_text_columns_if_supported(connection) -> None:
    if connection.dialect.name not in {"mysql", "mariadb"}:
        return
    for table, column in [
        ("business_system", "data_scopes"),
        ("important_data_asset", "processing_activity_types"),
        ("core_data_asset", "processing_activity_types"),
        ("data_processing_activity", "protection_measures"),
    ]:
        connection.execute(text(f"ALTER TABLE {table} MODIFY COLUMN {column} TEXT"))


def _backfill_payload_columns(
    connection,
    table: str,
    fields: list[str],
    list_fields: dict[str, str] | None = None,
) -> None:
    existing = {column["name"] for column in inspect(_engine).get_columns(table)} if table in inspect(_engine).get_table_names() else set()
    if "payload" not in existing:
        return
    selectable = ["id", "payload", *[field for field in fields if field in existing]]
    rows = connection.execute(text(f"SELECT {', '.join(selectable)} FROM {table} WHERE payload IS NOT NULL")).mappings().all()
    list_fields = list_fields or {}
    for row in rows:
        payload = _coerce_payload(row["payload"])
        if not payload:
            continue
        updates = {}
        for field in fields:
            if field not in existing or row.get(field) not in (None, ""):
                continue
            value = payload.get(field)
            if field in list_fields:
                value = _list_value_to_text(value, list_fields[field])
            if value not in (None, "", [], {}):
                updates[field] = value
        if updates:
            assignments = ", ".join(f"{field} = :{field}" for field in updates)
            connection.execute(text(f"UPDATE {table} SET {assignments} WHERE id = :id"), {**updates, "id": row["id"]})


def _normalize_text_list_column(connection, table: str, column: str, separator: str) -> None:
    tables = set(inspect(_engine).get_table_names())
    if table not in tables:
        return
    existing = {item["name"] for item in inspect(_engine).get_columns(table)}
    if column not in existing:
        return
    rows = connection.execute(text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")).mappings().all()
    for row in rows:
        normalized = _list_value_to_text(row[column], separator)
        if normalized != row[column]:
            connection.execute(text(f"UPDATE {table} SET {column} = :value WHERE id = :id"), {"value": normalized, "id": row["id"]})


def _coerce_payload(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _list_value_to_text(value, separator: str) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                return value
            return _list_value_to_text(parsed, separator)
        return value
    if isinstance(value, (list, tuple, set)):
        return separator.join(str(item) for item in value if item not in (None, ""))
    return str(value)
