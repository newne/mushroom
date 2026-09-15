from storage.models.base import Base
from utils.create_table import DecisionAnalysisDynamicResult, MushroomImageEmbedding


def test_create_table_models_use_storage_base_metadata():
    assert MushroomImageEmbedding.__table__.metadata is Base.metadata
    assert DecisionAnalysisDynamicResult.__table__.metadata is Base.metadata


def test_storage_base_metadata_contains_create_table_models():
    assert MushroomImageEmbedding.__tablename__ in Base.metadata.tables
    assert DecisionAnalysisDynamicResult.__tablename__ in Base.metadata.tables