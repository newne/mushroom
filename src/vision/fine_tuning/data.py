"""
数据加载与增强模块

支持图像-文本对数据集加载与增强。
"""

from __future__ import annotations

import csv
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
from PIL import Image, ImageEnhance, ImageOps


@dataclass
class ImageTextRecord:
    """图文对标注记录。"""
    image_path: str
    text: str
    label: Optional[str] = None
    split: Optional[str] = None


class ImageAugmentor:
    """图像增强器，包含随机裁剪、颜色抖动与翻转。"""
    def __init__(
        self,
        image_size: int,
        random_crop_scale: List[float],
        color_jitter: List[float],
        hflip_prob: float,
    ) -> None:
        self.image_size = image_size
        self.random_crop_scale = random_crop_scale
        self.color_jitter = color_jitter
        self.hflip_prob = hflip_prob

    def __call__(self, image: Image.Image) -> Image.Image:
        """对单张图像执行增强。"""
        image = image.convert("RGB")
        image = self._random_resized_crop(image)
        image = self._color_jitter(image)
        if random.random() < self.hflip_prob:
            image = ImageOps.mirror(image)
        return image

    def _random_resized_crop(self, image: Image.Image) -> Image.Image:
        width, height = image.size
        scale_min, scale_max = self.random_crop_scale
        scale = random.uniform(scale_min, scale_max)
        new_w = int(width * scale)
        new_h = int(height * scale)
        if new_w < 1 or new_h < 1:
            return image.resize((self.image_size, self.image_size), Image.BICUBIC)
        left = random.randint(0, max(0, width - new_w))
        top = random.randint(0, max(0, height - new_h))
        cropped = image.crop((left, top, left + new_w, top + new_h))
        return cropped.resize((self.image_size, self.image_size), Image.BICUBIC)

    def _color_jitter(self, image: Image.Image) -> Image.Image:
        brightness, contrast, saturation = self.color_jitter
        if brightness > 0:
            enhancer = ImageEnhance.Brightness(image)
            image = enhancer.enhance(1 + random.uniform(-brightness, brightness))
        if contrast > 0:
            enhancer = ImageEnhance.Contrast(image)
            image = enhancer.enhance(1 + random.uniform(-contrast, contrast))
        if saturation > 0:
            enhancer = ImageEnhance.Color(image)
            image = enhancer.enhance(1 + random.uniform(-saturation, saturation))
        return image


class TextAugmentor:
    """文本增强器，基于同义词替换生成变体文本。"""
    def __init__(
        self,
        synonym_prob: float,
        max_replacements: int,
        synonym_map: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        self.synonym_prob = synonym_prob
        self.max_replacements = max_replacements
        self.synonym_map = synonym_map or {}
        self.word_pattern = re.compile(r"[A-Za-z]+|[\u4e00-\u9fff]")

    def __call__(self, text: str) -> str:
        """对单条文本执行增强。"""
        tokens = self.word_pattern.findall(text)
        if not tokens:
            return text
        replacements = 0
        new_tokens = []
        for token in tokens:
            if replacements >= self.max_replacements:
                new_tokens.append(token)
                continue
            if token.lower() in self.synonym_map and random.random() < self.synonym_prob:
                candidates = self.synonym_map[token.lower()]
                if candidates:
                    new_tokens.append(random.choice(candidates))
                    replacements += 1
                else:
                    new_tokens.append(token)
            else:
                new_tokens.append(token)
        return "".join(new_tokens) if _contains_cjk(text) else " ".join(new_tokens)


class ImageTextPairDataset:
    """图文对数据集，返回图像、文本与标签。"""
    def __init__(
        self,
        annotations_path: str,
        image_root: str,
        image_augmentor: Optional[ImageAugmentor] = None,
        text_augmentor: Optional[TextAugmentor] = None,
        max_samples: Optional[int] = None,
    ) -> None:
        self.records = load_annotations(annotations_path)
        self.image_root = Path(image_root).expanduser().resolve() if image_root else None
        self.image_augmentor = image_augmentor
        self.text_augmentor = text_augmentor
        if max_samples is not None:
            self.records = self.records[:max_samples]

    def __len__(self) -> int:
        """返回样本数量。"""
        return len(self.records)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        """根据索引读取样本并应用增强。"""
        record = self.records[index]
        image_path = Path(record.image_path)
        if self.image_root and not image_path.is_absolute():
            image_path = self.image_root / image_path
        image = Image.open(image_path)
        if self.image_augmentor:
            image = self.image_augmentor(image)
        text = record.text
        if self.text_augmentor:
            text = self.text_augmentor(text)
        return {
            "image": image,
            "text": text,
            "label": record.label,
            "image_path": str(image_path),
        }


def load_annotations(path: str) -> List[ImageTextRecord]:
    """加载标注文件并转换为 ImageTextRecord 列表。"""
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"标注文件不存在: {file_path}")
    if file_path.suffix.lower() in {".jsonl"}:
        return _load_jsonl(file_path)
    if file_path.suffix.lower() in {".json"}:
        return _load_json(file_path)
    if file_path.suffix.lower() in {".csv"}:
        return _load_csv(file_path)
    raise ValueError(f"不支持的标注文件格式: {file_path}")


def load_synonym_map(path: Optional[str]) -> Dict[str, List[str]]:
    """读取同义词字典，若不存在则回退默认词表。"""
    if not path:
        return _default_synonyms()
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists():
        return _default_synonyms()
    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_jsonl(file_path: Path) -> List[ImageTextRecord]:
    records = []
    with file_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            records.append(_record_from_dict(item))
    return records


def _load_json(file_path: Path) -> List[ImageTextRecord]:
    with file_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if not isinstance(data, list):
        raise ValueError("JSON 标注格式必须是列表")
    return [_record_from_dict(item) for item in data]


def _load_csv(file_path: Path) -> List[ImageTextRecord]:
    records = []
    with file_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(_record_from_dict(row))
    return records


def _record_from_dict(item: Dict[str, Any]) -> ImageTextRecord:
    return ImageTextRecord(
        image_path=str(item.get("image_path") or item.get("image") or item.get("path")),
        text=str(item.get("text") or item.get("caption") or item.get("description") or ""),
        label=item.get("label"),
        split=item.get("split"),
    )


def _default_synonyms() -> Dict[str, List[str]]:
    return {
        "good": ["great", "nice", "excellent"],
        "bad": ["poor", "terrible", "awful"],
        "large": ["big", "huge", "vast"],
        "small": ["tiny", "little", "mini"],
        "fresh": ["new", "clean"],
        "old": ["aged", "ancient"],
        "mushroom": ["fungus"],
        "healthy": ["well", "robust"],
    }


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)
