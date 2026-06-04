# Datasecurity Backend

Flask backend for the data security risk assessment workflow.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env
flask init-db
flask run
```

The production target is MySQL 8. For quick local API checks, set
`DATABASE_URL=sqlite:///instance/datasecurity.db`.

To create versioned migrations after model changes:

```powershell
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

## Implemented scope

- Project management and start-evaluation item snapshot generation.
- Basic project information.
- Assessment plan data.
- Survey data.
- Evaluation records and score calculation.
- Risk summary refresh and editable risk lists.
- Word report generation from the annotated risk-assessment template.
- Report readiness checks, background tasks, status polling, retry, download, and logical deletion.
- Local/MinIO file storage and evaluation Excel import/export.

Report generation uses a process-local background executor by default. Set
`REPORT_TASK_ASYNC=false` for deterministic local testing. A multi-instance
production deployment should replace the task adapter with Celery or RQ while
keeping the report service and API contract unchanged.


代码文件说明如下。
根目录
requirements.txt：Python 依赖清单。
.env.example：数据库连接、认证开关等环境配置示例。
run.py：Flask 应用启动入口。
README.md：本地启动、初始化数据库、迁移命令说明。
alembic.ini：Alembic 数据库迁移配置。
应用初始化
app/__init__.py：创建 Flask app、注册蓝图、错误处理、CLI 命令。
app/config.py：读取数据库地址、认证开关、默认模板路径等配置。
app/extensions.py：SQLAlchemy 引擎、Session、建表/删表能力。
app/models.py：所有数据库表模型定义。
通用能力
app/common/response.py：统一成功响应、错误响应。
app/common/exceptions.py：业务异常、资源不存在异常。
app/common/auth.py：临时登录态/认证占位。
app/common/pagination.py：分页参数和分页返回。
app/common/utils.py：ID 生成、驼峰/下划线转换、JSON 请求解析等工具。
接口蓝图
app/blueprints/project.py：项目列表、统计、创建、编辑、详情、删除、开始评估。
app/blueprints/basic.py：项目基本信息、被评估单位、联系人、自定义法规/标准。
app/blueprints/plan.py：评估团队、被评估方团队、关注点、差距项 CRUD。
app/blueprints/survey.py：信息调研相关接口。
app/blueprints/evaluation.py：现场测评、评估记录、批量修改、分数计算。
app/blueprints/risk.py：汇总分析、风险源、风险清单、处置建议。
app/blueprints/knowledge.py：知识库下拉/列表接口。
app/blueprints/report.py：报告就绪检查、生成任务、状态、重试、下载和删除。
app/blueprints/placeholders.py：通用文件和大模型占位接口。
业务服务
app/services/project_service.py：项目核心业务和开始评估生成检查项快照。
app/services/basic_info_service.py：基本信息保存与回显。
app/services/crud_service.py：通用项目子表 CRUD。
app/services/survey_service.py：安全防护措施调研保存。
app/services/evaluation_service.py：测评目录、检查项、记录、分数计算。
app/services/risk_service.py：风险汇总刷新和人工修改。
app/services/report_service.py：报告任务状态流转、报告记录和文件管理。
app/services/docx_report_service.py：按批注标注的数据来源填充 Word 模板、表格和图片。
app/services/knowledge_service.py：知识库启用数据查询。
app/services/seed_service.py：从 Excel 初始化国标模板、评分模型、危害模型、矩阵。
app/services/audit_service.py：审计日志写入。
迁移与测试
migrations/env.py：Alembic 迁移环境。
migrations/script.py.mako：迁移脚本模板。
tests/conftest.py：测试 app 和测试数据库初始化。
tests/test_flow.py：项目流程接口测试用例。
tests/test_report.py：Word 报告填充、样式、图片、批注清理、失败和重试测试。
