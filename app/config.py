import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
ENV_EXAMPLE_PATH = BASE_DIR / ".env.example"

# 优先读取本机真实环境配置；没有 .env 时再读取示例配置，方便开发环境快速启动。
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
elif ENV_EXAMPLE_PATH.exists():
    load_dotenv(ENV_EXAMPLE_PATH)


def _path_env(name: str, default: Path) -> str:
    """读取路径类环境变量，并把相对路径转换为项目根目录下的绝对路径。"""
    value = os.getenv(name)
    if not value:
        return str(default)
    path = Path(value)
    return str(path if path.is_absolute() else BASE_DIR / path)


class Config:
    # 基础运行配置：日志级别用于控制新增中文链路日志的输出密度。
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # 数据库配置：默认使用 instance/datasecurity.db，部署环境可通过 DATABASE_URL 覆盖。
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'instance' / 'datasecurity.db'}",
    )
    AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "false").lower() == "true"
    FILE_STORAGE_ROOT = _path_env("FILE_STORAGE_ROOT", BASE_DIR / "instance" / "generated")

    MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT")
    MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
    MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")
    MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
    MINIO_BUCKET_NAME = os.getenv("MINIO_BUCKET_NAME")

    # 默认知识库模板路径：初始化数据库和测试夹具都会读取这两个 Excel 文件。
    DEFAULT_TEMPLATE_PATH = _path_env("DEFAULT_TEMPLATE_PATH", BASE_DIR / "doc" / "国标测评录入模版.xlsx")
    DEFAULT_RISK_SOURCE_TEMPLATE_PATH = _path_env(
        "DEFAULT_RISK_SOURCE_TEMPLATE_PATH",
        BASE_DIR / "doc" / "国标风险源模版.xlsx",
    )
    REPORT_TEMPLATE_PATH = _path_env(
        "REPORT_TEMPLATE_PATH",
        BASE_DIR / "doc" / "数据安全风险评估报告模版.docx",
    )
    REPORT_TASK_ASYNC = os.getenv("REPORT_TASK_ASYNC", "true").lower() == "true"

    # DashScope 大模型配置：API Key 只在服务端读取，不会通过接口返回或写入日志。
    LLM_ENABLED = os.getenv("LLM_ENABLED", "true").lower() == "true"
    DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    DASHSCOPE_MODEL = os.getenv("DASHSCOPE_MODEL", "qwen-plus")
    DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
    DASHSCOPE_TIMEOUT_MS = int(os.getenv("DASHSCOPE_TIMEOUT_MS", "30000"))
    JSON_AS_ASCII = False
