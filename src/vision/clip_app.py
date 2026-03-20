import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
from PIL import Image
from sqlalchemy.orm import sessionmaker

from global_const.global_const import IMAGE_DIR, pgsql_engine
from utils import log_task_event
from utils.create_table import MushroomImageEmbedding


def _clip_app_log(
    event: str, message: str, level: str = "INFO", **context: Any
) -> None:
    """输出统一的 CLIP 应用事件日志。"""
    log_task_event(
        "VISION_CLIP_APP",
        event,
        message,
        level=level,
        task_type="helper",
        **context,
    )


# ==========================
# 配置加载
# ==========================
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
LOCAL_MODEL_PATH = (
    Path(__file__).parent.parent.parent / "models" / "clip-vit-base-patch32"
)
MODEL_NAME = (
    str(LOCAL_MODEL_PATH)
    if LOCAL_MODEL_PATH.exists()
    else "openai/clip-vit-base-patch32"
)

_clip_processor = None
_clip_model = None


def _get_clip_runtime():
    """延迟加载并缓存 CLIP 模型与处理器。"""
    global _clip_processor, _clip_model
    if _clip_processor is not None and _clip_model is not None:
        return _clip_processor, _clip_model

    _clip_app_log(
        "VISION_CLIP_APP_MODEL_LOADING",
        "开始加载 CLIP 模型",
        model_name=MODEL_NAME,
        device=DEVICE,
        status="running",
    )
    from transformers import CLIPModel, CLIPProcessor

    _clip_processor = CLIPProcessor.from_pretrained(MODEL_NAME)
    _clip_model = CLIPModel.from_pretrained(MODEL_NAME).to(DEVICE)
    _clip_model.eval()

    _clip_app_log(
        "VISION_CLIP_APP_MODEL_READY",
        "CLIP 模型加载完成",
        model_name=MODEL_NAME,
        device=DEVICE,
        status="success",
    )
    return _clip_processor, _clip_model


# ==========================
# 数据库连接管理
# ==========================
def get_db_connection():
    """获取数据库连接"""
    # 使用全局配置的pgsql_engine
    from global_const.global_const import pgsql_engine

    _clip_app_log(
        "VISION_CLIP_APP_DB_READY",
        "数据库连接就绪",
        status="success",
    )
    return pgsql_engine


# ==========================
# 初始化数据库
# ==========================
def init_database(conn):
    """初始化数据库表和 pgvector 扩展"""
    from sqlalchemy import text

    # 启用 pgvector 扩展
    try:
        with conn.connect() as db_conn:
            db_conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            db_conn.commit()
            _clip_app_log(
                "VISION_CLIP_APP_PGVECTOR_READY",
                "pgvector 扩展已启用",
                status="success",
            )
    except Exception as e:
        _clip_app_log(
            "VISION_CLIP_APP_PGVECTOR_WARNING",
            "pgvector 扩展初始化告警",
            level="WARNING",
            status="failed",
            error_type=type(e).__name__,
            error_message=str(e),
        )

    # 创建图像向量表
    with conn.connect() as db_conn:
        db_conn.execute(
            text("""
                    CREATE TABLE IF NOT EXISTS image_embeddings
                    (
                        id
                        SERIAL
                        PRIMARY
                        KEY,
                        image_path
                        TEXT
                        NOT
                        NULL
                        UNIQUE,
                        file_name
                        TEXT
                        NOT
                        NULL,
                        embedding
                        vector
                    (
                        512
                    ) NOT NULL, -- CLIP ViT-B/32 维度为 512
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );
                    """)
        )
        db_conn.commit()

    # 创建文本向量表
    with conn.connect() as db_conn:
        db_conn.execute(
            text("""
                    CREATE TABLE IF NOT EXISTS text_embeddings
                    (
                        id
                        SERIAL
                        PRIMARY
                        KEY,
                        content
                        TEXT
                        NOT
                        NULL,
                        metadata
                        JSONB,
                        embedding
                        vector
                    (
                        512
                    ) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );
                    """)
        )
        db_conn.commit()

    # 创建索引（IVFFlat，推荐列表数为行数的平方根）
    # 对于小数据集（<10万条），索引可能不会显著提升性能，但为未来扩容做准备
    try:
        with conn.connect() as db_conn:
            db_conn.execute(
                text("""
                        CREATE INDEX IF NOT EXISTS idx_image_embeddings_ivf
                            ON image_embeddings
                            USING ivfflat (embedding vector_cosine_ops)
                            WITH (lists = 100);
                        """)
            )
            db_conn.commit()
            _clip_app_log(
                "VISION_CLIP_APP_INDEX_READY",
                "image_embeddings 索引已创建",
                status="success",
            )
    except Exception as e:
        _clip_app_log(
            "VISION_CLIP_APP_INDEX_WARNING",
            "创建 image_embeddings 索引告警",
            level="WARNING",
            status="failed",
            error_type=type(e).__name__,
            error_message=str(e),
        )

    _clip_app_log(
        "VISION_CLIP_APP_DB_INIT_FINISH",
        "数据库初始化完成",
        status="success",
    )


# ==========================
# 向量化函数
# ==========================
def get_image_embedding(image_path: Path) -> np.ndarray:
    """获取图像的向量表示"""
    try:
        processor, model = _get_clip_runtime()
        image = Image.open(image_path).convert("RGB")
        inputs = processor(images=image, return_tensors="pt", padding=True).to(DEVICE)

        with torch.no_grad():
            image_features = model.get_image_features(**inputs)

        # 归一化（对于余弦相似度很重要）
        embedding = image_features.cpu().numpy()[0]
        embedding = embedding / np.linalg.norm(embedding)
        return embedding.tolist()
    except Exception as e:
        _clip_app_log(
            "VISION_CLIP_APP_IMAGE_EMBEDDING_FAILED",
            "图像向量化失败",
            level="ERROR",
            image_path=str(image_path),
            status="failed",
            error_type=type(e).__name__,
            error_message=str(e),
        )
        return None


def get_text_embedding(text: str) -> np.ndarray:
    """获取文本的向量表示"""
    try:
        processor, model = _get_clip_runtime()
        inputs = processor(
            text=text, return_tensors="pt", padding=True, truncation=True
        ).to(DEVICE)

        with torch.no_grad():
            text_features = model.get_text_features(**inputs)

        # 归一化
        embedding = text_features.cpu().numpy()[0]
        embedding = embedding / np.linalg.norm(embedding)
        return embedding.tolist()
    except Exception as e:
        _clip_app_log(
            "VISION_CLIP_APP_TEXT_EMBEDDING_FAILED",
            "文本向量化失败",
            level="ERROR",
            text_preview=text[:80],
            status="failed",
            error_type=type(e).__name__,
            error_message=str(e),
        )
        return None


# ==========================
# 批量处理函数
# ==========================
def process_images(conn):
    """处理图像目录中的所有图像并存储到数据库"""
    if not IMAGE_DIR.exists():
        _clip_app_log(
            "VISION_CLIP_APP_IMAGE_DIR_MISSING",
            "图像目录不存在",
            level="WARNING",
            image_dir=str(IMAGE_DIR),
            status="skipped",
        )
        return

    # 获取所有支持的图像文件
    image_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff"}
    all_files = list(IMAGE_DIR.rglob("*.*"))
    image_files = [
        f
        for f in all_files
        if f.suffix.lower() in image_extensions and not f.name.startswith(".")
    ]
    _clip_app_log(
        "VISION_CLIP_APP_IMAGES_DISCOVERED",
        "已扫描图像目录",
        total_items=len(image_files),
        image_dir=str(IMAGE_DIR),
        status="success",
    )

    from sqlalchemy import text

    for img_path in image_files:
        # 跳过隐藏文件和非图像文件（已提前过滤，但保留检查以防万一）
        if img_path.name.startswith("."):
            continue

        try:
            embedding = get_image_embedding(img_path)
            if embedding is None:
                continue

            # 使用 ON CONFLICT 处理重复路径
            with conn.connect() as db_conn:
                db_conn.execute(
                    text("""
                            INSERT INTO image_embeddings (image_path, file_name, embedding)
                            VALUES (:image_path, :file_name, :embedding) ON CONFLICT (image_path) 
                    DO
                            UPDATE SET
                                embedding = EXCLUDED.embedding,
                                created_at = CURRENT_TIMESTAMP;
                            """),
                    {
                        "image_path": str(img_path),
                        "file_name": img_path.name,
                        "embedding": embedding,
                    },
                )
                db_conn.commit()
            _clip_app_log(
                "VISION_CLIP_APP_IMAGE_STORED",
                "图像向量已写入数据库",
                image_name=img_path.name,
                image_path=str(img_path),
                status="success",
            )
        except Exception as e:
            _clip_app_log(
                "VISION_CLIP_APP_IMAGE_STORE_FAILED",
                "存储图像向量失败",
                level="ERROR",
                image_name=img_path.name,
                image_path=str(img_path),
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )

    _clip_app_log(
        "VISION_CLIP_APP_IMAGES_FINISH",
        "图像批量处理完成",
        total_items=len(image_files),
        status="success",
    )


def process_texts(conn):
    """处理示例文本并存储到数据库"""
    sample_texts = [
        "一只红色的牛肝菌",
        "毒蘑菇含有毒素",
        "森林里的野生蘑菇",
        "美味的松茸汤",
        "白色的伞菌",
    ]

    from sqlalchemy import text

    for text_content in sample_texts:
        try:
            embedding = get_text_embedding(text_content)
            if embedding is None:
                continue

            with conn.connect() as db_conn:
                db_conn.execute(
                    text("""
                            INSERT INTO text_embeddings (content, embedding, metadata)
                            VALUES (:content, :embedding, :metadata) ON CONFLICT (content) 
                    DO
                            UPDATE SET
                                embedding = EXCLUDED.embedding,
                                metadata = EXCLUDED.metadata,
                                created_at = CURRENT_TIMESTAMP;
                            """),
                    {
                        "content": text_content,
                        "embedding": embedding,
                        "metadata": {"source": "demo", "lang": "zh"},
                    },
                )
                db_conn.commit()
            _clip_app_log(
                "VISION_CLIP_APP_TEXT_STORED",
                "文本向量已写入数据库",
                text_preview=text_content[:80],
                status="success",
            )
        except Exception as e:
            _clip_app_log(
                "VISION_CLIP_APP_TEXT_STORE_FAILED",
                "存储文本向量失败",
                level="ERROR",
                text_preview=text_content[:80],
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )

    _clip_app_log(
        "VISION_CLIP_APP_TEXTS_FINISH",
        "文本批量处理完成",
        total_items=len(sample_texts),
        status="success",
    )


def process_mushroom_images():
    """处理蘑菇图片并存储到数据库"""
    # 创建数据库会话
    Session = sessionmaker(bind=pgsql_engine)
    session = Session()

    try:
        # 优化后的文本描述
        description = (
            "611库，生长第27天，新风关15分开5分，照明关，加显关，循环关，今天采收蘑菇。"
        )

        # 获取data目录下的所有图片文件
        if not IMAGE_DIR.exists():
            _clip_app_log(
                "VISION_CLIP_APP_MUSHROOM_DIR_MISSING",
                "蘑菇图像目录不存在",
                level="WARNING",
                image_dir=str(IMAGE_DIR),
                status="skipped",
            )
            return

        # 获取所有支持的图像文件
        image_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff"}
        all_files = list(IMAGE_DIR.rglob("*.*"))
        image_files = [
            f
            for f in all_files
            if f.suffix.lower() in image_extensions and not f.name.startswith(".")
        ]
        _clip_app_log(
            "VISION_CLIP_APP_MUSHROOM_IMAGES_DISCOVERED",
            "已发现蘑菇图片文件",
            total_items=len(image_files),
            image_dir=str(IMAGE_DIR),
            status="success",
        )

        for img_path in image_files:
            # 跳过隐藏文件
            if img_path.name.startswith("."):
                continue

            try:
                # 获取图片向量
                embedding = get_image_embedding(img_path)
                if embedding is None:
                    continue

                # 检查图片是否已存在于数据库中
                existing = (
                    session.query(MushroomImageEmbedding)
                    .filter_by(image_path=str(img_path))
                    .first()
                )

                if existing:
                    # 更新现有记录
                    existing.embedding = embedding
                    existing.description = description
                    existing.growth_day = 27
                    _clip_app_log(
                        "VISION_CLIP_APP_MUSHROOM_IMAGE_UPDATED",
                        "已更新蘑菇图片记录",
                        image_name=img_path.name,
                        image_path=str(img_path),
                        status="success",
                    )
                else:
                    # 创建新记录
                    new_record = MushroomImageEmbedding(
                        image_path=str(img_path),
                        file_name=img_path.name,
                        description=description,
                        embedding=embedding,
                        growth_day=27,
                    )
                    session.add(new_record)
                    _clip_app_log(
                        "VISION_CLIP_APP_MUSHROOM_IMAGE_CREATED",
                        "已新增蘑菇图片记录",
                        image_name=img_path.name,
                        image_path=str(img_path),
                        status="success",
                    )

            except Exception as e:
                _clip_app_log(
                    "VISION_CLIP_APP_MUSHROOM_IMAGE_FAILED",
                    "处理蘑菇图片失败",
                    level="ERROR",
                    image_name=img_path.name,
                    image_path=str(img_path),
                    status="failed",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                session.rollback()

        # 提交所有更改
        session.commit()
        _clip_app_log(
            "VISION_CLIP_APP_MUSHROOM_IMAGES_FINISH",
            "蘑菇图片处理完成",
            total_items=len(image_files),
            status="success",
        )

    except Exception as e:
        _clip_app_log(
            "VISION_CLIP_APP_MUSHROOM_FLOW_FAILED",
            "蘑菇图片处理流程异常",
            level="ERROR",
            status="failed",
            error_type=type(e).__name__,
            error_message=str(e),
        )
        session.rollback()
    finally:
        session.close()


# ==========================
# 主程序
# ==========================
def main():
    # 等待数据库就绪
    time.sleep(10)

    # 获取数据库连接
    conn = get_db_connection()

    # 初始化数据库
    init_database(conn)

    # 处理图像
    process_images(conn)

    # 备份文本
    # process_texts(conn)

    # 处理蘑菇图片
    process_mushroom_images()

    _clip_app_log(
        "VISION_CLIP_APP_MAIN_FINISH",
        "CLIP 应用流程完成",
        status="success",
    )


if __name__ == "__main__":
    main()
