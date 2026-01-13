import logging
import sys

from loguru import logger
from global_const.global_const import env


class InterceptHandler(logging.Handler):
    def emit(self, record):
        # Retrieve context where the logging call occurred, this happens to be in the 6th frame upward
        logger_opt = logger.opt(depth=6, exception=record.exc_info)
        logger_opt.log(record.levelno, record.getMessage())


def loguru_setting(production=env):
    folder_ = "./Logs/"
    prefix_ = "mushroom_solution-"
    rotation_ = "00:00"
    retention_ = "30 days"
    encoding_ = "utf-8"
    backtrace_ = True
    diagnose_ = True

    # 格式里面添加了process和thread记录，方便查看多进程和线程程序
    format_ = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> "
        "| <magenta>{process}</magenta>:<yellow>{thread}</yellow> "
        "| <cyan>{name}</cyan>:<cyan>{function}</cyan>:<yellow>{line}</yellow> - <level>{message}</level>"
    )

    # 根据是否生产环境设置日志级别
    log_level = "INFO" if production else "DEBUG"

    ## loguru 接管所有日志（包括第三方库）。 但是日志数量比较多，基本上作用不大，选择关掉
    # class InterceptHandler(logging.Handler):
    #     def emit(self, record):
    #         # Retrieve context where the logging call occurred, this happens to be in the 6th frame upward
    #         logger_opt = logger.opt(depth=6, exception=record.exc_info)
    #         logger_opt.log(record.levelno, record.getMessage())
    #
    # logger_name_list = [name for name in logging.root.manager.loggerDict]
    #
    # for logger_name in logger_name_list:
    #     logging.getLogger(logger_name).setLevel(10)
    #     logging.getLogger(logger_name).handlers = []
    #     if '.' not in logger_name:
    #         logging.getLogger(logger_name).addHandler(InterceptHandler())

    # 这里面采用了层次式的日志记录方式，就是低级日志文件会记录比他高的所有级别日志，这样可以做到低等级日志最丰富，高级别日志更少更关键
    # debug
    logger.add(
        folder_ + prefix_ + "debug.log",
        level=log_level,
        backtrace=backtrace_,
        diagnose=diagnose_,
        format=format_,
        colorize=False,
        rotation=rotation_,
        retention=retention_,
        encoding=encoding_,
        filter=lambda record: record["level"].no >= logger.level(log_level).no,
    )

    # info
    logger.add(
        folder_ + prefix_ + "info.log",
        level="INFO",
        backtrace=backtrace_,
        diagnose=diagnose_,
        format=format_,
        colorize=False,
        rotation=rotation_,
        retention=retention_,
        encoding=encoding_,
        filter=lambda record: record["level"].no >= logger.level("INFO").no,
    )

    # warning
    logger.add(
        folder_ + prefix_ + "warning.log",
        level="WARNING",
        backtrace=backtrace_,
        diagnose=diagnose_,
        format=format_,
        colorize=False,
        rotation=rotation_,
        retention=retention_,
        encoding=encoding_,
        filter=lambda record: record["level"].no >= logger.level("WARNING").no,
    )

    # error
    logger.add(
        folder_ + prefix_ + "error.log",
        level="ERROR",
        backtrace=backtrace_,
        diagnose=diagnose_,
        format=format_,
        colorize=False,
        rotation=rotation_,
        retention=retention_,
        encoding=encoding_,
        filter=lambda record: record["level"].no >= logger.level("ERROR").no,
    )

    # critical
    logger.add(
        folder_ + prefix_ + "critical.log",
        level="CRITICAL",
        backtrace=backtrace_,
        diagnose=diagnose_,
        format=format_,
        colorize=False,
        rotation=rotation_,
        retention=retention_,
        encoding=encoding_,
        filter=lambda record: record["level"].no >= logger.level("CRITICAL").no,
    )

    # 控制台输出级别根据环境决定
    console_level = "INFO" if production else "DEBUG"
    logger.add(
        sys.stderr,
        level=console_level,
        backtrace=backtrace_,
        diagnose=diagnose_,
        format=format_,
        colorize=True,
        filter=lambda record: record["level"].no >= logger.level(console_level).no,
    )