import pytest

from app import create_app
from app.extensions import create_schema, drop_schema
from app.services.seed_service import seed_default_data


@pytest.fixture()
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "AUTH_REQUIRED": False,
            "FILE_STORAGE_ROOT": str(tmp_path / "generated"),
            "REPORT_TASK_ASYNC": False,
            "MINIO_ENDPOINT": None,
            "MINIO_ACCESS_KEY": None,
            "MINIO_SECRET_KEY": None,
            "MINIO_BUCKET_NAME": None,
        }
    )
    with app.app_context():
        create_schema()
        seed_default_data(app.config["DEFAULT_TEMPLATE_PATH"], app.config["DEFAULT_RISK_SOURCE_TEMPLATE_PATH"])
        yield app
        drop_schema()


@pytest.fixture()
def client(app):
    return app.test_client()
