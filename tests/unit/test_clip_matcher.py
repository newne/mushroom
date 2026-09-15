"""
Unit tests for CLIPMatcher

Tests cover:
- Normal operation with valid inputs（历史用例的取数**委托给 DataExtractor**）
- Edge cases (empty results, no matches)
- Similarity score → 百分比 → 置信档 的映射
- Error handling (extractor errors, invalid inputs)
- Confidence level calculation（**唯一**的阈值规则）
- Multi-image boost（纯函数）

2026-09-15 重写说明：这个文件原先 mock 的是 `db_engine.connect()`，即假设
`CLIPMatcher` 自己发 SQL、并把 pgvector 距离用 `_distance_to_similarity` 换算成相似度。
实现早已改成"委托 `DataExtractor.find_top_similar_historical_cases`（用余弦相似度 + 环境
参数加权出 `combined_similarity`）"，那个私有方法和那条 SQL 都不在了——于是 9 个用例
一直在失败。这里按**当前实现**重写，并把当初只有约定、没有测试的两件事钉住：
`_distance_to_similarity` 没了（相似度由 DataExtractor 算），置信档只有一份规则。
"""

import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from decision_analysis.clip_matcher import CLIPMatcher

EXTRACTOR = "decision_analysis.data_extractor.DataExtractor"


class TestCLIPMatcher:
    """Unit tests for CLIPMatcher class"""

    @pytest.fixture
    def mock_engine(self):
        """Create a mock database engine"""
        return Mock()

    @pytest.fixture
    def matcher(self, mock_engine):
        """Create a CLIPMatcher instance with mock engine"""
        return CLIPMatcher(mock_engine)

    @pytest.fixture
    def sample_embedding(self):
        """Create a sample 512-dimensional embedding"""
        return np.random.rand(512).astype(np.float32)

    def test_init(self, mock_engine):
        """Test CLIPMatcher initialization"""
        matcher = CLIPMatcher(mock_engine)
        assert matcher.db_engine == mock_engine

    # ---------- 置信档：唯一规则 ----------

    def test_calculate_confidence_level_high(self, matcher):
        """high：> 60%"""
        assert matcher._calculate_confidence_level(100.0) == "high"
        assert matcher._calculate_confidence_level(80.0) == "high"
        assert matcher._calculate_confidence_level(61.0) == "high"

    def test_calculate_confidence_level_medium(self, matcher):
        """medium：20% ~ 60%（含两端）"""
        assert matcher._calculate_confidence_level(60.0) == "medium"
        assert matcher._calculate_confidence_level(40.0) == "medium"
        assert matcher._calculate_confidence_level(20.0) == "medium"

    def test_calculate_confidence_level_low(self, matcher):
        """low：< 20%"""
        assert matcher._calculate_confidence_level(19.9) == "low"
        assert matcher._calculate_confidence_level(10.0) == "low"
        assert matcher._calculate_confidence_level(0.0) == "low"

    def test_find_similar_cases_uses_the_same_rule(self, matcher, sample_embedding):
        """取数路径与纯函数必须给出**同一个**档。

        曾经的实现里 `find_similar_cases` 内联了一份 80/50 的阈值，而 `_calculate_confidence_level`
        是 60/20——同一个 70 分会被一处叫 high、另一处叫 medium。这条用例把它钉死：
        取数路径的档必须等于纯函数对同一个分数的判断。
        """
        rows = self._rows("611", 12, "2024-01-01T12:00:00", [0.95, 0.70, 0.45, 0.10])
        with patch(EXTRACTOR) as extractor:
            extractor.return_value.find_top_similar_historical_cases.return_value = rows
            cases = matcher.find_similar_cases(query_embedding=sample_embedding, room_id="611",
                                              in_date=date(2024, 1, 1), growth_day=12)
        assert [c.confidence_level for c in cases] == [
            matcher._calculate_confidence_level(c.similarity_score) for c in cases
        ]
        assert [c.confidence_level for c in cases] == ["high", "high", "medium", "low"]

    # ---------- 取数：委托 DataExtractor，本类只做映射 ----------

    @staticmethod
    def _rows(room_id, growth_day, ts, scores, *, with_env=True):
        data = {
            "room_id": [room_id] * len(scores),
            "growth_day": [growth_day] * len(scores),
            "collection_datetime": [datetime.fromisoformat(ts)] * len(scores),
            "combined_similarity": scores,
            "air_cooler_config": [{"temp_set": 18.0}] * len(scores),
            "fresh_fan_config": [{"mode": 1}] * len(scores),
            "humidifier_config": [{"on": 90}] * len(scores),
            "light_config": [{"model": 1}] * len(scores),
        }
        if with_env:
            data["temperature"] = [18.5] * len(scores)
            data["humidity"] = [95.0] * len(scores)
            data["co2"] = [2000.0] * len(scores)
        return pd.DataFrame(data)

    def test_find_similar_cases_maps_rows_to_cases(self, matcher, sample_embedding):
        """`combined_similarity` 是 0–1 的比例，出来必须是百分比，字段逐个对上。"""
        rows = self._rows("611", 10, "2024-01-01T12:00:00", [0.87])
        with patch(EXTRACTOR) as extractor:
            extractor.return_value.find_top_similar_historical_cases.return_value = rows
            cases = matcher.find_similar_cases(query_embedding=sample_embedding, room_id="611",
                                              in_date=date(2024, 1, 1), growth_day=10, top_k=3)

        assert len(cases) == 1
        case = cases[0]
        assert case.similarity_score == pytest.approx(87.0)
        assert case.confidence_level == "high"
        assert case.room_id == "611"
        assert case.growth_day == 10
        assert case.collection_time == datetime(2024, 1, 1, 12, 0, 0)
        assert (case.temperature, case.humidity, case.co2) == (18.5, 95.0, 2000.0)
        assert case.air_cooler_params == {"temp_set": 18.0}
        assert case.fresh_air_params == {"mode": 1}
        assert case.humidifier_params == {"on": 90}
        assert case.grow_light_params == {"model": 1}

    def test_find_similar_cases_passes_filters_through(self, matcher, sample_embedding):
        """库房/批次/天数窗口/权重/top_k 必须**原样**传给取数层——它们是排除当前批次的依据。"""
        with patch(EXTRACTOR) as extractor:
            extractor.return_value.find_top_similar_historical_cases.return_value = pd.DataFrame()
            matcher.find_similar_cases(
                query_embedding=sample_embedding, room_id="612", in_date=date(2024, 1, 15),
                growth_day=10, top_k=5, date_window_days=7, growth_day_window=3,
                embedding_similarity_weight=0.6, env_similarity_weight=0.4,
            )
        kwargs = extractor.return_value.find_top_similar_historical_cases.call_args.kwargs
        assert kwargs["room_id"] == "612"
        assert kwargs["current_in_date"] == date(2024, 1, 15)
        assert kwargs["target_growth_day"] == 10
        assert kwargs["top_k"] == 5
        assert kwargs["date_window_days"] == 7
        assert kwargs["growth_day_window"] == 3
        assert (kwargs["embedding_similarity_weight"], kwargs["env_similarity_weight"]) == (0.6, 0.4)

    def test_find_similar_cases_empty_result(self, matcher, sample_embedding):
        """取数层给空表 ⇒ 空列表（不是异常，也不是 None）。"""
        with patch(EXTRACTOR) as extractor:
            extractor.return_value.find_top_similar_historical_cases.return_value = pd.DataFrame()
            cases = matcher.find_similar_cases(query_embedding=sample_embedding, room_id="611",
                                              in_date=date(2024, 1, 1), growth_day=10)
        assert cases == []

    def test_find_similar_cases_extractor_error_returns_empty(self, matcher, sample_embedding):
        """取数层抛异常 ⇒ 空列表 + 记日志（调用方据此走"没有历史参照"的保守分支）。"""
        with patch(EXTRACTOR) as extractor:
            extractor.return_value.find_top_similar_historical_cases.side_effect = (
                RuntimeError("database connection failed"))
            cases = matcher.find_similar_cases(query_embedding=sample_embedding, room_id="611",
                                              in_date=date(2024, 1, 1), growth_day=10)
        assert cases == []

    def test_find_similar_cases_missing_env_columns_default_to_zero(self, matcher, sample_embedding):
        """缺环境列（老数据/取数层没带）时给 0.0 而不是崩——但**不编造**看起来像真值的数。"""
        rows = self._rows("611", 10, "2024-01-01T12:00:00", [0.5], with_env=False)
        with patch(EXTRACTOR) as extractor:
            extractor.return_value.find_top_similar_historical_cases.return_value = rows
            cases = matcher.find_similar_cases(query_embedding=sample_embedding, room_id="611",
                                              in_date=date(2024, 1, 1), growth_day=10)
        assert len(cases) == 1
        assert (cases[0].temperature, cases[0].humidity, cases[0].co2) == (0.0, 0.0, 0.0)
        assert cases[0].similarity_score == pytest.approx(50.0)

    def test_find_similar_cases_without_combined_column_is_low(self, matcher, sample_embedding):
        """没有 `combined_similarity` 列时按 0.0 处理（低置信），而不是崩。

        这条覆盖的是 `row.get("combined_similarity", 0.0)` 这个默认值分支。
        （逐行的 try/except 是**防御性**的：`SimilarCase` 是普通 dataclass，字段给 NaN 也不报错，
        所以"跳过坏行"那条路径在正常输入下走不到——写在这里免得后来人以为它测过了。）
        """
        rows = self._rows("611", 10, "2024-01-01T12:00:00", [0.9]).drop(
            columns=["combined_similarity"])
        with patch(EXTRACTOR) as extractor:
            extractor.return_value.find_top_similar_historical_cases.return_value = rows
            cases = matcher.find_similar_cases(query_embedding=sample_embedding, room_id="611",
                                              in_date=date(2024, 1, 1), growth_day=10)
        assert len(cases) == 1
        assert cases[0].similarity_score == 0.0 and cases[0].confidence_level == "low"

    # ---------- 多图加权：纯函数（**注意：当前实现并没有调用它，见文件头说明**） ----------

    def test_multi_image_boost_current_behaviour(self, matcher):
        """钉住现有公式的实际行为（不是"应该"的行为）。

        两处值得注意，都写在这里以免被当成"测试写错了"：

        * **单图也有 10% 加成**：`consistency_boost = 1 + images_in_window/total_images * 0.1`，
          单图时是 1.1 —— 即"基线"不是 1.0；
        * 质量低于 50 时 `quality_boost < 1`，分数会被**下调**。

        另外：这个方法是**死代码**——真正跑的 `find_similar_cases_multi_image` 用的是另一份
        只按图数加权的内联公式。两者不一致这件事记在本次改动的说明里，等确认哪份是想要的
        再合并成一处。
        """
        base = matcher._apply_multi_image_boost(
            50.0, {"total_images": 1, "images_in_window": 1, "avg_quality": 50.0})
        more = matcher._apply_multi_image_boost(
            50.0, {"total_images": 5, "images_in_window": 5, "avg_quality": 50.0})
        better = matcher._apply_multi_image_boost(
            50.0, {"total_images": 5, "images_in_window": 5, "avg_quality": 80.0})
        low_quality = matcher._apply_multi_image_boost(
            50.0, {"total_images": 5, "images_in_window": 5, "avg_quality": 20.0})
        capped = matcher._apply_multi_image_boost(
            95.0, {"total_images": 10, "images_in_window": 10, "avg_quality": 100.0})

        assert base == pytest.approx(55.0)          # 单图也有 1.1 的"一致性"加成
        assert more > base                          # 图多则更高
        assert better > more                        # 质量高则更高
        assert low_quality < more                   # 质量低会被下调
        # 注意：三个因子是**相乘**的，所以总加成会超过"图数封顶 20%"——单看这一项封顶，
        # 总倍数实测可达 1.32（1.2 × 1.0 × 1.1）。这里只钉"不超过 100 分"这条硬边界。
        assert capped <= 100.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
