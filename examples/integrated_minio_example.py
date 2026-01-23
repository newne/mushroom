"""
集成MinIO示例
展示如何在实际项目中使用MinIO服务进行图片处理和分析
"""

import sys
import os
from pathlib import Path
import numpy as np
from PIL import Image, ImageEnhance
import json

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from utils.minio_service import create_minio_service
from loguru import logger


def image_analysis_callback(image: Image.Image, image_info: dict) -> dict:
    """
    图片分析回调函数
    
    Args:
        image: PIL图片对象
        image_info: 图片信息
        
    Returns:
        分析结果
    """
    try:
        # 基本信息
        width, height = image.size
        mode = image.mode
        
        # 转换为RGB模式进行分析
        if mode != 'RGB':
            rgb_image = image.convert('RGB')
        else:
            rgb_image = image
        
        # 转换为numpy数组
        img_array = np.array(rgb_image)
        
        # 计算统计信息
        mean_rgb = np.mean(img_array, axis=(0, 1))
        std_rgb = np.std(img_array, axis=(0, 1))
        
        # 亮度分析
        gray_image = rgb_image.convert('L')
        gray_array = np.array(gray_image)
        brightness = np.mean(gray_array)
        
        # 对比度分析
        contrast = np.std(gray_array)
        
        analysis_result = {
            'dimensions': {'width': width, 'height': height},
            'mode': mode,
            'pixel_count': width * height,
            'aspect_ratio': round(width / height, 2),
            'color_analysis': {
                'mean_rgb': mean_rgb.tolist(),
                'std_rgb': std_rgb.tolist(),
                'brightness': float(brightness),
                'contrast': float(contrast)
            },
            'file_info': {
                'size_bytes': image_info['size'],
                'object_name': image_info['object_name']
            }
        }
        
        return analysis_result
        
    except Exception as e:
        logger.error(f"图片分析失败: {e}")
        return {'error': str(e)}


def image_enhancement_callback(image: Image.Image, image_info: dict) -> dict:
    """
    图片增强回调函数
    
    Args:
        image: PIL图片对象
        image_info: 图片信息
        
    Returns:
        增强结果
    """
    try:
        # 创建增强器
        enhancer_brightness = ImageEnhance.Brightness(image)
        enhancer_contrast = ImageEnhance.Contrast(image)
        enhancer_sharpness = ImageEnhance.Sharpness(image)
        
        # 应用增强
        enhanced_image = enhancer_brightness.enhance(1.1)  # 增加10%亮度
        enhanced_image = enhancer_contrast.enhance(1.2)    # 增加20%对比度
        enhanced_image = enhancer_sharpness.enhance(1.1)   # 增加10%锐度
        
        # 这里可以保存增强后的图片到MinIO
        # 或者返回增强参数
        
        result = {
            'original_size': image.size,
            'enhanced': True,
            'enhancements': {
                'brightness': 1.1,
                'contrast': 1.2,
                'sharpness': 1.1
            },
            'object_name': image_info['object_name']
        }
        
        return result
        
    except Exception as e:
        logger.error(f"图片增强失败: {e}")
        return {'error': str(e)}


def main():
    """主函数"""
    logger.info("开始集成MinIO示例")
    
    try:
        # 创建MinIO服务
        service = create_minio_service()
        
        # 健康检查
        logger.info("执行健康检查...")
        health = service.health_check()
        if not health['healthy']:
            logger.error(f"健康检查失败: {health['errors']}")
            return
        
        logger.info(f"健康检查通过，发现 {health['image_count']} 张图片")
        
        # 获取图片统计信息
        logger.info("获取图片统计信息...")
        stats = service.get_image_statistics()
        logger.info(f"统计信息: {json.dumps(stats, indent=2, ensure_ascii=False)}")
        
        # 获取图片数据集
        logger.info("获取图片数据集...")
        dataset = service.get_image_dataset()
        if not dataset:
            logger.warning("没有找到图片数据集")
            return
        
        # 按类别分组图片（示例分类规则）
        logger.info("按类别分组图片...")
        category_mapping = {
            'test': 'test/',
            'data': 'data/',
            'samples': 'sample',
            'processed': 'processed/'
        }
        categorized = service.get_images_by_category(category_mapping)
        
        # 创建图片清单
        logger.info("创建图片清单...")
        manifest_created = service.create_image_manifest("image_manifest.json")
        if manifest_created:
            logger.info("图片清单创建成功")
        
        # 批量分析图片（只分析前5张）
        logger.info("开始批量图片分析...")
        analysis_results = service.process_images_with_callback(
            image_analysis_callback, 
            batch_size=3
        )
        
        # 保存分析结果
        if analysis_results:
            analysis_summary = {
                'total_processed': len(analysis_results),
                'successful': sum(1 for r in analysis_results if r['success']),
                'failed': sum(1 for r in analysis_results if not r['success']),
                'results': analysis_results[:5]  # 只保存前5个结果作为示例
            }
            
            with open("image_analysis_results.json", 'w', encoding='utf-8') as f:
                json.dump(analysis_summary, f, indent=2, ensure_ascii=False, default=str)
            
            logger.info(f"图片分析完成: {analysis_summary['successful']}/{analysis_summary['total_processed']} 成功")
        
        # 演示图片增强处理
        logger.info("开始图片增强处理...")
        enhancement_results = service.process_images_with_callback(
            image_enhancement_callback,
            batch_size=2
        )
        
        if enhancement_results:
            logger.info(f"图片增强完成: {len(enhancement_results)} 张图片处理")
        
        # 演示批量下载（下载前3张图片）
        if len(dataset) > 0:
            logger.info("演示批量下载...")
            download_list = [img['object_name'] for img in dataset[:3]]
            download_results = service.batch_download_images(download_list, "downloads")
            
            success_downloads = [r for r in download_results if r[1]]
            logger.info(f"下载完成: {len(success_downloads)} 张图片")
        
        logger.info("集成MinIO示例完成")
        
    except Exception as e:
        logger.error(f"示例执行失败: {e}")
        raise


def demonstrate_environment_config():
    """演示环境配置"""
    logger.info("演示环境配置功能")
    
    # 显示当前环境配置
    service = create_minio_service()
    
    config_info = {
        'environment': service.client.environment,
        'endpoint': service.client.config['endpoint'],
        'bucket': service.client.config['bucket'],
        'access_key': service.client.config['access_key'][:4] + '***',  # 隐藏敏感信息
        'region': service.client.config['region']
    }
    
    logger.info("当前环境配置:")
    for key, value in config_info.items():
        logger.info(f"  {key}: {value}")
    
    # 环境切换说明
    logger.info("\n环境切换说明:")
    logger.info("  开发环境: export prod=false (默认)")
    logger.info("  生产环境: export prod=true")
    logger.info("  配置文件: src/configs/settings.toml")


if __name__ == "__main__":
    # 设置日志级别
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    
    # 演示环境配置
    demonstrate_environment_config()
    
    # 运行主示例
    main()