import base64
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image
from PIL.Image import Image as PILImageType


@dataclass(frozen=True)
class LlamaImageCompressionConfig:
    target_width: int = 960
    target_height: int = 960
    jpeg_quality: int = 80
    min_jpeg_quality: int = 50
    max_image_bytes: int = 350 * 1024
    downscale_step_percent: int = 15
    ssim_threshold: float = 0.95
    quality_search_steps: int = 7
    max_downscale_attempts: int = 4

    def normalized(self) -> "LlamaImageCompressionConfig":
        jpeg_quality = max(20, min(95, int(self.jpeg_quality)))
        return LlamaImageCompressionConfig(
            target_width=max(64, int(self.target_width)),
            target_height=max(64, int(self.target_height)),
            jpeg_quality=jpeg_quality,
            min_jpeg_quality=max(20, min(jpeg_quality, int(self.min_jpeg_quality))),
            max_image_bytes=max(64 * 1024, int(self.max_image_bytes)),
            downscale_step_percent=max(5, min(40, int(self.downscale_step_percent))),
            ssim_threshold=max(0.7, min(0.999, float(self.ssim_threshold))),
            quality_search_steps=max(3, min(10, int(self.quality_search_steps))),
            max_downscale_attempts=max(1, min(8, int(self.max_downscale_attempts))),
        )


@dataclass(frozen=True)
class LlamaEncodedImage:
    image_data: str
    image_bytes: bytes
    resized_image: PILImageType
    compression_applied: bool
    attempt: int
    selected_quality: int
    ssim: float
    encoded_size: tuple[int, int]
    target_ssim: float


@dataclass(frozen=True)
class ResizedImage:
    image: PILImageType
    original_size: tuple[int, int]
    resized_size: tuple[int, int]

    @property
    def changed(self) -> bool:
        return self.original_size != self.resized_size


def resize_image_for_llama(
    image: PILImageType,
    target_width: int = 960,
    target_height: int = 960,
) -> ResizedImage:
    target_width = max(64, int(target_width))
    target_height = max(64, int(target_height))
    original_width, original_height = image.size

    scale_ratio = min(
        target_width / original_width,
        target_height / original_height,
        1.0,
    )

    new_width = max(1, int(original_width * scale_ratio))
    new_height = max(1, int(original_height * scale_ratio))

    if new_width == original_width and new_height == original_height:
        return ResizedImage(image, image.size, image.size)

    resized_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
    return ResizedImage(resized_image, image.size, resized_image.size)


def compute_ssim(source_image: PILImageType, target_image: PILImageType) -> float:
    src_gray = np.asarray(source_image.convert("L"), dtype=np.float64)
    tgt_gray = np.asarray(target_image.convert("L"), dtype=np.float64)

    if src_gray.shape != tgt_gray.shape:
        tgt_gray = np.asarray(
            target_image.convert("L").resize(
                (src_gray.shape[1], src_gray.shape[0]),
                Image.Resampling.BILINEAR,
            ),
            dtype=np.float64,
        )

    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2

    mu_src = src_gray.mean()
    mu_tgt = tgt_gray.mean()
    var_src = src_gray.var()
    var_tgt = tgt_gray.var()
    cov = ((src_gray - mu_src) * (tgt_gray - mu_tgt)).mean()

    numerator = (2 * mu_src * mu_tgt + c1) * (2 * cov + c2)
    denominator = (mu_src**2 + mu_tgt**2 + c1) * (var_src + var_tgt + c2)

    if denominator <= 0:
        return 0.0
    return float(max(0.0, min(1.0, numerator / denominator)))


def encode_jpeg_bytes(image: PILImageType, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def binary_search_quality(
    source_image: PILImageType,
    max_image_bytes: int,
    min_jpeg_quality: int,
    max_jpeg_quality: int,
    ssim_threshold: float,
    quality_search_steps: int,
) -> tuple[bytes, int, float]:
    low = min_jpeg_quality
    high = max_jpeg_quality

    best_bytes = encode_jpeg_bytes(source_image, min_jpeg_quality)
    best_quality = min_jpeg_quality
    best_ssim = 0.0

    for _ in range(quality_search_steps):
        if low > high:
            break

        mid = (low + high) // 2
        encoded = encode_jpeg_bytes(source_image, mid)

        decoded = Image.open(io.BytesIO(encoded)).convert("RGB")
        current_ssim = compute_ssim(source_image, decoded)
        size_ok = len(encoded) <= max_image_bytes
        ssim_ok = current_ssim >= ssim_threshold

        if size_ok and ssim_ok:
            if mid >= best_quality:
                best_bytes = encoded
                best_quality = mid
                best_ssim = current_ssim
            low = mid + 1
        else:
            high = mid - 1

    if best_ssim <= 0.0:
        fallback_quality = min_jpeg_quality
        for quality in range(max_jpeg_quality, min_jpeg_quality - 1, -5):
            encoded = encode_jpeg_bytes(source_image, quality)
            if len(encoded) <= max_image_bytes:
                decoded = Image.open(io.BytesIO(encoded)).convert("RGB")
                best_ssim = compute_ssim(source_image, decoded)
                best_bytes = encoded
                fallback_quality = quality
                break
        best_quality = fallback_quality

    return best_bytes, best_quality, best_ssim


def encode_image_for_llama_with_meta(
    image: PILImageType,
    config: LlamaImageCompressionConfig | None = None,
) -> LlamaEncodedImage:
    config = (config or LlamaImageCompressionConfig()).normalized()
    resized = resize_image_for_llama(
        image,
        target_width=config.target_width,
        target_height=config.target_height,
    )

    resized_image = resized.image.convert("RGB") if resized.image.mode != "RGB" else resized.image
    current_image = resized_image
    jpeg_quality = config.jpeg_quality

    last_bytes = b""
    last_quality = config.min_jpeg_quality
    last_ssim = 0.0

    for attempt in range(1, config.max_downscale_attempts + 1):
        image_bytes, selected_quality, current_ssim = binary_search_quality(
            source_image=current_image,
            max_image_bytes=config.max_image_bytes,
            min_jpeg_quality=config.min_jpeg_quality,
            max_jpeg_quality=jpeg_quality,
            ssim_threshold=config.ssim_threshold,
            quality_search_steps=config.quality_search_steps,
        )
        last_bytes = image_bytes
        last_quality = selected_quality
        last_ssim = current_ssim

        if len(image_bytes) <= config.max_image_bytes:
            return LlamaEncodedImage(
                image_data=base64.b64encode(image_bytes).decode("utf-8"),
                image_bytes=image_bytes,
                resized_image=resized_image,
                compression_applied=attempt > 1 or current_ssim < config.ssim_threshold,
                attempt=attempt,
                selected_quality=selected_quality,
                ssim=current_ssim,
                encoded_size=current_image.size,
                target_ssim=config.ssim_threshold,
            )

        width, height = current_image.size
        scale = (100 - config.downscale_step_percent) / 100
        next_width = max(64, int(width * scale))
        next_height = max(64, int(height * scale))

        if (next_width, next_height) == (width, height):
            return LlamaEncodedImage(
                image_data=base64.b64encode(image_bytes).decode("utf-8"),
                image_bytes=image_bytes,
                resized_image=resized_image,
                compression_applied=True,
                attempt=attempt,
                selected_quality=selected_quality,
                ssim=current_ssim,
                encoded_size=current_image.size,
                target_ssim=config.ssim_threshold,
            )

        current_image = current_image.resize(
            (next_width, next_height), Image.Resampling.LANCZOS
        )
        jpeg_quality = max(config.min_jpeg_quality, jpeg_quality - 5)

    final_bytes = encode_jpeg_bytes(current_image, config.min_jpeg_quality)
    if not last_bytes:
        last_quality = config.min_jpeg_quality
        last_ssim = 0.0
    return LlamaEncodedImage(
        image_data=base64.b64encode(final_bytes).decode("utf-8"),
        image_bytes=final_bytes,
        resized_image=resized_image,
        compression_applied=True,
        attempt=config.max_downscale_attempts,
        selected_quality=last_quality,
        ssim=last_ssim,
        encoded_size=current_image.size,
        target_ssim=config.ssim_threshold,
    )
