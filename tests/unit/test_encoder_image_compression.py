import base64

from PIL import Image

from vision.encoder_image.compression import (
    LlamaImageCompressionConfig,
    binary_search_quality,
    compute_ssim,
    encode_image_for_llama_with_meta,
    encode_jpeg_bytes,
    resize_image_for_llama,
)


def test_resize_image_for_llama_keeps_small_image():
    image = Image.new("RGB", (120, 80), "white")

    resized = resize_image_for_llama(image, target_width=960, target_height=960)

    assert resized.image is image
    assert resized.original_size == (120, 80)
    assert resized.resized_size == (120, 80)
    assert not resized.changed


def test_resize_image_for_llama_constrains_long_side():
    image = Image.new("RGB", (2000, 1000), "white")

    resized = resize_image_for_llama(image, target_width=500, target_height=500)

    assert resized.resized_size == (500, 250)
    assert resized.changed


def test_compute_ssim_identical_image_is_one():
    image = Image.new("RGB", (64, 64), "white")

    assert compute_ssim(image, image) == 1.0


def test_binary_search_quality_returns_jpeg_under_size_limit():
    image = Image.new("RGB", (256, 256), "white")

    image_bytes, quality, ssim = binary_search_quality(
        source_image=image,
        max_image_bytes=64 * 1024,
        min_jpeg_quality=40,
        max_jpeg_quality=80,
        ssim_threshold=0.9,
        quality_search_steps=5,
    )

    assert image_bytes.startswith(b"\xff\xd8")
    assert len(image_bytes) <= 64 * 1024
    assert 40 <= quality <= 80
    assert 0.0 <= ssim <= 1.0


def test_encode_image_for_llama_with_meta_returns_base64_and_bytes():
    image = Image.new("RGB", (300, 160), "white")

    encoded = encode_image_for_llama_with_meta(
        image,
        LlamaImageCompressionConfig(
            target_width=200,
            target_height=200,
            jpeg_quality=75,
            max_image_bytes=64 * 1024,
        ),
    )

    assert base64.b64decode(encoded.image_data) == encoded.image_bytes
    assert encoded.image_bytes == encode_jpeg_bytes(encoded.resized_image, encoded.selected_quality)
    assert encoded.resized_image.size == (200, 106)