"""
调度器核心模块
"""
import signal
import sys
import time
import sqlalchemy
from typing import NoReturn, Optional

from apscheduler import events
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers import SchedulerAlreadyRunningError

from global_const.global_const import pgsql_engine
from utils.loguru_setting import logger
from utils.exception_listener import exception_listener, set_scheduler_instance

from ..config.settings import SchedulerConfig
from ..utils.time_utils import get_local_timezone
from ..tasks.job_registry import register_jobs, log_registered_jobs, perform_initial_tasks

class OptimizedScheduler:
    """优化版调度器类"""
    
    def __init__(self):
        self.scheduler: Optional[BackgroundScheduler] = None
        self.is_shutting_down = False
        self.consecutive_failures = 0
        
        # 配置参数
        self.timezone = get_local_timezone()
        self.max_failures = SchedulerConfig.MAX_FAILURES
        self.main_loop_interval = SchedulerConfig.MAIN_LOOP_INTERVAL
        
    def _init_scheduler(self) -> BackgroundScheduler:
        """初始化调度器"""
        try:
            scheduler = BackgroundScheduler(
                timezone=self.timezone,
                job_defaults=SchedulerConfig.get_job_defaults(),
            )
            
            # 注册事件监听器
            scheduler.add_listener(
                exception_listener,
                events.EVENT_JOB_ERROR | events.EVENT_JOB_EXECUTED
            )
            
            # 注册到健康检查模块
            set_scheduler_instance(scheduler)
            
            logger.info("[SCHEDULER] 调度器初始化完成（使用内存存储）")
            return scheduler
        except Exception as e:
            logger.error(f"[SCHEDULER] 调度器初始化失败: {e}", exc_info=True)
            raise
    
    def _handle_shutdown(self, signal_num: int, frame) -> NoReturn:
        """处理关闭信号"""
        if self.is_shutting_down:
            logger.warning("[SCHEDULER] 已在关闭过程中")
            return
        
        self.is_shutting_down = True
        signal_name = signal.Signals(signal_num).name
        logger.info(f"[SCHEDULER] 收到退出信号: {signal_name}")
        
        if self.scheduler and self.scheduler.running:
            try:
                self.scheduler.shutdown(wait=True)
                logger.info("[SCHEDULER] 调度器已停止")
            except Exception as e:
                logger.error(f"[SCHEDULER] 调度器停止失败: {e}", exc_info=True)
        
        logger.info("[SCHEDULER] 程序退出")
        sys.exit(0)
    
    def _register_signal_handlers(self) -> None:
        """注册信号处理器"""
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        logger.info("[SCHEDULER] 信号处理器已注册")
    
    def _start_scheduler(self) -> None:
        """启动调度器"""
        try:
            self.scheduler.start()
            logger.info("[SCHEDULER] 调度器启动成功")
        except SchedulerAlreadyRunningError as e:
            logger.error(f"[SCHEDULER] 调度器启动失败: {e}", exc_info=True)
            raise
    
    def _run_main_loop(self) -> None:
        """运行主循环"""
        logger.info("[SCHEDULER] 进入主循环")
        
        while not self.is_shutting_down:
            try:
                if not self.scheduler.running:
                    self.consecutive_failures += 1
                    logger.error(f"[SCHEDULER] 调度器停止 (失败: {self.consecutive_failures}/{self.max_failures})")
                    
                    if self.consecutive_failures >= self.max_failures:
                        logger.critical("[SCHEDULER] 连续失败过多，退出")
                        break
                    
                    self.scheduler.start()
                    logger.info("[SCHEDULER] 调度器重启成功")
                else:
                    # 重置失败计数
                    if self.consecutive_failures > 0:
                        self.consecutive_failures = 0
                        logger.info("[SCHEDULER] 调度器状态恢复正常")
                
                time.sleep(self.main_loop_interval)
                
            except SchedulerAlreadyRunningError:
                # 调度器已运行，正常情况
                self.consecutive_failures = 0
                time.sleep(self.main_loop_interval)
            except KeyboardInterrupt:
                logger.info("[SCHEDULER] 收到键盘中断")
                self._handle_shutdown(signal.SIGINT, None)
            except Exception as e:
                self.consecutive_failures += 1
                logger.error(f"[SCHEDULER] 主循环异常: {e}", exc_info=True)
                
                if self.consecutive_failures >= self.max_failures:
                    logger.critical("[SCHEDULER] 主循环异常过多，退出")
                    break
                
                time.sleep(self.main_loop_interval)
        
        logger.info("[SCHEDULER] 主循环结束")
        self._handle_shutdown(signal.SIGTERM, None)
    
    def initialize(self) -> None:
        """初始化调度器（包含数据库检查、建表、任务注册）"""
        logger.info("[SCHEDULER] 开始初始化调度器...")
        
        max_init_retries = SchedulerConfig.MAX_INIT_RETRIES
        init_retry_delay = SchedulerConfig.INIT_RETRY_DELAY
        
        for init_attempt in range(1, max_init_retries + 1):
            try:
                # 在初始化前先测试数据库连接
                logger.info("[SCHEDULER] 测试数据库连接...")
                try:
                    with pgsql_engine.connect() as conn:
                        conn.execute(sqlalchemy.text("SELECT 1"))
                    logger.info("[SCHEDULER] 数据库连接测试成功")
                except Exception as db_error:
                    raise Exception(f"数据库连接失败: {db_error}")
                
                # 执行初始任务（建表）
                perform_initial_tasks()
                
                # 初始化调度器
                self.scheduler = self._init_scheduler()
                
                # 注册信号处理器
                self._register_signal_handlers()
                
                # 注册业务任务
                register_jobs(self.scheduler, self.timezone)
                
                # 记录任务信息
                log_registered_jobs(self.scheduler)
                
                logger.info("[SCHEDULER] 初始化完成")
                return
                
            except Exception as e:
                error_msg = str(e)
                logger.error(f"[SCHEDULER] 初始化失败 (尝试 {init_attempt}/{max_init_retries}): {error_msg}")
                
                if init_attempt < max_init_retries:
                    logger.warning(f"[SCHEDULER] {init_retry_delay}秒后重试初始化...")
                    time.sleep(init_retry_delay)
                else:
                    logger.critical(f"[SCHEDULER] 初始化失败，已达到最大重试次数 ({max_init_retries})")
                    raise

    def start(self) -> None:
        """启动调度器（非阻塞模式）"""
        if not self.scheduler:
            raise RuntimeError("调度器尚未初始化，请先调用 initialize()")
            
        self._start_scheduler()

    def run(self) -> NoReturn:
        """运行调度器（带数据库连接重试，阻塞模式）"""
        logger.info("[SCHEDULER] === 优化版调度器启动 ===")
        
        try:
            self.initialize()
            self.start()
            logger.info("[SCHEDULER] 调度器已启动，进入主循环")
        except Exception as e:
            logger.critical(f"[SCHEDULER] 调度器无法启动: {e}")
            raise

        # 运行主循环
        try:
            self._run_main_loop()
        except KeyboardInterrupt:
            logger.info("[SCHEDULER] 收到键盘中断，程序退出")
        except Exception as e:
            logger.critical(f"[SCHEDULER] 调度器运行异常: {e}")
            logger.exception(e)  # 记录完整堆栈
        finally:
            logger.info("[SCHEDULER] 调度器程序结束")

def run_scheduler() -> NoReturn:
    """运行调度器入口函数"""
    scheduler = OptimizedScheduler()
    scheduler.run()
