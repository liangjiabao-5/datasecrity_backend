from pathlib import Path
import logging

import click
from flask import Flask

from app.blueprints import register_blueprints
from app.common.auth import init_auth
from app.common.response import register_error_handlers
from app.config import Config
from app.extensions import apply_runtime_schema_updates, create_schema, init_database
from app.services.seed_service import seed_default_data


def create_app(config_override: dict | None = None) -> Flask:
    """创建 Flask 应用，并按固定顺序完成配置、数据库、鉴权、异常处理和路由注册。"""
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if config_override:
        app.config.update(config_override)

    # 先确保 instance 目录存在，后续 SQLite、导出文件等本地资源会依赖该目录。
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    # 日志必须尽早初始化，方便记录数据库补丁、鉴权和蓝图注册阶段的问题。
    configure_logging(app)
    app.logger.info("应用日志初始化完成。log_level=%s", app.config.get("LOG_LEVEL"))

    # 初始化 SQLAlchemy 会话工厂，并执行增量运行时表结构补丁。
    init_database(app)
    app.logger.info("数据库连接初始化完成。database_uri_configured=%s", bool(app.config.get("SQLALCHEMY_DATABASE_URI")))
    apply_runtime_schema_updates()
    app.logger.info("运行时数据库结构检查完成。")

    # 鉴权、异常处理和业务蓝图按顺序注册，确保所有接口都有统一错误返回。
    init_auth(app)
    register_error_handlers(app)
    register_blueprints(app)
    register_cli(app)
    app.logger.info("应用初始化完成，接口蓝图和命令行工具已注册。")

    return app


def configure_logging(app: Flask) -> None:
    """按环境变量 LOG_LEVEL 配置应用日志级别和日志格式。"""
    level_name = str(app.config.get("LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    if not hasattr(logging, level_name):
        app.logger.warning("LOG_LEVEL 配置无法识别，已使用 INFO。raw_log_level=%s", level_name)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logging.getLogger("app").setLevel(level)
    app.logger.setLevel(level)


def register_cli(app: Flask) -> None:
    @app.cli.command("init-db")
    @click.option("--skip-seed", is_flag=True, help="只创建数据库表，不初始化默认知识库数据。")
    def init_db(skip_seed: bool = False) -> None:
        """创建数据库表，并按需初始化默认知识库数据。"""
        app.logger.info("开始执行数据库初始化命令。skip_seed=%s", skip_seed)
        create_schema()
        if not skip_seed:
            seed_default_data(app.config["DEFAULT_TEMPLATE_PATH"], app.config["DEFAULT_RISK_SOURCE_TEMPLATE_PATH"])
            app.logger.info("默认知识库种子数据初始化完成。")
        app.logger.info("数据库初始化命令执行完成。")
        click.echo("数据库初始化完成。")
