import sys
import time
import psutil
import traceback
import json
import os
from datetime import datetime
from typing import Callable, Dict, Any, List, Optional
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_fixed, RetryError

class TaskContext:
    """任务执行上下文，用于存储监控数据和日志"""
    def __init__(self, task_name: str):
        self.task_name = task_name
        self.start_time = None
        self.end_time = None
        self.duration = 0
        self.status = "PENDING"  # PENDING, RUNNING, SUCCESS, FAILURE, WARNING
        self.logs: List[str] = []
        self.errors: List[str] = []
        self.metrics = {
            "cpu_percent_start": 0,
            "cpu_percent_end": 0,
            "memory_mb_start": 0,
            "memory_mb_end": 0
        }

    def log_sink(self, message):
        """Loguru sink 用于捕获日志"""
        record = message.record
        log_entry = f"[{record['time'].strftime('%H:%M:%S')}] [{record['level'].name}] {record['message']}"
        self.logs.append(log_entry)
        if record["level"].no >= 40:  # ERROR or CRITICAL
            self.errors.append(record["message"])
            if self.status != "FAILURE":
                self.status = "WARNING"  # 标记为警告，除非已经是失败

class TaskExecutor:
    """自动化任务执行器"""
    
    def __init__(self):
        self.tasks: Dict[str, Callable] = {}
        self.task_descriptions: Dict[str, str] = {}
        self.results: List[TaskContext] = []

    def register(self, name: str, func: Callable, description: str = ""):
        """注册任务"""
        self.tasks[name] = func
        self.task_descriptions[name] = description
        logger.info(f"[EXECUTOR] 注册任务: {name} - {description}")

    def _get_process_metrics(self):
        """获取当前进程资源使用情况"""
        process = psutil.Process(os.getpid())
        mem = process.memory_info().rss / 1024 / 1024  # MB
        cpu = process.cpu_percent(interval=0.1)
        return cpu, mem

    def run(self, task_name: str, max_retries: int = 3, retry_delay: int = 5) -> TaskContext:
        """运行单个任务"""
        if task_name not in self.tasks:
            raise ValueError(f"任务 {task_name} 未注册")

        func = self.tasks[task_name]
        context = TaskContext(task_name)
        
        # 配置日志捕获
        sink_id = logger.add(context.log_sink, level="INFO")
        
        context.start_time = datetime.now()
        context.status = "RUNNING"
        
        try:
            context.metrics["cpu_percent_start"], context.metrics["memory_mb_start"] = self._get_process_metrics()
            
            logger.info(f"========== 开始执行任务: {task_name} ==========")
            
            # 使用 tenacity 进行重试封装
            @retry(stop=stop_after_attempt(max_retries), wait=wait_fixed(retry_delay), reraise=True)
            def _run_with_retry():
                return func()

            _run_with_retry()
            
            # 检查是否有错误日志
            if context.status == "WARNING":
                logger.warning(f"任务 {task_name} 完成，但检测到错误日志")
            else:
                context.status = "SUCCESS"
                logger.info(f"任务 {task_name} 执行成功")
                
        except RetryError as re:
            context.status = "FAILURE"
            error_msg = f"重试耗尽: {re}"
            logger.error(error_msg)
            context.errors.append(error_msg)
        except Exception as e:
            context.status = "FAILURE"
            error_msg = f"未捕获异常: {str(e)}\n{traceback.format_exc()}"
            logger.error(error_msg)
            context.errors.append(error_msg)
        finally:
            context.metrics["cpu_percent_end"], context.metrics["memory_mb_end"] = self._get_process_metrics()
            context.end_time = datetime.now()
            context.duration = (context.end_time - context.start_time).total_seconds()
            logger.info(f"========== 任务结束: {task_name} (耗时: {context.duration:.2f}s) ==========")
            logger.remove(sink_id)
            self.results.append(context)
            
        return context

    def run_all(self):
        """运行所有注册的任务"""
        for name in self.tasks:
            self.run(name)

    def generate_report(self, output_path: str = "task_report.md"):
        """生成 Markdown 报告"""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# 自动化任务执行报告\n\n")
            f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            # 概览表格
            f.write("## 1. 执行概览\n\n")
            f.write("| 任务名称 | 状态 | 耗时(s) | 内存变化(MB) | 错误数 |\n")
            f.write("| --- | --- | --- | --- | --- |\n")
            
            for ctx in self.results:
                status_icon = "✅" if ctx.status == "SUCCESS" else "⚠️" if ctx.status == "WARNING" else "❌"
                mem_diff = ctx.metrics["memory_mb_end"] - ctx.metrics["memory_mb_start"]
                mem_str = f"{mem_diff:+.1f}"
                f.write(f"| {ctx.task_name} | {status_icon} {ctx.status} | {ctx.duration:.2f} | {mem_str} | {len(ctx.errors)} |\n")
            
            f.write("\n## 2. 详细诊断\n\n")
            
            for ctx in self.results:
                f.write(f"### {ctx.task_name}\n\n")
                f.write(f"- **描述**: {self.task_descriptions.get(ctx.task_name, 'N/A')}\n")
                f.write(f"- **开始时间**: {ctx.start_time}\n")
                f.write(f"- **结束时间**: {ctx.end_time}\n")
                
                if ctx.errors:
                    f.write("\n**🚨 错误详情**:\n")
                    f.write("```text\n")
                    for err in ctx.errors:
                        f.write(f"{err}\n")
                    f.write("```\n")
                
                if ctx.logs:
                    f.write("\n**📝 执行日志 (最后20行)**:\n")
                    f.write("```text\n")
                    for log in ctx.logs[-20:]:
                        f.write(f"{log}\n")
                    f.write("```\n")
                
                f.write("---\n")
                
        logger.info(f"[EXECUTOR] 报告已生成: {output_path}")

