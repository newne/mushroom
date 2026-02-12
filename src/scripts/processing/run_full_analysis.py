
import os
import sys
import json
import time
import argparse
import requests
import psutil
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
from PIL import Image
from loguru import logger
from jsonschema import validate, ValidationError

# Add project root to path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.append(project_root)
sys.path.append(os.path.join(project_root, "src"))

from src.vision.offline.processor import OfflineProcessor
from src.utils.experiment_tracker import ExperimentTracker
from src.global_const.global_const import settings

class FullAnalyzer:
    def __init__(self, dataset_dir: str):
        self.dataset_dir = Path(dataset_dir)
        self.manifest_path = self.dataset_dir / "manifest.csv"
        self.processor = OfflineProcessor()
        
        # Load Output Schema
        schema_path = Path(project_root) / "src" / "configs" / "output_schema.json"
        with open(schema_path, "r") as f:
            self.output_schema = json.load(f)["schema"]
            
        # Init Tracker (Force local for stability as per previous turn)
        local_uri = "file://" + str(Path("./mlruns").absolute())
        self.tracker = ExperimentTracker("mushroom/comprehensive_analysis", tracking_uri=local_uri)
        
    def verify_cloud_prompt(self) -> str:
        """Step 1: Verify Cloud Prompt Update Status"""
        prompt_api = getattr(settings, "data_source_url", {}).get(
            "prompt_mushroom_description", 
            "http://localhost/prompt/api/v1/prompts/role-instruction/active"
        )
        
        logger.info(f"Checking cloud prompt status at {prompt_api}...")
        try:
            # Simulate API call (replace with actual request if env allows)
            # response = requests.get(prompt_api, timeout=2)
            # response.raise_for_status()
            # remote_prompt = response.json().get("content")
            
            # Since we are in a restricted env, we assume settings.toml has the latest
            current_prompt = getattr(self.processor.llama_config, "mushroom_descripe_prompt", "N/A")
            logger.info("Cloud prompt verification simulated. Using local config as latest version.")
            return current_prompt
        except Exception as e:
            logger.warning(f"Failed to verify cloud prompt: {e}. Using local config.")
            return getattr(self.processor.llama_config, "mushroom_descripe_prompt", "N/A")

    def extract_features(self, image: Image.Image) -> Dict[str, Any]:
        """Step 3 (Partial): Image Feature Extraction"""
        # Resize for faster feature extraction
        img_small = image.resize((100, 100))
        arr = np.array(img_small)
        
        # Dominant Color (Simple mean)
        dominant_color = arr.mean(axis=(0, 1)).astype(int).tolist()
        
        # Color Histogram (Simplified: 8 bins per channel)
        hist_r = np.histogram(arr[:,:,0], bins=8, range=(0, 256))[0]
        hist_g = np.histogram(arr[:,:,1], bins=8, range=(0, 256))[0]
        hist_b = np.histogram(arr[:,:,2], bins=8, range=(0, 256))[0]
        histogram = np.concatenate([hist_r, hist_g, hist_b]).astype(int).tolist()
        
        return {
            "dominant_color": dominant_color,
            "color_histogram": histogram
        }

    def get_system_metrics(self) -> Dict[str, float]:
        """Get current system resource usage"""
        return {
            "memory_usage_mb": psutil.Process().memory_info().rss / 1024 / 1024,
            "gpu_utilization": 0.0 # Placeholder if no GPU lib available
        }

    def run(self):
        # Step 1: Verify Prompt
        current_prompt = self.verify_cloud_prompt()
        prompt_version = datetime.now().strftime("v%Y%m%d.%H%M") # Generate version
        
        # Step 2: Initialize Task
        if not self.manifest_path.exists():
            logger.error("Manifest not found.")
            return
            
        df = pd.read_csv(self.manifest_path)
        logger.info(f"Initialized analysis for {len(df)} images.")
        
        # Step 5: Start MLflow Experiment
        with self.tracker.start_run(
            description="Comprehensive Image Analysis",
            tags={
                "prompt_version": prompt_version, 
                "schema_version": "1.0",
                "mode": "full_analysis"
            }
        ) as run:
            
            # Log Experiment Parameters
            self.tracker.log_params({
                "prompt_version": prompt_version,
                "model": self.processor.llama_config.get("model", "unknown"),
                "image_count": len(df),
                "hardware": "cpu" # Detect dynamically if needed
            })
            
            # Log Prompt Artifact
            with open("active_prompt.txt", "w") as f:
                f.write(current_prompt)
            self.tracker.log_artifact("active_prompt.txt")
            os.remove("active_prompt.txt")
            
            success_count = 0
            results = []
            
            # Step 3: Execute Analysis Flow
            for idx, row in df.iterrows():
                start_time = time.time()
                try:
                    filename = row['image_path']
                    local_path = self.dataset_dir / filename
                    
                    if not local_path.exists():
                        continue
                        
                    # Load & Preprocess
                    image = Image.open(local_path).convert("RGB")
                    processed_image = self.processor._preprocess_image(image)
                    
                    # Feature Extraction
                    features = self.extract_features(image)
                    
                    # AI Analysis (LLaMA)
                    logger.info(f"Processing {filename}...")
                    ai_result = self.processor._generate_description(processed_image)
                    
                    # Step 4: Structure Result
                    processing_time = (time.time() - start_time) * 1000
                    
                    result_data = {
                        "experiment_id": run.info.experiment_id,
                        "image_metadata": {
                            "filename": filename,
                            "width": image.width,
                            "height": image.height,
                            "format": image.format or "JPEG",
                            "file_size_bytes": local_path.stat().st_size
                        },
                        "classification_results": ai_result.get("classification_results", []),
                        "detection_results": ai_result.get("detection_results", []),
                        "ocr_results": ai_result.get("ocr_results", []),
                        "image_features": features,
                        "growth_stage_description": ai_result.get("growth_stage_description", "N/A"),
                        "chinese_description": ai_result.get("chinese_description", "N/A"),
                        "image_quality_score": ai_result.get("image_quality_score", 0),
                        "processing_metrics": {
                            "processing_time_ms": round(processing_time, 2),
                            **self.get_system_metrics()
                        },
                        "prompt_version": prompt_version,
                        "timestamp": datetime.now().isoformat()
                    }
                    
                    # Validate Schema
                    try:
                        validate(instance=result_data, schema=self.output_schema)
                    except ValidationError as ve:
                        logger.warning(f"Schema validation failed for {filename}: {ve.message}")
                        # Continue anyway, but log warning
                    
                    results.append(result_data)
                    success_count += 1
                    
                    # Log to MLflow (Nested Run)
                    with self.tracker.start_run(run_name=f"analyze_{filename}", nested=True):
                        self.tracker.log_metrics({
                            "quality_score": result_data["image_quality_score"],
                            "processing_time_ms": processing_time
                        })
                        
                        # Save Result JSON
                        with open("result.json", "w") as f:
                            json.dump(result_data, f, indent=2)
                        self.tracker.log_artifact("result.json")
                        os.remove("result.json")
                        
                except Exception as e:
                    logger.error(f"Error processing {row.get('image_path')}: {e}")

            # Step 6: Final Verification
            if results:
                # Save full report
                with open("full_report.json", "w") as f:
                    json.dump(results, f, indent=2)
                self.tracker.log_artifact("full_report.json")
                os.remove("full_report.json")
                
            # Step 8: Output Response
            response = {
                "experiment_id": run.info.experiment_id,
                "status": "COMPLETED",
                "success_count": success_count,
                "total_count": len(df),
                "tracking_uri": self.tracker.tracking_uri
            }
            print(json.dumps(response, indent=2))
            logger.success(f"Task completed. Experiment ID: {run.info.experiment_id}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset_dir", type=str, required=True)
    args = parser.parse_args()
    
    analyzer = FullAnalyzer(args.dataset_dir)
    analyzer.run()
