# LLaMA Visual Language Model Integration Guide

## Overview
This document describes the integration of the LLaMA Visual Language Model (VLM) into the Mushroom Algorithm's `safe_hourly_clip_inference` task. The integration allows the system to generate semantic descriptions of mushroom growth stages from images, which are then used to enhance the CLIP embedding process.

## Configuration (`src/configs/settings.toml`)

A new configuration section `[development.llama-vl]` (and corresponding production section) has been added to control the VLM behavior.

```toml
[development.llama_vl]
llama_host = "10.77.77.49"
llama_port = "7001"
model = "qwen/qwen3-vl-2b"
llama_completions = "http://{0}:{1}/v1/chat/completions"
enabled = true  # Set to false to disable VLM calls
timeout = 600   # API timeout in seconds
temperature = 0.7
max_tokens = 1024
top_p = 0.9
image_width = 960  # Resize target width
image_height = 960 # Resize target height
device = "cuda"
```

### Key Parameters
- **enabled**: Master switch to enable/disable VLM integration.
- **image_width/image_height**: Images are resized (LANCZOS) to fit within these dimensions before being sent to the API to optimize latency and token usage.
- **model**: Specifies the model name passed in the API payload.

## Code Changes

### `src/vision/mushroom_image_encoder.py`
The `MushroomImageEncoder` class has been updated to:
1.  **Strict Configuration Loading**: It specifically looks for `llama_vl` configuration. It **does not** fall back to the text-only `llama` configuration to avoid sending images to incompatible models.
2.  **Image Resizing**: Implemented `_resize_image_for_llama` to resize images based on configuration.
3.  **API Integration**: Updated `_call_llama_api` to use the configured VLM parameters and handle JSON responses.
4.  **Integration Flow**:
    -   Process image -> Get Environment Data -> **Call LLaMA VLM** -> Combine Metadata + LLaMA Description -> Generate Multimodal CLIP Embedding -> Save to DB.

### `src/vision/tasks.py`
The `safe_hourly_clip_inference` task automatically utilizes the new capabilities via `MushroomImageEncoder`. No direct changes were needed in the task loop logic.

## Testing

### Unit Tests
A new test file `tests/unit/test_llama_integration.py` has been added to verify:
-   Correct loading of `llama-vl` configuration.
-   Correct image resizing logic.
-   Mocked API calls to ensure robust error handling.

Run tests with:
```bash
python -m pytest tests/unit/test_llama_integration.py
```

## Troubleshooting
-   **"LLaMA client not available"**: Check if `[development.llama-vl]` exists in `settings.toml` and `enabled = true`.
-   **API Errors**: Check network connectivity to `llama_host` and ensure the model service supports the OpenAI Chat Completions API format with image inputs.
