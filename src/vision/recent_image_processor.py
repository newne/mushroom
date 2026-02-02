"""
最近图片处理器
用于处理最近时间段内的图片数据，支持定期处理和增量处理
"""

from datetime import date, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy.orm import sessionmaker

from global_const.global_const import pgsql_engine
from utils.create_table import MushroomImageEmbedding
from utils.minio_client import create_minio_client

from .mushroom_image_encoder import create_mushroom_encoder
from .mushroom_image_processor import MushroomImagePathParser


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

        logger.debug("图片处理器初始化完成")

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
            logger.warning(f"[IMG-000] 入库记录查询失败 | 库房: {room_id} | 错误: {e}")
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
        logger.info(f"执行AI优选策略: 从 {len(images)} 张图片中筛选 Top 5")

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
                        logger.debug(
                            f"[FILTER] Quality fail: {score} < {self.encoder.quality_threshold} | {img_dict.get('object_name')}"
                        )
                        skipped_count += 1
                        continue

                # 关键词过滤
                if self.encoder.required_keywords:
                    desc_lower = description.lower()
                    if not any(
                        k.lower() in desc_lower for k in self.encoder.required_keywords
                    ):
                        logger.debug(
                            f"[FILTER] Keyword fail: '{description[:50]}...' not in {self.encoder.required_keywords} | {img_dict.get('object_name')}"
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
                logger.error(f"优选过程出错 {img_dict.get('object_name')}: {e}")
                continue

        # 2. 排序并取 Top 5
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)
        top_candidates = scored_candidates[:5]

        selected_images = [cand["img_dict"] for cand in top_candidates]
        logger.info(
            f"优选完成: 选中 {len(selected_images)} 张 (筛选池: {len(images)}, 过滤: {skipped_count}, 最高分: {scored_candidates[0]['score'] if scored_candidates else 'N/A'})"
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
        logger.info(f"[IMG-001] 开始处理图片 | 时间范围: 最近{hours}小时")

        # 解析批处理配置
        batch_enabled = batch_config and batch_config.get("enabled", False)
        batch_size = batch_config.get("batch_size", 10) if batch_config else 10

        if batch_enabled:
            logger.info(f"[IMG-001-BATCH] 批处理模式启用 | 批大小: {batch_size}")

        # 一次性获取所有图片数据
        recent_images = self._get_recent_images_cached(hours=hours)

        if not recent_images:
            logger.warning(f"[IMG-002] 未找到图片 | 时间范围: 最近{hours}小时")
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

        logger.info(
            f"[IMG-003] 图片分布 | 库房: {sorted(room_groups.keys())}, 总数: {len(recent_images)}张"
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
                logger.warning(
                    f"[IMG-003] 空库房/未入库场景 | 库房: {room_id} | "
                    "入库时间超过30天窗口，跳过处理 | 图片列表: "
                    f"{image_list}"
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
                    logger.info(
                        f"[IMG-OPT] 库房 {room_id} 在 {date_str} 图片数量 {len(daily_images)} > 10，启用AI智能优选 (Top 5)"
                    )
                    try:
                        selected = self._select_best_images(daily_images)
                        optimized_images.extend(selected)
                    except Exception as e:
                        logger.error(
                            f"[IMG-OPT] {date_str} AI优选失败，回退到普通处理: {e}"
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

            logger.info(
                f"[IMG-004] 开始处理库房 | 库房: {room_id}, 图片数: {len(images)}张"
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
                logger.info(
                    f"[IMG-005-BATCH] 批处理统计 | 总批数: {batch_stats['total_batches']}, "
                    f"平均批大小: {batch_stats['avg_batch_size']:.1f}, 平均批处理时间: {avg_batch_time:.2f}s"
                )

        logger.info(
            f"[IMG-005] 处理完成 | "
            f"找到: {processing_stats['total_found']}张, "
            f"处理: {processing_stats['total_processed']}张, "
            f"成功: {processing_stats['total_success']}张, "
            f"失败: {processing_stats['total_failed']}张, "
            f"跳过: {processing_stats['total_skipped']}张"
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
        print(f"总图片数: {summary['total_images']}")
        print(
            f"时间范围: {summary['time_range']['start']} ~ {summary['time_range']['end']}"
        )
        print("各库房统计:")
        for room_id, stats in summary["room_stats"].items():
            print(f"库房{room_id}: {stats['count']}张 (最新: {stats['latest_time']})")

    def _process_room_images(
        self, room_id: str, images: list[dict], save_to_db: bool
    ) -> dict[str, int]:
        """处理单个库房的图片"""
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
                    logger.warning(f"无法解析图片路径: {img['object_name']}")
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

                if result:
                    if result.get("saved_to_db", False):
                        room_stats["success"] += 1
                        logger.info(
                            f"[IMG-006] 处理成功 | 文件: {image_info.file_name}"
                        )
                    elif result.get("skip_reason") == "no_environment_data":
                        room_stats["success"] += 1
                        logger.debug(f"处理成功但无环境数据: {image_info.file_name}")
                    else:
                        room_stats["failed"] += 1
                        logger.warning(
                            f"[IMG-007] 处理失败 | 文件: {image_info.file_name}"
                        )
                else:
                    room_stats["failed"] += 1
                    logger.error(
                        f"[IMG-008] 处理异常 | 文件: {image_info.file_name}, 返回: None"
                    )

                room_stats["processed"] += 1

            except Exception as e:
                logger.error(f"处理图片异常 {img['object_name']}: {e}")
                room_stats["failed"] += 1
                room_stats["processed"] += 1

        logger.info(
            f"[IMG-009] 库房处理完成 | "
            f"库房: {room_id}, "
            f"处理: {room_stats['processed']}张, "
            f"成功: {room_stats['success']}张, "
            f"失败: {room_stats['failed']}张, "
            f"跳过: {room_stats['skipped']}张"
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

            logger.info(
                f"[IMG-BATCH-{batch_num}] 处理批次 | 库房: {room_id}, 批大小: {len(batch)}, "
                f"进度: {i + len(batch)}/{len(images)}"
            )

            # 预处理批次：检查哪些图片需要处理
            batch_to_process = []
            for img in batch:
                try:
                    # 解析图片路径
                    image_info = self.parser.parse_path(img["object_name"])

                    if not image_info:
                        logger.warning(f"无法解析图片路径: {img['object_name']}")
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
                    logger.error(f"预处理图片异常 {img['object_name']}: {e}")
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

            logger.info(
                f"[IMG-BATCH-{batch_num}] 批次完成 | 耗时: {batch_processing_time:.2f}s, "
                f"处理: {len(batch_to_process)}张"
            )

        logger.info(
            f"[IMG-009-BATCH] 库房批处理完成 | "
            f"库房: {room_id}, "
            f"批数: {batch_stats['batches']}, "
            f"处理: {room_stats['processed']}张, "
            f"成功: {room_stats['success']}张, "
            f"失败: {room_stats['failed']}张, "
            f"跳过: {room_stats['skipped']}张"
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
                    logger.warning(
                        f"[IMG-BATCH] 获取图像失败 | 文件: {image_info.file_name}"
                    )
                    batch_results.append({"success": False, "image_info": image_info})
                    continue

                images_data.append(
                    {"image": image, "image_info": image_info, "img_meta": img}
                )

            except Exception as e:
                logger.error(
                    f"[IMG-BATCH] 获取图像异常 | 文件: {image_info.file_name}, 错误: {e}"
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
                    logger.error(f"[IMG-BATCH] 批量处理失败，回退到单张处理: {e}")
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
                            logger.error(
                                f"[IMG-BATCH] 单张处理也失败: {img_data['image_info'].file_name}, 错误: {e2}"
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
                            logger.debug(
                                f"[IMG-BATCH] 处理成功 | 文件: {img_data['image_info'].file_name}"
                            )
                        else:
                            logger.warning(
                                f"[IMG-BATCH] 处理失败 | 文件: {img_data['image_info'].file_name}"
                            )

                    except Exception as e:
                        logger.error(
                            f"[IMG-BATCH] 处理异常 | 文件: {img_data['image_info'].file_name}, 错误: {e}"
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
        logger.info(
            f"最近 {hours} 小时图片摘要: 总计 {len(recent_images)} 张, "
            f"涉及库房 {sorted(summary['room_stats'].keys())}"
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
        logger.info(f"处理库房 {room_id} 最近 {hours} 小时的图片")

        # 获取指定库房的最近图片
        recent_images = self.minio_client.list_recent_images(
            room_id=room_id, hours=hours
        )

        if not recent_images:
            logger.warning(f"库房 {room_id} 未找到最近 {hours} 小时的图片")
            return {
                "room_id": room_id,
                "found": 0,
                "processed": 0,
                "success": 0,
                "failed": 0,
                "skipped": 0,
            }

        logger.info(
            f"库房 {room_id} 找到最近 {hours} 小时的图片: {len(recent_images)} 张"
        )

        # 按时间排序，处理最新的图片
        recent_images.sort(key=lambda x: x["capture_time"], reverse=True)

        # 限制处理数量
        if max_images:
            recent_images = recent_images[:max_images]
            logger.info(f"限制库房 {room_id} 处理数量为: {len(recent_images)} 张")

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
                    logger.warning(f"无法解析图片路径: {img['object_name']}")
                    stats["failed"] += 1
                    continue

                # 检查是否已处理
                if save_to_db and self.encoder._is_already_processed(
                    image_info.file_path
                ):
                    logger.info(f"跳过已处理图片: {image_info.file_name}")
                    stats["skipped"] += 1
                    continue

                # 处理图片
                logger.info(f"处理图片: {image_info.file_name}")
                result = self.encoder.process_single_image(
                    image_info, save_to_db=save_to_db
                )

                if result:
                    if result.get("saved_to_db", False):
                        stats["success"] += 1
                        logger.info(f"成功处理并保存: {image_info.file_name}")
                    elif result.get("skip_reason") == "no_environment_data":
                        stats["success"] += 1  # 算作成功，只是没有环境数据
                        logger.info(f"成功处理但无环境数据: {image_info.file_name}")
                    else:
                        stats["failed"] += 1
                        logger.warning(f"处理失败: {image_info.file_name}")
                else:
                    stats["failed"] += 1
                    logger.error(f"处理返回None: {image_info.file_name}")

                stats["processed"] += 1

            except Exception as e:
                logger.error(f"处理图片异常 {img['object_name']}: {e}")
                stats["failed"] += 1
                stats["processed"] += 1

        logger.info(
            f"库房 {room_id} 处理完成: 找到={stats['found']}, 处理={stats['processed']}, "
            f"成功={stats['success']}, 失败={stats['failed']}, 跳过={stats['skipped']}"
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
        logger.info(f"获取最近 {hours} 小时的图片摘要")

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

        logger.info(
            f"最近 {hours} 小时图片摘要: 总计 {len(recent_images)} 张, "
            f"涉及库房 {sorted(room_stats.keys())}"
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
    print("=== 初始化共享组件 ===")
    from utils.minio_client import create_minio_client
    from vision.mushroom_image_encoder import create_mushroom_encoder

    # 创建共享实例，避免重复初始化
    shared_encoder = create_mushroom_encoder(load_clip=False)
    shared_minio_client = create_minio_client()

    processor = create_recent_image_processor(
        shared_encoder=shared_encoder, shared_minio_client=shared_minio_client
    )

    # 使用整合的方法：一次调用完成摘要和处理
    print("\n=== 整合处理最近1小时图片 ===")
    result = processor.get_recent_image_summary_and_process(
        hours=1, max_images_per_room=1, save_to_db=True, show_summary=True
    )

    print(
        f"\n处理结果: 找到={result['processing']['total_found']}, "
        f"处理={result['processing']['total_processed']}, "
        f"成功={result['processing']['total_success']}, "
        f"失败={result['processing']['total_failed']}, "
        f"跳过={result['processing']['total_skipped']}"
    )

    print("各库房详情:")
    for room_id, stats in result["processing"]["room_stats"].items():
        print(
            f"  库房{room_id}: 找到={stats.get('found', 0)}, "
            f"处理={stats.get('processed', 0)}, 成功={stats.get('success', 0)}, "
            f"失败={stats.get('failed', 0)}, 跳过={stats.get('skipped', 0)}"
        )
