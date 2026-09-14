from datetime import datetime
from typing import Any, Callable

from utils.create_table import ImageTextQuality, MushroomImageEmbedding


def insert_text_quality_record(
    session: Any,
    image_path: str,
    embedding_id: Any,
    room_id: str | None,
    in_date: Any,
    collection_datetime: datetime | None,
    llama_description: str | None,
    chinese_description: str | None,
    image_quality_score: float | None,
) -> None:
    session.add(
        ImageTextQuality(
            mushroom_embedding_id=embedding_id,
            image_path=image_path,
            room_id=room_id,
            in_date=in_date,
            collection_datetime=collection_datetime,
            llama_description=llama_description,
            chinese_description=chinese_description,
            image_quality_score=image_quality_score,
        )
    )


def save_text_quality_only(
    session_factory: Callable[[], Any],
    image_path: str,
    room_id: str | None,
    in_date: Any,
    collection_datetime: datetime | None,
    growth_stage_description: str | None,
    chinese_description: str | None,
    llama_quality_score: float | None,
) -> bool:
    session = session_factory()
    try:
        existing_embedding = (
            session.query(MushroomImageEmbedding).filter_by(image_path=image_path).first()
        )
        embedding_id = existing_embedding.id if existing_embedding else None

        insert_text_quality_record(
            session,
            image_path,
            embedding_id,
            room_id,
            in_date,
            collection_datetime,
            growth_stage_description if growth_stage_description else None,
            chinese_description,
            llama_quality_score,
        )
        session.commit()
        return True
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
