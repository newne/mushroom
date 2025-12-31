import logging
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from sqlalchemy.orm import sessionmaker

from global_const.global_const import pgsql_engine, IMAGE_DIR
from utils.create_table import MushroomImageEmbedding

# ==========================
# 日志配置
# ==========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==========================
# 配置加载
# ==========================
# MODEL_NAME = os.getenv('MODEL_NAME', 'openai/clip-vit-base-patch32')
# # 自动检测设备，如果CUDA可用则使用CUDA，否则使用CPU
# DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
# logger.info(f"🔄 检测到设备: {DEVICE}")
# # 更新图片目录为项目中的 data 目录
# IMAGE_DIR = Path(__file__).parent.parent.parent / 'data'
#
# # 检查本地模型是否存在，如果存在则使用本地模型
# LOCAL_MODEL_PATH = Path(__file__).parent.parent.parent / 'models' / 'clip-vit-base-patch32'
#
# if LOCAL_MODEL_PATH.exists():
#     MODEL_NAME = str(LOCAL_MODEL_PATH)
#     logger.info(f"🔄 从本地路径加载模型: {MODEL_NAME}")
# else:
#     logger.info(f"🔄 本地模型不存在，将从HuggingFace加载: {MODEL_NAME}")


# ==========================
# 数据库连接管理
# ==========================
def get_db_connection():
    """获取数据库连接"""
    # 使用全局配置的pgsql_engine
    from src.global_const.global_const import pgsql_engine
    logger.info("✅ 数据库连接成功")
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
            logger.info("✅ pgvector 扩展已启用")
    except Exception as e:
        logger.warning(f"pgvector 扩展启用警告: {e}")

    # 创建图像向量表
    with conn.connect() as db_conn:
        db_conn.execute(text("""
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
                    """))
        db_conn.commit()

    # 创建文本向量表
    with conn.connect() as db_conn:
        db_conn.execute(text("""
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
                    """))
        db_conn.commit()

    # 创建索引（IVFFlat，推荐列表数为行数的平方根）
    # 对于小数据集（<10万条），索引可能不会显著提升性能，但为未来扩容做准备
    try:
        with conn.connect() as db_conn:
            db_conn.execute(text("""
                        CREATE INDEX IF NOT EXISTS idx_image_embeddings_ivf
                            ON image_embeddings
                            USING ivfflat (embedding vector_cosine_ops)
                            WITH (lists = 100);
                        """))
            db_conn.commit()
            logger.info("✅ image_embeddings 索引已创建")
    except Exception as e:
        logger.warning(f"创建索引失败（可能数据量不足）: {e}")

    logger.info("✅ 数据库表初始化完成")




# ==========================
# 向量化函数
# ==========================
def get_image_embedding(image_path: Path) -> np.ndarray:
        # ==========================
    # CLIP 模型加载
    # `openai/clip-vit-base-patch32` 输出 512 维向量
    # ==========================
    logger.info(f"🔄 正在加载 CLIP 模型: {MODEL_NAME}...")
    from transformers import CLIPProcessor, CLIPModel
    processor = CLIPProcessor.from_pretrained(MODEL_NAME)
    model = CLIPModel.from_pretrained(MODEL_NAME).to(DEVICE)
    model.eval()
    logger.info(f"✅ CLIP 模型加载完成，设备: {DEVICE}")
    """获取图像的向量表示"""
    try:
        image = Image.open(image_path).convert("RGB")
        inputs = processor(images=image, return_tensors="pt", padding=True).to(DEVICE)

        with torch.no_grad():
            image_features = model.get_image_features(**inputs)

        # 归一化（对于余弦相似度很重要）
        embedding = image_features.cpu().numpy()[0]
        embedding = embedding / np.linalg.norm(embedding)
        return embedding.tolist()
    except Exception as e:
        logger.error(f"❌ 处理图像失败 {image_path}: {e}")
        return None


def get_text_embedding(text: str) -> np.ndarray:
    """获取文本的向量表示"""
    try:
        inputs = processor(text=text, return_tensors="pt", padding=True, truncation=True).to(DEVICE)

        with torch.no_grad():
            text_features = model.get_text_features(**inputs)

        # 归一化
        embedding = text_features.cpu().numpy()[0]
        embedding = embedding / np.linalg.norm(embedding)
        return embedding.tolist()
    except Exception as e:
        logger.error(f"❌ 处理文本失败 '{text}': {e}")
        return None


# ==========================
# 批量处理函数
# ==========================
def process_images(conn):
    """处理图像目录中的所有图像并存储到数据库"""
    if not IMAGE_DIR.exists():
        logger.warning(f"⚠️ 图像目录不存在: {IMAGE_DIR}")
        return

    # 获取所有支持的图像文件
    image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff'}
    all_files = list(IMAGE_DIR.rglob("*.*"))
    image_files = [f for f in all_files if f.suffix.lower() in image_extensions and not f.name.startswith('.')]
    logger.info(f"📁 发现 {len(image_files)} 个图像文件")

    from sqlalchemy import text
    
    for img_path in image_files:
        # 跳过隐藏文件和非图像文件（已提前过滤，但保留检查以防万一）
        if img_path.name.startswith('.'):
            continue

        try:
            embedding = get_image_embedding(img_path)
            if embedding is None:
                continue

            # 使用 ON CONFLICT 处理重复路径
            with conn.connect() as db_conn:
                db_conn.execute(text("""
                            INSERT INTO image_embeddings (image_path, file_name, embedding)
                            VALUES (:image_path, :file_name, :embedding) ON CONFLICT (image_path) 
                    DO
                            UPDATE SET
                                embedding = EXCLUDED.embedding,
                                created_at = CURRENT_TIMESTAMP;
                            """), {
                    "image_path": str(img_path),
                    "file_name": img_path.name,
                    "embedding": embedding
                })
                db_conn.commit()
            logger.info(f"✅ 已处理: {img_path.name}")
        except Exception as e:
            logger.error(f"❌ 存储图像向量失败 {img_path.name}: {e}")

    logger.info("✅ 所有图像处理完成")


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
                db_conn.execute(text("""
                            INSERT INTO text_embeddings (content, embedding, metadata)
                            VALUES (:content, :embedding, :metadata) ON CONFLICT (content) 
                    DO
                            UPDATE SET
                                embedding = EXCLUDED.embedding,
                                metadata = EXCLUDED.metadata,
                                created_at = CURRENT_TIMESTAMP;
                            """), {
                    "content": text_content,
                    "embedding": embedding,
                    "metadata": {"source": "demo", "lang": "zh"}
                })
                db_conn.commit()
            logger.info(f"✅ 已处理文本: {text_content}")
        except Exception as e:
            logger.error(f"❌ 存储文本向量失败 '{text_content}': {e}")

    logger.info("✅ 所有文本处理完成")


def process_mushroom_images():
    """处理蘑菇图片并存储到数据库"""
    # 创建数据库会话
    Session = sessionmaker(bind=pgsql_engine)
    session = Session()

    try:
        # 优化后的文本描述
        description = "611库，生长第27天，新风关15分开5分，照明关，加显关，循环关，今天采收蘑菇。"

        # 获取data目录下的所有图片文件
        if not IMAGE_DIR.exists():
            logger.warning(f"⚠️ 图像目录不存在: {IMAGE_DIR}")
            return

        # 获取所有支持的图像文件
        image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff'}
        all_files = list(IMAGE_DIR.rglob("*.*"))
        image_files = [f for f in all_files if f.suffix.lower() in image_extensions and not f.name.startswith('.')]
        logger.info(f"📁 发现 {len(image_files)} 个图片文件")

        for img_path in image_files:
            # 跳过隐藏文件
            if img_path.name.startswith('.'):
                continue

            try:
                # 获取图片向量
                embedding = get_image_embedding(img_path)
                if embedding is None:
                    continue

                # 检查图片是否已存在于数据库中
                existing = session.query(MushroomImageEmbedding).filter_by(image_path=str(img_path)).first()

                if existing:
                    # 更新现有记录
                    existing.embedding = embedding
                    existing.description = description
                    existing.growth_day = 27
                    logger.info(f"✅ 已更新图片记录: {img_path.name}")
                else:
                    # 创建新记录
                    new_record = MushroomImageEmbedding(
                        image_path=str(img_path),
                        file_name=img_path.name,
                        description=description,
                        embedding=embedding,
                        growth_day=27
                    )
                    session.add(new_record)
                    logger.info(f"✅ 已添加图片记录: {img_path.name}")

            except Exception as e:
                logger.error(f"❌ 处理图片失败 {img_path.name}: {e}")
                session.rollback()

        # 提交所有更改
        session.commit()
        logger.info("✅ 所有蘑菇图片处理完成")

    except Exception as e:
        logger.error(f"❌ 处理蘑菇图片时发生错误: {e}")
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

    logger.info("🎉 处理完成，程序退出")


if __name__ == "__main__":
    main()
