"""巡检触发任务：每 3 小时让库房主机跑一轮巡检。

## 为什么是"投一次就走"

库房主机那边一轮约 **11 分钟**（60 个站位 × 单次采图 ~5.2 s）。而本调度器的
`max_instances=1`、`misfire_grace_time=300s`：如果 job 里等这一轮跑完，
下一次触发会被判 misfire 直接丢掉——看上去"定时任务时好时坏"。
所以这里只做一次 POST，拿到 202/409 就结束，跑没跑完由巡检台的
`GET /api/patrol/run` 与页面回答。

## 409 是正常结果，不是错误

同一时刻只允许一个待处理请求（避免两次挤在一起连跑 22 分钟）。上一轮的请求还没被
执行方领走时再触发，对方回 409 + 现有请求——这属于"已经有一次在排队了"，记一句日志即可。

## 地址

现场算法容器与巡检容器在同一个 compose 网络里，但巡检服务暴露在**宿主**上，
按本项目的既有惯例用 `172.17.0.1:<端口>`（与 scada / redis / MinIO 的写法一致）。
"""

from __future__ import annotations

import os
from datetime import timezone
from importlib import import_module

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from utils.send_request import SendRequest

_common = import_module("scheduling.tasks.common")
CronJobDefinition = _common.CronJobDefinition
LockWrapper = _common.LockWrapper
register_cron_jobs = _common.register_cron_jobs

#: 每 3 小时一轮；分钟取 20，避开老系统整点 :01:30 的那次采图（同一台相机）
PATROL_TRIGGER_MINUTE = 20
PATROL_TRIGGER_HOURS = "*/3"
#: 巡检台的触发地址。用环境变量而不是 settings.toml：巡检是**另一个子系统**的地址，
#: 放在算法侧的 configs 里会让两边改配置互相牵动；compose 里给一个 env 就够了。
DEFAULT_PATROL_URL = "http://172.17.0.1:8001/api/patrol/run"


def safe_patrol_trigger() -> None:
    """投一次巡检请求。任何异常都吞掉并记日志——不能让它拖垮调度器的健康统计。"""
    url = os.environ.get("PATROL_RUN_URL", DEFAULT_PATROL_URL)
    try:
        resp = SendRequest().send_post_request(
            url=url,
            headers={"Content-Type": "application/json"},
            payload={"by": "scheduler", "reason": "每 3 小时定时巡检"},
        )
    except Exception as exc:  # noqa: BLE001 - 巡检投不出去不该影响别的任务
        logger.warning(f"[PATROL] 触发请求发送失败：{exc}（url={url}）")
        return
    logger.info(f"[PATROL] 已投巡检请求（url={url}）：{resp}")


def get_patrol_job_definitions() -> list[CronJobDefinition]:
    """返回巡检任务定义。"""
    return [
        CronJobDefinition(
            job_id="patrol_round_every_3h",
            lock_id="patrol_round_every_3h",
            func=safe_patrol_trigger,
            cron_kwargs={"hour": PATROL_TRIGGER_HOURS, "minute": PATROL_TRIGGER_MINUTE},
            log_message=(f"[SCHEDULER] 巡检任务已添加：每 3 小时第 {PATROL_TRIGGER_MINUTE} 分"
                         "触发一次（只投请求，不等它跑完）"),
        )
    ]


def register_patrol_jobs(
    scheduler: BackgroundScheduler,
    local_timezone: timezone,
    with_lock: LockWrapper,
) -> None:
    """注册巡检触发任务。"""
    register_cron_jobs(
        scheduler,
        local_timezone,
        with_lock,
        get_patrol_job_definitions(),
    )
