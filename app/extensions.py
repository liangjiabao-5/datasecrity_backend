from __future__ import annotations

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
        if drop_legacy_tables:
            for table in dropped_tables:
                connection.execute(text(f"DROP TABLE IF EXISTS {table}"))
