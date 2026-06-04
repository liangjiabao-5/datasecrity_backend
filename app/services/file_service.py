from __future__ import annotations

import mimetypes
from io import BytesIO
from pathlib import Path, PurePosixPath

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.common.exceptions import BusinessError, NotFoundError
from app.extensions import SessionLocal
from app.models import FileObject
from app.services.audit_service import audit


LOCAL_STORAGE_PROVIDER = "LOCAL"
MINIO_STORAGE_PROVIDER = "MINIO"
MINIO_BIZ_TYPES = {"SURVEY_TOPOLOGY_DIAGRAM", "SURVEY_DATA_FLOW_DIAGRAM", "REPORT"}


def save_upload(file: FileStorage | None, biz_type: str = "GENERAL", project_id: str | None = None) -> dict:
    if not file or not file.filename:
        raise BusinessError("FILE_REQUIRED", "Upload file is required.")
    content = file.read()
    content_type = file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
    row = save_bytes(file.filename, content, content_type, biz_type=biz_type, project_id=project_id)
    audit("FILE_UPLOAD", "FileObject", row["fileId"], after=row)
    return row


def save_bytes(
    file_name: str,
    content: bytes,
    content_type: str | None = None,
    biz_type: str = "GENERAL",
    project_id: str | None = None,
) -> dict:
    session = SessionLocal()
    file_id = FileObject().id
    object_key = _object_key(file_id, file_name)
    normalized_content_type = content_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    storage_provider, bucket_name = _storage_target(biz_type)
    if storage_provider == MINIO_STORAGE_PROVIDER:
        _save_to_minio(bucket_name, object_key, content, normalized_content_type)
    else:
        target = _storage_root() / object_key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    row = FileObject(
        id=file_id,
        project_id=project_id,
        file_name=file_name,
        object_key=object_key,
        storage_provider=storage_provider,
        bucket_name=bucket_name,
        content_type=normalized_content_type,
        file_size=len(content),
        biz_type=biz_type,
    )
    session.add(row)
    session.commit()
    return serialize_file(row)


def get_file(file_id: str) -> FileObject:
    session = SessionLocal()
    row = session.get(FileObject, file_id)
    if not row or row.deleted:
        raise NotFoundError("File not found.")
    return row


def file_path(row: FileObject) -> Path:
    if _file_provider(row) != LOCAL_STORAGE_PROVIDER:
        raise BusinessError("UNSUPPORTED_FILE_SOURCE", "File is not stored on local disk.")
    root = _storage_root().resolve()
    target = (root / row.object_key).resolve()
    if root not in target.parents and target != root:
        raise BusinessError("INVALID_FILE_PATH", "Invalid file storage path.")
    if not target.exists():
        raise NotFoundError("File content not found.")
    return target


def file_stream(row: FileObject) -> BytesIO:
    return BytesIO(read_bytes(row))


def read_bytes(row: FileObject) -> bytes:
    if _file_provider(row) == MINIO_STORAGE_PROVIDER:
        bucket_name = row.bucket_name or _minio_config(required=True)["bucket_name"]
        return _read_from_minio(bucket_name, row.object_key)
    return file_path(row).read_bytes()


def serialize_file(row: FileObject) -> dict:
    data = row.to_dict()
    data["fileId"] = row.id
    data["downloadUrl"] = f"/api/v1/files/{row.id}/download"
    return data


def _storage_root() -> Path:
    configured = current_app.config.get("FILE_STORAGE_ROOT")
    if configured:
        return Path(configured)
    return Path(current_app.instance_path) / "generated"


def _object_key(file_id: str, file_name: str) -> str:
    suffix = Path(file_name).suffix
    safe_name = secure_filename(Path(file_name).stem) or "file"
    return str(PurePosixPath("files") / f"{file_id}_{safe_name}{suffix}")


def _storage_target(biz_type: str) -> tuple[str, str | None]:
    if biz_type in MINIO_BIZ_TYPES:
        minio_config = _minio_config()
        if not minio_config:
            return LOCAL_STORAGE_PROVIDER, None
        return MINIO_STORAGE_PROVIDER, minio_config["bucket_name"]
    return LOCAL_STORAGE_PROVIDER, None


def _file_provider(row: FileObject) -> str:
    return (row.storage_provider or LOCAL_STORAGE_PROVIDER).upper()


def _minio_config(required: bool = False) -> dict | None:
    config = {
        "endpoint": current_app.config.get("MINIO_ENDPOINT"),
        "access_key": current_app.config.get("MINIO_ACCESS_KEY"),
        "secret_key": current_app.config.get("MINIO_SECRET_KEY"),
        "secure": _as_bool(current_app.config.get("MINIO_SECURE")),
        "bucket_name": current_app.config.get("MINIO_BUCKET_NAME"),
    }
    required_keys = ("endpoint", "access_key", "secret_key", "bucket_name")
    configured_values = [config[key] for key in required_keys]
    if not any(configured_values):
        if required:
            raise BusinessError("MINIO_NOT_CONFIGURED", "MinIO storage is not configured.")
        return None
    if not all(configured_values):
        raise BusinessError("MINIO_CONFIG_INCOMPLETE", "MinIO endpoint, access key, secret key and bucket name are required.")
    return config


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _minio_client():
    config = _minio_config(required=True)
    try:
        from minio import Minio
    except ImportError as exc:
        raise BusinessError("MINIO_DEPENDENCY_MISSING", "Python package 'minio' is required for MinIO storage.") from exc

    cache = current_app.extensions.setdefault("minio_clients", {})
    cache_key = (config["endpoint"], config["access_key"], config["secure"])
    if cache_key not in cache:
        cache[cache_key] = Minio(
            config["endpoint"],
            access_key=config["access_key"],
            secret_key=config["secret_key"],
            secure=config["secure"],
        )
    return cache[cache_key]


def _ensure_minio_bucket(bucket_name: str) -> None:
    client = _minio_client()
    try:
        if not client.bucket_exists(bucket_name):
            client.make_bucket(bucket_name)
    except Exception as exc:
        _raise_minio_error(exc, "MINIO_BUCKET_UNAVAILABLE", "MinIO bucket is unavailable.")


def _save_to_minio(bucket_name: str, object_key: str, content: bytes, content_type: str) -> None:
    _ensure_minio_bucket(bucket_name)
    try:
        _minio_client().put_object(
            bucket_name,
            object_key,
            BytesIO(content),
            length=len(content),
            content_type=content_type,
        )
    except Exception as exc:
        _raise_minio_error(exc, "MINIO_UPLOAD_FAILED", "Failed to upload file to MinIO.")


def _read_from_minio(bucket_name: str, object_key: str) -> bytes:
    response = None
    try:
        response = _minio_client().get_object(bucket_name, object_key)
        return response.read()
    except Exception as exc:
        _raise_minio_error(exc, "MINIO_DOWNLOAD_FAILED", "Failed to download file from MinIO.")
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def _raise_minio_error(exc: Exception, default_code: str, default_message: str) -> None:
    if getattr(exc, "code", None) == "RequestTimeTooSkewed":
        raise BusinessError(
            "MINIO_TIME_SKEW",
            "MinIO server time differs too much from the application server time.",
            status_code=502,
        ) from exc
    raise BusinessError(default_code, default_message, status_code=502) from exc
