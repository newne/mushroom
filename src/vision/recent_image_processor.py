"""
最近图片处理器
用于处理最近时间段内的图片数据，支持定期处理和增量处理
"""

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import sessionmaker

from global_const.global_const import pgsql_engine
from utils import log_task_event
from utils.create_table import MushroomImageEmbedding
from utils.minio_client import create_minio_client

from .mushroom_image_encoder import create_mushroom_encoder
from .mushroom_image_processor import MushroomImagePathParser


def _recent_log(event: str, message: str, level: str = "INFO", **context: Any) -> None:
    """输出统一的最近图片处理器事件日志。"""
    log_task_event(
        "VISION_RECENT_IMAGE_PROCESSOR",
        event,
        message,
        level=level,
        task_type="helper",
        **context,
    )


class RecentImageProcessor:
    """最近图片处理器"""

    def __init__(self, shared_encoder=None, shared_minio_client=None):
        """
        初始化处理器

        Args:
            shared_encoder: 共享的编码器实例，避免重复初始化
            shared_minio_client: 共享的MinIO客户端实例，避免重复初始化
        """
        # 使用共享实例或创建新实例
        self.minio_client = shared_minio_client or create_minio_client()
        self.encoder = shared_encoder or create_mushroom_encoder()
        self.parser = MushroomImagePathParser()

        # 缓存最近查询的图片数据，避免重复查询
        self._cached_images = None
        self._cache_timestamp = None
        self._cache_hours = None

        self._latest_in_date_cache: dict[str, datetime | None] = {}

        _recent_log(
            "VISION_RECENT_PROCESSOR_INIT",
            "最近图片处理器初始化完成",
            level="DEBUG",
            status="success",
        )

    def _get_latest_in_date(self, room_id: str) -> datetime | None:
        """获取库房最近入库日期（缓存）"""
        if room_id in self._latest_in_date_cache:
            return self._latest_in_date_cache[room_id]

        Session = sessionmaker(bind=pgsql_engine)
        session = Session()
        try:
            latest = (
                session.query(MushroomImageEmbedding.in_date)
                .filter(MushroomImageEmbedding.room_id == room_id)
                .order_by(MushroomImageEmbedding.in_date.desc())
                .first()
            )
            latest_in_date = latest[0] if latest else None
            self._latest_in_date_cache[room_id] = latest_in_date
            return latest_in_date
        except Exception as e:
            _recent_log(
                "VISION_RECENT_IN_DATE_QUERY_FAILED",
                "查询最近入库日期失败",
                level="WARNING",
                room_id=room_id,
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            self._latest_in_date_cache[room_id] = None
            return None
        finally:
            session.close()

    def _get_recent_in_date_from_images(self, images: list[dict]) -> date | None:
        """从图片路径中推断最近一次入库日期"""
        in_dates: list[date] = []
        for img in images:
            object_name = img.get("object_name")
            if not object_name:
                continue
            image_info = self.parser.parse_path(object_name)
            if not image_info:
                continue
            in_dates.append(image_info.collection_date_obj.date())

        return max(in_dates) if in_dates else None

    def _is_room_stale(
        self, room_id: str, images: list[dict], window_days: int = 30
    ) -> bool:
        """判断库房是否超过入库窗口期"""
        today = datetime.now().date()
        latest_in_date = self._get_latest_in_date(room_id)
        inferred_in_date = self._get_recent_in_date_from_images(images)

        if latest_in_date is None:
            if inferred_in_date is None:
                return True
            return (today - inferred_in_date).days > window_days

        if (today - latest_in_date).days <= window_days:
            return False

        if inferred_in_date is None:
            return True

        return (today - inferred_in_date).days > window_days

    def _get_recent_images_cached(
        self, hours: int = 1, room_id: str | None = None
    ) -> list[dict]:
        """
        获取最近图片数据，使用缓存避免重复查询

        Args:
            hours: 查询最近多少小时的数据
            room_id: 指定库房号，如果为None则查询所有库房

        Returns:
            图片数据列表
        """
        current_time = datetime.now()

        # 检查缓存是否有效（5分钟内的查询结果可以复用）
        cache_valid = (
            self._cached_images is not None
            and self._cache_timestamp is not None
            and self._cache_hours == hours
            and (current_time - self._cache_timestamp).total_seconds()
            < 300  # 5分钟缓存
        )

        if cache_valid:
            cached_images = self._cached_images
        else:
            # 重新查询并缓存
            cached_images = self.minio_client.list_recent_images(hours=hours)
            self._cached_images = cached_images
            self._cache_timestamp = current_time
            self._cache_hours = hours

        # 如果指定了库房，进行过滤
        if room_id:
            filtered_images = [
                img for img in cached_images if img["room_id"] == room_id
            ]
            return filtered_images

        return cached_images

    def _select_best_images(self, images: list[dict]) -> list[dict]:
        """
        根据AI评分筛选最佳的5张图片 (Growth Stage Scoring Strategy)
        触发条件: 图片数量 > 10
        筛选流程:
        1. 质量过滤 (Quality Scale >= 50)
        2. 关键词匹配 (e.g. 'mushroom')
        3. 生长阶段评分 (Relevance Score)
        4. Top-5 选取
        """
        _recent_log(
            "VISION_RECENT_AI_SELECTION_START",
            "开始执行 AI 优选 Top-5",
            total_items=len(images),
            status="running",
        )

        scored_candidates = []
        skipped_count = 0

        # 1. 预过滤和评分
        for img_dict in images:
            try:
                # 解析路径
                image_info = self.parser.parse_path(img_dict["object_name"])
                if not image_info:
                    continue

                # 获取图像
                image = self.minio_client.get_image(image_info.file_path)
                if image is None:
                    continue

                # 获取分析结果 (LLaMA)
                # 使用新添加的公开方法
                analysis = self.encoder.get_growth_stage_analysis(image)
                score = analysis.get("image_quality_score")
                description = analysis.get("growth_stage_description", "")

                # 质量过滤
                if self.encoder.quality_threshold > 0:
                    if score is None or score < self.encoder.quality_threshold:
                        _recent_log(
                            "VISION_RECENT_AI_SELECTION_QUALITY_FILTERED",
                            "图片因质量评分不足被过滤",
                            level="DEBUG",
                            image_path=img_dict.get("object_name"),
                            score=score,
                            threshold=self.encoder.quality_threshold,
                            status="skipped",
                        )
                        skipped_count += 1
                        continue

                # 关键词过滤
                if self.encoder.required_keywords:
                    desc_lower = description.lower()
                    if not any(
                        k.lower() in desc_lower for k in self.encoder.required_keywords
                    ):
                        _recent_log(
                            "VISION_RECENT_AI_SELECTION_KEYWORD_FILTERED",
                            "图片因关键词不匹配被过滤",
                            level="DEBUG",
                            image_path=img_dict.get("object_name"),
                            description_preview=description[:50],
                            required_keywords=",".join(self.encoder.required_keywords),
                            status="skipped",
                        )
                        skipped_count += 1
                        continue

                # 记录候选者
                # 将分析结果附带在 img_dict 中，以便后续使用
                img_dict_with_analysis = img_dict.copy()
                img_dict_with_analysis["_precomputed_analysis"] = analysis
                scored_candidates.append(
                    {
                        "score": score if score is not None else 0,
                        "img_dict": img_dict_with_analysis,
                    }
                )

            except Exception as e:
                _recent_log(
                    "VISION_RECENT_AI_SELECTION_ITEM_FAILED",
                    "AI 优选单项处理失败",
                    level="ERROR",
                    image_path=img_dict.get("object_name"),
                    status="failed",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                continue

        # 2. 排序并取 Top 5
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)
        top_candidates = scored_candidates[:5]

        selected_images = [cand["img_dict"] for cand in top_candidates]
        _recent_log(
            "VISION_RECENT_AI_SELECTION_FINISH",
            "AI 优选完成",
            total_items=len(images),
            selected_items=len(selected_images),
            skipped_items=skipped_count,
            top_score=scored_candidates[0]["score"] if scored_candidates else None,
            status="success",
        )

        return selected_images

    def get_recent_image_summary_and_process(
        self,
        hours: int = 1,
        room_ids: list[str] | None = None,
        max_images_per_room: int | None = None,
        save_to_db: bool = True,
        show_summary: bool = True,
        batch_config: dict | None = None,
    ) -> dict[str, Any]:
        """
        整合的方法：获取摘要并处理图片，避免重复查询

        Args:
            hours: 查询最近多少小时的数据
            room_ids: 指定库房列表，如果为None则处理所有库房
            max_images_per_room: 每个库房最多处理多少张图片
            save_to_db: 是否保存到数据库
            show_summary: 是否显示摘要信息
            batch_config: 批处理配置 {'enabled': bool, 'batch_size': int}

        Returns:
            包含摘要和处理结果的统计
        """
        _recent_log(
            "VISION_RECENT_PROCESS_START",
            "开始处理最近图片",
            hours=hours,
            status="running",
        )

        # 解析批处理配置
        batch_enabled = batch_config and batch_config.get("enabled", False)
        batch_size = batch_config.get("batch_size", 10) if batch_config else 10

        if batch_enabled:
            _recent_log(
                "VISION_RECENT_BATCH_ENABLED",
                "最近图片处理启用批处理模式",
                batch_size=batch_size,
                status="running",
            )

        # 一次性获取所有图片数据
        recent_images = self._get_recent_images_cached(hours=hours)

        if not recent_images:
            _recent_log(
                "VISION_RECENT_NO_IMAGES",
                "最近图片处理未找到图像",
                level="WARNING",
                hours=hours,
                status="skipped",
            )
            return {
                "summary": {
                    "total_images": 0,
                    "time_range": {
                        "start": datetime.now() - timedelta(hours=hours),
                        "end": datetime.now(),
                    },
                    "room_stats": {},
                },
                "processing": {
                    "total_found": 0,
                    "total_processed": 0,
                    "total_success": 0,
                    "total_failed": 0,
                    "total_skipped": 0,
                    "room_stats": {},
                },
            }

        # 生成摘要信息
        summary = self._generate_summary(recent_images, hours)

        if show_summary:
            self._print_summary(summary)

        # 按库房分组并处理
        room_groups = {}
        for img in recent_images:
            room_id = img["room_id"]

            # 库房过滤
            if room_ids and room_id not in room_ids:
                continue

            if room_id not in room_groups:
                room_groups[room_id] = []
            room_groups[room_id].append(img)

        _recent_log(
            "VISION_RECENT_ROOM_DISTRIBUTION",
            "最近图片已完成库房分组",
            room_ids=",".join(sorted(room_groups.keys())),
            total_items=len(recent_images),
            status="running",
        )

        # 处理统计
        processing_stats = {
            "total_found": len(recent_images),
            "total_processed": 0,
            "total_success": 0,
            "total_failed": 0,
            "total_skipped": 0,
            "room_stats": {},
        }

        # 批处理统计
        batch_stats = {
            "total_batches": 0,
            "avg_batch_size": 0,
            "batch_processing_times": [],
        }

        # 处理每个库房的图片
        for room_id, images in room_groups.items():
            if self._is_room_stale(room_id, images, window_days=30):
                image_list = [
                    img.get("object_name") for img in images if img.get("object_name")
                ]
                _recent_log(
                    "VISION_RECENT_ROOM_STALE_SKIPPED",
                    "库房已超出入库窗口期，跳过最近图片处理",
                    level="WARNING",
                    room_id=room_id,
                    image_paths=",".join(image_list),
                    skipped_items=len(images),
                    status="skipped",
                )
                processing_stats["room_stats"][room_id] = {
                    "found": len(images),
                    "processed": 0,
                    "success": 0,
                    "failed": 0,
                    "skipped": len(images),
                    "reason": "room_stale_or_no_in_date",
                }
                processing_stats["total_skipped"] += len(images)
                continue

            # 按时间排序，处理最新的图片
            images.sort(key=lambda x: x["capture_time"], reverse=True)

            # 按日期分组进行优选 (Daily Top-5 Strategy)
            # 策略：如果单库房单日收集 > 10 张图片，则启用AI优选 Top 5
            # 目的：减少冗余计算，保留每天最具代表性的生长阶段图片
            from itertools import groupby

            optimized_images = []
            # images已按时间倒序，直接分组即可 (YYYY-MM-DD grouping)
            for date_str, group in groupby(
                images, key=lambda x: x["capture_time"].strftime("%Y-%m-%d")
            ):
                daily_images = list(group)

                # Check trigger condition per day
                if len(daily_images) > 10:
                    _recent_log(
                        "VISION_RECENT_DAILY_OPTIMIZATION_TRIGGERED",
                        "单日图片过多，启用 AI 优选",
                        room_id=room_id,
                        collection_date=date_str,
                        total_items=len(daily_images),
                        status="running",
                    )
                    try:
                        selected = self._select_best_images(daily_images)
                        optimized_images.extend(selected)
                    except Exception as e:
                        _recent_log(
                            "VISION_RECENT_DAILY_OPTIMIZATION_FAILED",
                            "AI 优选失败，回退到普通处理",
                            level="ERROR",
                            room_id=room_id,
                            collection_date=date_str,
                            status="failed",
                            error_type=type(e).__name__,
                            error_message=str(e),
                        )
                        optimized_images.extend(daily_images)
                else:
                    # 图片较少，全部保留
                    optimized_images.extend(daily_images)

            # 更新处理列表
            images = optimized_images

            # 限制处理数量 (Apply global limit if set)
            if max_images_per_room:
                images = images[:max_images_per_room]

            _recent_log(
                "VISION_RECENT_ROOM_PROCESS_START",
                "开始处理单个库房的最近图片",
                room_id=room_id,
                total_items=len(images),
                status="running",
            )

            if batch_enabled:
                room_stats, room_batch_stats = self._process_room_images_batch(
                    room_id, images, save_to_db, batch_size
                )
                # 合并批处理统计
                batch_stats["total_batches"] += room_batch_stats["batches"]
                batch_stats["batch_processing_times"].extend(
                    room_batch_stats["processing_times"]
                )
            else:
                room_stats = self._process_room_images(room_id, images, save_to_db)

            processing_stats["room_stats"][room_id] = room_stats

            # 更新总统计
            processing_stats["total_processed"] += room_stats["processed"]
            processing_stats["total_success"] += room_stats["success"]
            processing_stats["total_failed"] += room_stats["failed"]
            processing_stats["total_skipped"] += room_stats["skipped"]

        # 计算批处理统计
        if batch_enabled and batch_stats["total_batches"] > 0:
            batch_stats["avg_batch_size"] = (
                processing_stats["total_processed"] / batch_stats["total_batches"]
            )
            if batch_stats["batch_processing_times"]:
                avg_batch_time = sum(batch_stats["batch_processing_times"]) / len(
                    batch_stats["batch_processing_times"]
                )
                _recent_log(
                    "VISION_RECENT_BATCH_STATS",
                    "最近图片批处理统计完成",
                    total_batches=batch_stats["total_batches"],
                    avg_batch_size=round(batch_stats["avg_batch_size"], 2),
                    avg_batch_time=round(avg_batch_time, 2),
                    status="success",
                )

        _recent_log(
            "VISION_RECENT_PROCESS_FINISH",
            "最近图片处理完成",
            total_found=processing_stats["total_found"],
            total_items=processing_stats["total_processed"],
            successful_items=processing_stats["total_success"],
            failed_items=processing_stats["total_failed"],
            skipped_items=processing_stats["total_skipped"],
            success_rate=round(
                (
                    processing_stats["total_success"]
                    / processing_stats["total_processed"]
                    * 100
                ),
                2,
            )
            if processing_stats["total_processed"]
            else 0.0,
            status="success" if processing_stats["total_failed"] == 0 else "partial",
        )

        result = {"summary": summary, "processing": processing_stats}

        # 如果启用了批处理，添加批处理统计
        if batch_enabled:
            result["batch_stats"] = batch_stats

        return result

    def _generate_summary(
        self, recent_images: list[dict], hours: int
    ) -> dict[str, Any]:
        """生成图片摘要信息"""
        # 按库房统计
        room_stats = {}
        for img in recent_images:
            room_id = img["room_id"]
            if room_id not in room_stats:
                room_stats[room_id] = {
                    "count": 0,
                    "latest_time": None,
                    "earliest_time": None,
                }

            room_stats[room_id]["count"] += 1

            capture_time = img["capture_time"]
            if (
                not room_stats[room_id]["latest_time"]
                or capture_time > room_stats[room_id]["latest_time"]
            ):
                room_stats[room_id]["latest_time"] = capture_time

            if (
                not room_stats[room_id]["earliest_time"]
                or capture_time < room_stats[room_id]["earliest_time"]
            ):
                room_stats[room_id]["earliest_time"] = capture_time

        # 整体时间范围
        all_times = [img["capture_time"] for img in recent_images]
        time_range = {"start": min(all_times), "end": max(all_times)}

        return {
            "total_images": len(recent_images),
            "time_range": time_range,
            "room_stats": room_stats,
        }

    def _print_summary(self, summary: dict[str, Any]):
        """打印摘要信息"""
        _recent_log(
            "VISION_RECENT_SUMMARY",
            "最近图片摘要",
            total_items=summary["total_images"],
            time_start=str(summary["time_range"]["start"]),
            time_end=str(summary["time_range"]["end"]),
            room_count=len(summary["room_stats"]),
            status="success",
        )
        for room_id, stats in summary["room_stats"].items():
            _recent_log(
                "VISION_RECENT_SUMMARY_ROOM",
                "最近图片摘要库房明细",
                level="DEBUG",
                room_id=room_id,
                total_items=stats["count"],
                latest_time=str(stats["latest_time"]),
                earliest_time=str(stats["earliest_time"]),
                status="success",
            )

    def _process_room_images(
        self, room_id: str, images: list[dict], save_to_db: bool
    ) -> dict[str, int]:
        """处理单个库房的图片"""

        def _is_success_result(result: dict[str, Any] | None) -> bool:
            """统一 recent 单图处理成功判定。"""
            if not result:
                return False
            if not save_to_db:
                return True
            if result.get("saved_to_db", False):
                return True
            return result.get("skip_reason") == "no_environment_data"

        room_stats = {
            "found": len(images),
            "processed": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
        }

        for img in images:
            try:
                # 解析图片路径
                image_info = self.parser.parse_path(img["object_name"])

                if not image_info:
                    _recent_log(
                        "VISION_RECENT_ROOM_IMAGE_PARSE_FAILED",
                        "无法解析最近图片路径",
                        level="WARNING",
                        room_id=room_id,
                        image_path=img["object_name"],
                        status="failed",
                    )
                    room_stats["failed"] += 1
                    continue

                # 检查是否已处理
                if save_to_db and self.encoder._is_already_processed(
                    image_info.file_path
                ):
                    room_stats["skipped"] += 1
                    continue

                # 处理图片
                result = self.encoder.process_single_image(
                    image_info, save_to_db=save_to_db
                )

                if _is_success_result(result):
                    room_stats["success"] += 1
                    if result and result.get("saved_to_db", False):
                        _recent_log(
                            "VISION_RECENT_ROOM_IMAGE_SUCCESS",
                            "最近图片处理成功并已保存",
                            room_id=room_id,
                            image_name=image_info.file_name,
                            image_path=image_info.file_path,
                            status="success",
                        )
                    elif result and result.get("skip_reason") == "no_environment_data":
                        _recent_log(
                            "VISION_RECENT_ROOM_IMAGE_NO_ENV",
                            "最近图片处理成功但无环境数据",
                            level="DEBUG",
                            room_id=room_id,
                            image_name=image_info.file_name,
                            image_path=image_info.file_path,
                            status="success",
                        )
                    else:
                        _recent_log(
                            "VISION_RECENT_ROOM_IMAGE_SUCCESS_NO_SAVE",
                            "最近图片处理成功（测试模式未保存）",
                            level="DEBUG",
                            room_id=room_id,
                            image_name=image_info.file_name,
                            image_path=image_info.file_path,
                            status="success",
                        )
                else:
                    room_stats["failed"] += 1
                    if result:
                        _recent_log(
                            "VISION_RECENT_ROOM_IMAGE_FAILED",
                            "最近图片处理返回失败状态",
                            level="WARNING",
                            room_id=room_id,
                            image_name=image_info.file_name,
                            image_path=image_info.file_path,
                            status="failed",
                        )
                    else:
                        _recent_log(
                            "VISION_RECENT_ROOM_IMAGE_NONE_RESULT",
                            "最近图片处理返回空结果",
                            level="ERROR",
                            room_id=room_id,
                            image_name=image_info.file_name,
                            image_path=image_info.file_path,
                            status="failed",
                        )

                room_stats["processed"] += 1

            except Exception as e:
                _recent_log(
                    "VISION_RECENT_ROOM_IMAGE_EXCEPTION",
                    "最近图片处理异常",
                    level="ERROR",
                    room_id=room_id,
                    image_path=img["object_name"],
                    status="failed",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                room_stats["failed"] += 1
                room_stats["processed"] += 1

        _recent_log(
            "VISION_RECENT_ROOM_PROCESS_FINISH",
            "单个库房最近图片处理完成",
            room_id=room_id,
            total_items=room_stats["processed"],
            successful_items=room_stats["success"],
            failed_items=room_stats["failed"],
            skipped_items=room_stats["skipped"],
            found_items=room_stats["found"],
            status="success" if room_stats["failed"] == 0 else "partial",
        )

        return room_stats

    def _process_room_images_batch(
        self, room_id: str, images: list[dict], save_to_db: bool, batch_size: int
    ) -> tuple:
        """批处理模式处理单个库房的图片"""
        import time

        room_stats = {
            "found": len(images),
            "processed": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
        }

        batch_stats = {"batches": 0, "processing_times": []}

        # 将图片分批处理
        for i in range(0, len(images), batch_size):
            batch_start_time = time.time()
            batch = images[i : i + batch_size]
            batch_num = (i // batch_size) + 1

            _recent_log(
                "VISION_RECENT_ROOM_BATCH_START",
                "开始处理最近图片批次",
                room_id=room_id,
                batch_number=batch_num,
                batch_size=len(batch),
                processed_items=i + len(batch),
                total_items=len(images),
                status="running",
            )

            # 预处理批次：检查哪些图片需要处理
            batch_to_process = []
            for img in batch:
                try:
                    # 解析图片路径
                    image_info = self.parser.parse_path(img["object_name"])

                    if not image_info:
                        _recent_log(
                            "VISION_RECENT_ROOM_BATCH_PARSE_FAILED",
                            "批处理预处理阶段无法解析图片路径",
                            level="WARNING",
                            room_id=room_id,
                            image_path=img["object_name"],
                            batch_number=batch_num,
                            status="failed",
                        )
                        room_stats["failed"] += 1
                        continue

                    # 检查是否已处理
                    if save_to_db and self.encoder._is_already_processed(
                        image_info.file_path
                    ):
                        room_stats["skipped"] += 1
                        continue

                    batch_to_process.append((img, image_info))

                except Exception as e:
                    _recent_log(
                        "VISION_RECENT_ROOM_BATCH_PREPARE_FAILED",
                        "批处理预处理图片异常",
                        level="ERROR",
                        room_id=room_id,
                        image_path=img["object_name"],
                        batch_number=batch_num,
                        status="failed",
                        error_type=type(e).__name__,
                        error_message=str(e),
                    )
                    room_stats["failed"] += 1

            # 如果批次中有需要处理的图片，进行批处理
            if batch_to_process:
                batch_results = self._process_image_batch(batch_to_process, save_to_db)

                # 更新统计
                for result in batch_results:
                    room_stats["processed"] += 1
                    if result["success"]:
                        room_stats["success"] += 1
                    else:
                        room_stats["failed"] += 1

            batch_end_time = time.time()
            batch_processing_time = batch_end_time - batch_start_time
            batch_stats["processing_times"].append(batch_processing_time)
            batch_stats["batches"] += 1

            _recent_log(
                "VISION_RECENT_ROOM_BATCH_FINISH",
                "最近图片批次处理完成",
                room_id=room_id,
                batch_number=batch_num,
                batch_size=len(batch),
                processed_items=len(batch_to_process),
                processing_time=round(batch_processing_time, 2),
                status="success",
            )

        _recent_log(
            "VISION_RECENT_ROOM_BATCH_SUMMARY",
            "单个库房最近图片批处理完成",
            room_id=room_id,
            total_batches=batch_stats["batches"],
            total_items=room_stats["processed"],
            successful_items=room_stats["success"],
            failed_items=room_stats["failed"],
            skipped_items=room_stats["skipped"],
            status="success" if room_stats["failed"] == 0 else "partial",
        )

        return room_stats, batch_stats

    def _process_image_batch(
        self, batch_to_process: list[tuple], save_to_db: bool
    ) -> list[dict]:
        """处理一批图片"""
        batch_results = []

        # 批量获取图片数据
        images_data = []
        for img, image_info in batch_to_process:
            try:
                # 从MinIO获取图像
                image = self.minio_client.get_image(image_info.file_path)
                if image is None:
                    _recent_log(
                        "VISION_RECENT_BATCH_IMAGE_FETCH_FAILED",
                        "批量处理阶段获取图像失败",
                        level="WARNING",
                        image_name=image_info.file_name,
                        image_path=image_info.file_path,
                        status="failed",
                    )
                    batch_results.append({"success": False, "image_info": image_info})
                    continue

                images_data.append(
                    {"image": image, "image_info": image_info, "img_meta": img}
                )

            except Exception as e:
                _recent_log(
                    "VISION_RECENT_BATCH_IMAGE_FETCH_EXCEPTION",
                    "批量处理阶段获取图像异常",
                    level="ERROR",
                    image_name=image_info.file_name,
                    image_path=image_info.file_path,
                    status="failed",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                batch_results.append({"success": False, "image_info": image_info})

        # 如果有成功获取的图片，进行批量处理
        if images_data:
            # 检查是否可以使用批量编码
            if hasattr(self.encoder, "process_image_batch"):
                # 使用批量处理方法
                try:
                    batch_processing_results = self.encoder.process_image_batch(
                        images_data, save_to_db
                    )
                    batch_results.extend(batch_processing_results)
                except Exception as e:
                    _recent_log(
                        "VISION_RECENT_BATCH_ENCODER_FAILED",
                        "编码器批量处理失败，回退到单张处理",
                        level="ERROR",
                        total_items=len(images_data),
                        status="failed",
                        error_type=type(e).__name__,
                        error_message=str(e),
                    )
                    # 回退到单张处理
                    for img_data in images_data:
                        try:
                            result = self.encoder.process_single_image(
                                img_data["image_info"], save_to_db=save_to_db
                            )
                            success = result is not None and (
                                not save_to_db or result.get("saved_to_db", False)
                            )
                            batch_results.append(
                                {
                                    "success": success,
                                    "image_info": img_data["image_info"],
                                }
                            )
                        except Exception as e2:
                            _recent_log(
                                "VISION_RECENT_BATCH_FALLBACK_ITEM_FAILED",
                                "批量回退单张处理失败",
                                level="ERROR",
                                image_name=img_data["image_info"].file_name,
                                image_path=img_data["image_info"].file_path,
                                status="failed",
                                error_type=type(e2).__name__,
                                error_message=str(e2),
                            )
                            batch_results.append(
                                {"success": False, "image_info": img_data["image_info"]}
                            )
            else:
                # 编码器不支持批量处理，使用单张处理但优化调用
                for img_data in images_data:
                    try:
                        result = self.encoder.process_single_image(
                            img_data["image_info"], save_to_db=save_to_db
                        )
                        success = result is not None and (
                            not save_to_db or result.get("saved_to_db", False)
                        )
                        batch_results.append(
                            {"success": success, "image_info": img_data["image_info"]}
                        )

                        if success:
                            _recent_log(
                                "VISION_RECENT_BATCH_ITEM_SUCCESS",
                                "非批量编码器路径下单张处理成功",
                                level="DEBUG",
                                image_name=img_data["image_info"].file_name,
                                image_path=img_data["image_info"].file_path,
                                status="success",
                            )
                        else:
                            _recent_log(
                                "VISION_RECENT_BATCH_ITEM_FAILED",
                                "非批量编码器路径下单张处理失败",
                                level="WARNING",
                                image_name=img_data["image_info"].file_name,
                                image_path=img_data["image_info"].file_path,
                                status="failed",
                            )

                    except Exception as e:
                        _recent_log(
                            "VISION_RECENT_BATCH_ITEM_EXCEPTION",
                            "非批量编码器路径下单张处理异常",
                            level="ERROR",
                            image_name=img_data["image_info"].file_name,
                            image_path=img_data["image_info"].file_path,
                            status="failed",
                            error_type=type(e).__name__,
                            error_message=str(e),
                        )
                        batch_results.append(
                            {"success": False, "image_info": img_data["image_info"]}
                        )

        return batch_results

    def process_recent_images(
        self,
        hours: int = 1,
        room_ids: list[str] | None = None,
        max_images_per_room: int | None = None,
        save_to_db: bool = True,
    ) -> dict[str, Any]:
        """
        处理最近指定小时内的图片（保持向后兼容）

        Args:
            hours: 查询最近多少小时的数据
            room_ids: 指定库房列表，如果为None则处理所有库房
            max_images_per_room: 每个库房最多处理多少张图片
            save_to_db: 是否保存到数据库

        Returns:
            处理结果统计
        """
        result = self.get_recent_image_summary_and_process(
            hours=hours,
            room_ids=room_ids,
            max_images_per_room=max_images_per_room,
            save_to_db=save_to_db,
            show_summary=False,
        )
        return result["processing"]

    def get_recent_image_summary(self, hours: int = 1) -> dict[str, Any]:
        """
        获取最近图片的摘要信息（保持向后兼容）

        Args:
            hours: 查询最近多少小时的数据

        Returns:
            摘要信息
        """
        recent_images = self._get_recent_images_cached(hours=hours)

        if not recent_images:
            return {
                "total_images": 0,
                "time_range": {
                    "start": datetime.now() - timedelta(hours=hours),
                    "end": datetime.now(),
                },
                "room_stats": {},
            }

        summary = self._generate_summary(recent_images, hours)
        _recent_log(
            "VISION_RECENT_SUMMARY_READY",
            "最近图片摘要已生成",
            hours=hours,
            total_items=len(recent_images),
            room_ids=",".join(sorted(summary["room_stats"].keys())),
            status="success",
        )

        return summary

    def process_room_recent_images(
        self,
        room_id: str,
        hours: int = 1,
        max_images: int | None = None,
        save_to_db: bool = True,
    ) -> dict[str, Any]:
        """
        处理指定库房最近的图片

        Args:
            room_id: 库房号
            hours: 查询最近多少小时的数据
            max_images: 最多处理多少张图片
            save_to_db: 是否保存到数据库

        Returns:
            处理结果统计
        """
        _recent_log(
            "VISION_RECENT_ROOM_DIRECT_START",
            "开始处理指定库房最近图片",
            room_id=room_id,
            hours=hours,
            status="running",
        )

        # 获取指定库房的最近图片
        recent_images = self.minio_client.list_recent_images(
            room_id=room_id, hours=hours
        )

        if not recent_images:
            _recent_log(
                "VISION_RECENT_ROOM_DIRECT_NO_IMAGES",
                "指定库房未找到最近图片",
                level="WARNING",
                room_id=room_id,
                hours=hours,
                status="skipped",
            )
            return {
                "room_id": room_id,
                "found": 0,
                "processed": 0,
                "success": 0,
                "failed": 0,
                "skipped": 0,
            }

        _recent_log(
            "VISION_RECENT_ROOM_DIRECT_FOUND",
            "指定库房最近图片查询完成",
            room_id=room_id,
            hours=hours,
            total_items=len(recent_images),
            status="success",
        )

        # 按时间排序，处理最新的图片
        recent_images.sort(key=lambda x: x["capture_time"], reverse=True)

        # 限制处理数量
        if max_images:
            recent_images = recent_images[:max_images]
            _recent_log(
                "VISION_RECENT_ROOM_DIRECT_LIMIT_APPLIED",
                "指定库房最近图片已应用处理数量限制",
                room_id=room_id,
                total_items=len(recent_images),
                max_images=max_images,
                status="success",
            )

        stats = {
            "room_id": room_id,
            "found": len(recent_images),
            "processed": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
        }

        for img in recent_images:
            try:
                # 解析图片路径
                image_info = self.parser.parse_path(img["object_name"])

                if not image_info:
                    _recent_log(
                        "VISION_RECENT_ROOM_DIRECT_PARSE_FAILED",
                        "指定库房处理时无法解析图片路径",
                        level="WARNING",
                        room_id=room_id,
                        image_path=img["object_name"],
                        status="failed",
                    )
                    stats["failed"] += 1
                    continue

                # 检查是否已处理
                if save_to_db and self.encoder._is_already_processed(
                    image_info.file_path
                ):
                    _recent_log(
                        "VISION_RECENT_ROOM_DIRECT_ITEM_SKIPPED",
                        "指定库房处理时跳过已处理图片",
                        room_id=room_id,
                        image_name=image_info.file_name,
                        image_path=image_info.file_path,
                        status="skipped",
                    )
                    stats["skipped"] += 1
                    continue

                # 处理图片
                _recent_log(
                    "VISION_RECENT_ROOM_DIRECT_ITEM_START",
                    "开始处理指定库房图片",
                    room_id=room_id,
                    image_name=image_info.file_name,
                    image_path=image_info.file_path,
                    status="running",
                )
                result = self.encoder.process_single_image(
                    image_info, save_to_db=save_to_db
                )

                success = bool(result) and (
                    (not save_to_db)
                    or result.get("saved_to_db", False)
                    or result.get("skip_reason") == "no_environment_data"
                )

                if success:
                    if result and result.get("saved_to_db", False):
                        stats["success"] += 1
                        _recent_log(
                            "VISION_RECENT_ROOM_DIRECT_ITEM_SUCCESS",
                            "指定库房图片处理成功并已保存",
                            room_id=room_id,
                            image_name=image_info.file_name,
                            image_path=image_info.file_path,
                            status="success",
                        )
                    elif result and result.get("skip_reason") == "no_environment_data":
                        stats["success"] += 1  # 算作成功，只是没有环境数据
                        _recent_log(
                            "VISION_RECENT_ROOM_DIRECT_ITEM_NO_ENV",
                            "指定库房图片处理成功但无环境数据",
                            room_id=room_id,
                            image_name=image_info.file_name,
                            image_path=image_info.file_path,
                            status="success",
                        )
                    else:
                        stats["success"] += 1
                        _recent_log(
                            "VISION_RECENT_ROOM_DIRECT_ITEM_SUCCESS_NO_SAVE",
                            "指定库房图片处理成功（测试模式未保存）",
                            level="DEBUG",
                            room_id=room_id,
                            image_name=image_info.file_name,
                            image_path=image_info.file_path,
                            status="success",
                        )
                else:
                    stats["failed"] += 1
                    if result:
                        _recent_log(
                            "VISION_RECENT_ROOM_DIRECT_ITEM_FAILED",
                            "指定库房图片处理失败",
                            level="WARNING",
                            room_id=room_id,
                            image_name=image_info.file_name,
                            image_path=image_info.file_path,
                            status="failed",
                        )
                    else:
                        _recent_log(
                            "VISION_RECENT_ROOM_DIRECT_ITEM_NONE_RESULT",
                            "指定库房图片处理返回空结果",
                            level="ERROR",
                            room_id=room_id,
                            image_name=image_info.file_name,
                            image_path=image_info.file_path,
                            status="failed",
                        )

                stats["processed"] += 1

            except Exception as e:
                _recent_log(
                    "VISION_RECENT_ROOM_DIRECT_ITEM_EXCEPTION",
                    "指定库房图片处理异常",
                    level="ERROR",
                    room_id=room_id,
                    image_path=img["object_name"],
                    status="failed",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                stats["failed"] += 1
                stats["processed"] += 1

        _recent_log(
            "VISION_RECENT_ROOM_DIRECT_FINISH",
            "指定库房最近图片处理完成",
            room_id=room_id,
            found_items=stats["found"],
            total_items=stats["processed"],
            successful_items=stats["success"],
            failed_items=stats["failed"],
            skipped_items=stats["skipped"],
            status="success" if stats["failed"] == 0 else "partial",
        )

        return stats

    def get_recent_image_summary(self, hours: int = 1) -> dict[str, Any]:
        """
        获取最近图片的摘要信息

        Args:
            hours: 查询最近多少小时的数据

        Returns:
            摘要信息
        """
        _recent_log(
            "VISION_RECENT_SUMMARY_QUERY_START",
            "开始获取最近图片摘要",
            hours=hours,
            status="running",
        )

        # 获取最近的图片
        recent_images = self.minio_client.list_recent_images(hours=hours)

        if not recent_images:
            return {
                "total_images": 0,
                "time_range": {
                    "start": datetime.now() - timedelta(hours=hours),
                    "end": datetime.now(),
                },
                "room_stats": {},
            }

        # 按库房统计
        room_stats = {}
        for img in recent_images:
            room_id = img["room_id"]
            if room_id not in room_stats:
                room_stats[room_id] = {
                    "count": 0,
                    "latest_time": None,
                    "earliest_time": None,
                }

            room_stats[room_id]["count"] += 1

            capture_time = img["capture_time"]
            if (
                not room_stats[room_id]["latest_time"]
                or capture_time > room_stats[room_id]["latest_time"]
            ):
                room_stats[room_id]["latest_time"] = capture_time

            if (
                not room_stats[room_id]["earliest_time"]
                or capture_time < room_stats[room_id]["earliest_time"]
            ):
                room_stats[room_id]["earliest_time"] = capture_time

        # 整体时间范围
        all_times = [img["capture_time"] for img in recent_images]
        time_range = {"start": min(all_times), "end": max(all_times)}

        summary = {
            "total_images": len(recent_images),
            "time_range": time_range,
            "room_stats": room_stats,
        }

        _recent_log(
            "VISION_RECENT_SUMMARY_QUERY_FINISH",
            "最近图片摘要获取完成",
            hours=hours,
            total_items=len(recent_images),
            room_ids=",".join(sorted(room_stats.keys())),
            status="success",
        )

        return summary


def create_recent_image_processor(
    shared_encoder=None, shared_minio_client=None
) -> RecentImageProcessor:
    """
    创建最近图片处理器实例

    Args:
        shared_encoder: 共享的编码器实例，避免重复初始化
        shared_minio_client: 共享的MinIO客户端实例，避免重复初始化
    """
    return RecentImageProcessor(
        shared_encoder=shared_encoder, shared_minio_client=shared_minio_client
    )


if __name__ == "__main__":
    # 测试代码 - 使用优化后的整合方法
    from utils.minio_client import create_minio_client
    from vision.mushroom_image_encoder import create_mushroom_encoder

    _recent_log(
        "VISION_RECENT_SELFTEST_INIT",
        "开始初始化 recent image processor 自检组件",
        status="running",
    )

    # 创建共享实例，避免重复初始化
    shared_encoder = create_mushroom_encoder(load_clip=False)
    shared_minio_client = create_minio_client()

    processor = create_recent_image_processor(
        shared_encoder=shared_encoder, shared_minio_client=shared_minio_client
    )

    # 使用整合的方法：一次调用完成摘要和处理
    result = processor.get_recent_image_summary_and_process(
        hours=1, max_images_per_room=1, save_to_db=True, show_summary=True
    )

    _recent_log(
        "VISION_RECENT_SELFTEST_FINISH",
        "recent image processor 自检完成",
        total_found=result["processing"]["total_found"],
        total_items=result["processing"]["total_processed"],
        successful_items=result["processing"]["total_success"],
        failed_items=result["processing"]["total_failed"],
        skipped_items=result["processing"]["total_skipped"],
        status="success" if result["processing"]["total_failed"] == 0 else "partial",
    )

    for room_id, stats in result["processing"]["room_stats"].items():
        _recent_log(
            "VISION_RECENT_SELFTEST_ROOM_DETAIL",
            "recent image processor 自检库房明细",
            level="DEBUG",
            room_id=room_id,
            found_items=stats.get("found", 0),
            total_items=stats.get("processed", 0),
            successful_items=stats.get("success", 0),
            failed_items=stats.get("failed", 0),
            skipped_items=stats.get("skipped", 0),
            status="success" if stats.get("failed", 0) == 0 else "partial",
        )
