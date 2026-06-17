from concurrent.futures import ThreadPoolExecutor

from flask import current_app


_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="report-task")


def enqueue(project_id: str, report_id: str, task_id: str) -> None:
    """按配置异步或同步执行报告任务。

    生产环境默认提交线程池并立即返回；测试和本地调试可关闭异步以便直接获取最终状态。
    """
    app = current_app._get_current_object()
    if app.config.get("REPORT_TASK_ASYNC", True):
        _executor.submit(_run, app, project_id, report_id, task_id)
    else:
        _run(app, project_id, report_id, task_id)


def _run(app, project_id: str, report_id: str, task_id: str) -> None:
    """在独立应用上下文中调用报告服务，保证后台线程可访问配置和数据库。"""
    with app.app_context():
        from app.services.report_service import execute_report_task

        try:
            execute_report_task(project_id, report_id, task_id)
        except Exception:
            app.logger.exception(
                "报告后台任务异常退出。project_id=%s report_id=%s task_id=%s",
                project_id,
                report_id,
                task_id,
            )
