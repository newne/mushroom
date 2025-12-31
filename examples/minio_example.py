"""
MinIO使用示例
演示如何使用MinIO客户端进行图片操作
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from src.utils.minio_client import create_minio_client
from loguru import logger


def main():
    """主函数"""
    logger.info("开始MinIO示例演示")
    
    try:
        # 创建MinIO客户端
        client = create_minio_client()
        logger.info(f"当前环境: {client.environment}")
        logger.info(f"MinIO端点: {client.config['endpoint']}")
        
        # 测试连接
        if not client.test_connection():
            logger.error("MinIO连接失败，请检查配置")
            return
        
        # 确保存储桶存在
        bucket_name = client.config['bucket']
        if client.ensure_bucket_exists(bucket_name):
            logger.info(f"存储桶 {bucket_name} 准备就绪")
        
        # 列出所有图片文件
        logger.info("正在列出存储桶中的图片文件...")
        image_files = client.list_images()
        
        if not image_files:
            logger.warning("存储桶中没有找到图片文件")
            
            # 如果本地有测试图片，可以上传一张
            test_image_path = project_root / "data" / "m1.jpg"
            if test_image_path.exists():
                logger.info(f"发现本地测试图片: {test_image_path}")
                if client.upload_image(str(test_image_path), "test/m1.jpg"):
                    logger.info("测试图片上传成功")
                    image_files = client.list_images()
                else:
                    logger.error("测试图片上传失败")
        
        # 处理找到的图片
        for image_file in image_files[:5]:  # 只处理前5张图片
            logger.info(f"处理图片: {image_file}")
            
            # 获取图片信息
            info = client.get_image_info(image_file)
            if info:
                logger.info(f"  - 大小: {info['size']} bytes")
                logger.info(f"  - 最后修改: {info['last_modified']}")
                logger.info(f"  - 内容类型: {info['content_type']}")
            
            # 获取图片对象
            image = client.get_image(image_file)
            if image:
                logger.info(f"  - 图片尺寸: {image.size}")
                logger.info(f"  - 图片模式: {image.mode}")
                
                # 可以在这里进行图片处理
                # 例如：调整大小、格式转换等
                
            # 获取图片字节数据
            image_bytes = client.get_image_bytes(image_file)
            if image_bytes:
                logger.info(f"  - 字节数据大小: {len(image_bytes)} bytes")
        
        logger.info("MinIO示例演示完成")
        
    except Exception as e:
        logger.error(f"示例执行失败: {e}")
        raise


def test_environment_switching():
    """测试环境切换"""
    logger.info("测试环境切换功能")
    
    # 保存原始环境变量
    original_env = os.environ.get("prod", "false")
    
    try:
        # 测试开发环境
        os.environ["prod"] = "false"
        dev_client = create_minio_client()
        logger.info(f"开发环境端点: {dev_client.config['endpoint']}")
        
        # 测试生产环境
        os.environ["prod"] = "true"
        prod_client = create_minio_client()
        logger.info(f"生产环境端点: {prod_client.config['endpoint']}")
        
        # 验证配置不同
        assert dev_client.config['endpoint'] != prod_client.config['endpoint']
        logger.info("环境切换测试通过")
        
    finally:
        # 恢复原始环境变量
        os.environ["prod"] = original_env


if __name__ == "__main__":
    # 设置日志级别
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    
    # 运行示例
    main()
    
    # 测试环境切换
    # test_environment_switching()