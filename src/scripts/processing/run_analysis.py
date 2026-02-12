
import os
import sys
import argparse
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any
from loguru import logger
from PIL import Image

# Add project root to path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.append(project_root)
sys.path.append(os.path.join(project_root, "src"))

from src.vision.offline.processor import OfflineProcessor
from src.utils.experiment_tracker import ExperimentTracker

class DatasetAnalyzer:
    """
    针对本地数据集的离线分析器，集成 ExperimentTracker
    """
    def __init__(self, dataset_dir: str):
        self.dataset_dir = Path(dataset_dir)
        self.manifest_path = self.dataset_dir / "manifest.csv"
        self.processor = OfflineProcessor()
        
        # 初始化实验追踪器
        # 强制使用本地 MLflow，避免 OfflineProcessor 内部 MLflowLogger 的干扰
        local_uri = "file://" + str(Path("./mlruns").absolute())
        self.tracker = ExperimentTracker("mushroom/dataset_v6_analysis", tracking_uri=local_uri)
        
    def run(self):
        if not self.manifest_path.exists():
            logger.error(f"Manifest not found: {self.manifest_path}")
            return
            
        df = pd.read_csv(self.manifest_path)
        logger.info(f"Loaded manifest with {len(df)} records")
        
        # 获取当前 Prompt
        prompt = getattr(self.processor.llama_config, "mushroom_descripe_prompt", "N/A")
        
        # 开启一次 Parent Run 代表本次全量分析
        with self.tracker.start_run(
            description="Batch analysis of dataset_v6 with updated prompt",
            tags={"dataset": "v6", "model": "qwen3-vl-2b"}
        ) as parent_run:
            
            # 1. 记录环境和参数
            self.tracker.log_environment()
            self.tracker.log_params({
                "dataset_dir": str(self.dataset_dir),
                "total_images": len(df),
                "prompt_template": prompt,
                "model_config": self.processor.llama_config.get("model", "unknown")
            })
            
            # 2. 记录 Prompt 到 Artifacts
            with open("prompt_snapshot.txt", "w") as f:
                f.write(prompt)
            self.tracker.log_artifact("prompt_snapshot.txt")
            os.remove("prompt_snapshot.txt")
            
            success_count = 0
            failed_count = 0
            quality_scores = []
            results = []
            
            for idx, row in df.iterrows():
                try:
                    filename = row['image_path']
                    local_path = self.dataset_dir / filename
                    
                    if not local_path.exists():
                        logger.warning(f"Image not found locally: {local_path}")
                        failed_count += 1
                        continue
                        
                    image = Image.open(local_path).convert("RGB")
                    
                    # 预处理
                    processed_image = self.processor._preprocess_image(image)
                    
                    # 调用模型
                    logger.info(f"Analyzing {filename}...")
                    description_result = self.processor._generate_description(processed_image)
                    
                    # 收集结果
                    quality_score = description_result.get("image_quality_score")
                    if isinstance(quality_score, (int, float)):
                        quality_scores.append(quality_score)
                        
                    result_entry = {
                        "filename": filename,
                        "room_id": row['room_id'],
                        "collection_timestamp": row['collection_timestamp'],
                        "quality_score": quality_score,
                        "chinese_desc": description_result.get("chinese_description", "") or "N/A",
                        "english_desc": description_result.get("growth_stage_description", "") or "N/A"
                    }
                    results.append(result_entry)
                    
                    # 使用 Nested Run 记录每张图片的详细信息（可选，避免 Parent Run 过于臃肿）
                    # 为了方便查看每张图的效果，这里选择记录
                    with self.tracker.start_run(
                        run_name=f"img_{filename}", 
                        nested=True,
                        tags={"parent_run_id": parent_run.info.run_id}
                    ):
                        self.tracker.log_metrics({"quality_score": quality_score} if quality_score is not None else {})
                        self.tracker.log_artifact(str(local_path), "image")
                        
                        # 记录描述文本
                        with open("description.txt", "w") as f:
                            f.write(f"Chinese: {result_entry['chinese_desc']}\n")
                            f.write(f"English: {result_entry['english_desc']}\n")
                        self.tracker.log_artifact("description.txt")
                        os.remove("description.txt")
                    
                    success_count += 1
                    
                except Exception as e:
                    logger.error(f"Failed to analyze {row.get('image_path', 'unknown')}: {e}")
                    failed_count += 1
            
            # 3. 记录整体指标
            avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0
            self.tracker.log_metrics({
                "success_count": success_count,
                "failed_count": failed_count,
                "avg_quality_score": avg_quality,
                "min_quality_score": min(quality_scores) if quality_scores else 0,
                "max_quality_score": max(quality_scores) if quality_scores else 0
            })
            
            # 4. 生成并记录汇总报告
            if results:
                df_results = pd.DataFrame(results)
                df_results.to_csv("analysis_results.csv", index=False)
                self.tracker.log_artifact("analysis_results.csv")
                os.remove("analysis_results.csv")
                
            logger.success(f"Analysis completed. Success: {success_count}, Failed: {failed_count}, Avg Quality: {avg_quality:.2f}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset_dir", type=str, required=True, help="Path to dataset directory")
    args = parser.parse_args()
    
    # 设置日志
    logger.add(os.path.join(args.dataset_dir, "analysis.log"))
    
    analyzer = DatasetAnalyzer(args.dataset_dir)
    analyzer.run()

if __name__ == "__main__":
    main()
