I will modify the `analyze_enhanced` workflow to dynamically adapt to the `src/configs/monitoring_points_config.json` configuration.

### 1. Update Data Models (`src/decision_analysis/data_models.py`)
- Modify `EnhancedDeviceRecommendations` to be a dictionary-based structure (or hold a dictionary) to support dynamic device types.
- Create a generic `EnhancedDeviceRecommendation` class that holds a dictionary of `ParameterAdjustment` objects, keyed by point alias.
- This ensures the data structure can accommodate any device and point defined in the config.

### 2. Update Output Handler (`src/decision_analysis/output_handler.py`)
- Modify `validate_and_format_enhanced` to use `monitoring_points_config.json` for validation rules.
- Implement dynamic validation logic that iterates over the configured devices and points.
- Remove hardcoded checks for specific device types (like `air_cooler`, `fresh_air_fan`) in the enhanced validation flow.
- Ensure it constructs the new dynamic data models.

### 3. Update Template Renderer (`src/decision_analysis/template_renderer.py`)
- Add logic to dynamically generate the "Device Status" section for the prompt based on `monitoring_points_config.json`.
- Add logic to dynamically generate the "Device Constraints" section (output requirements) for the prompt.
- This ensures the LLM receives instructions that match the current configuration.

### 4. Update Decision Prompt (`src/configs/decision_prompt.jinja`)
- Replace the hardcoded "Current Device Status" section with a `{device_status_section}` placeholder.
- Replace the hardcoded "Device Parameter Constraints" section with a `{device_constraints_section}` placeholder.

### 5. Update Decision Analyzer (`src/decision_analysis/decision_analyzer.py`)
- Load `src/configs/monitoring_points_config.json` in `__init__`.
- Pass this configuration to `OutputHandler` and `TemplateRenderer`.
- Ensure `analyze_enhanced` uses these updated components.

### 6. Verification
- I will verify that the generated prompt contains the correct device info from the config.
- I will verify that the output validation correctly accepts valid dynamic JSON and rejects invalid ones.
