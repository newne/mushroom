from collections.abc import Callable


def build_compact_v15_prompt() -> str:
    return (
        "You are a visual morphologist for Lyophyllum decastes in industrial bag cultivation. "
        "Return exactly one JSON object with growth_stage_description, chinese_description, image_quality_score.\n"
        "Use exact stage terms: Substrate Stage, Primordia Stage, Fruiting Stage, Post-harvest Stage.\n"
        "Stage rules: "
        "Substrate Stage=bags/substrate visible, smooth/flat, no visible primordia or fruiting bodies; "
        "Primordia Stage=pinhead/coral primordia visible, no mature fruiting bodies; "
        "Fruiting Stage=mature caps/stipes present; "
        "Post-harvest Stage=bags visible, disturbed substrate, cut traces/residual bases, no intact mushrooms.\n"
        "Visible traits only, no causes/speculation, no subjective words, no verbs.\n"
        "Describe only deer antler mushroom growth status; never use biological-activity wording.\n"
        "When primordia/fruiting visible, use noun-chain traits if clearly visible: "
        "Cap(thin/thick, small/large, hemispherical/flattened, smooth/rough), "
        "Stipe(short/long, slender/clavate, uniform/irregular), "
        "Cluster(sparse/moderate/dense, radial/bushy), "
        "Development(normal development/delayed development/over-mature), "
        "Morphology Integrity(intact morphology/minor deformity/severe deformity), "
        "Texture Uniformity(uniform grayscale/mottled grayscale), "
        "Grayscale Tone(dark/medium/light).\n"
        "growth_stage_description format: "
        "[Stage], [Cap], [Stipe], [Cluster], [Development Status], [Morphology Integrity], [Texture Uniformity], [Grayscale Tone]. "
        "Omit categories with no visible evidence. Post-harvest may skip cap/stipe.\n"
        "chinese_description must be a professional mycological translation.\n"
        "image_quality_score (0-100) based on focus/illumination/noise and obscuration; do not penalize natural absence of mushrooms.\n"
        "Few-shot examples:\n"
        '{"growth_stage_description": "Fruiting Stage, thick caps, long stipes, dense radial cluster, normal development, intact morphology, uniform grayscale, medium grayscale", "chinese_description": "子实体阶段，厚菌盖，长菌 柄，密集放射状菌簇，发育正常，形态完整，灰度均匀，中等灰度", "image_quality_score": 92}\n'
        '{"growth_stage_description": "Primordia Stage, coral primordia, dense cluster, normal development, intact morphology, uniform grayscale, medium grayscale", "chinese_description": "原基阶段，珊瑚状原基，密集菌簇，发育正 常，形态完整，灰度均匀，中等灰度", "image_quality_score": 88}\n'
        '{"growth_stage_description": "Post-harvest Stage, disturbed substrate, cut traces visible, low contrast grayscale", "chinese_description": "已采收阶段，基质扰动，可见切痕，低对比度灰度", "image_quality_score": 45}'
    )


def build_minimal_fallback_prompt() -> str:
    return (
        "Analyze this mushroom image and return EXACTLY one JSON object with keys "
        "growth_stage_description, chinese_description, image_quality_score. "
        "Use growth stages only from: Substrate Stage, Primordia Stage, Fruiting Stage, Post-harvest Stage. "
        "Describe only visible traits and keep concise professional terms."
    )


def apply_prompt_length_guard(
    prompt_text: str | None,
    *,
    max_chars: int = 2600,
    on_compacted: Callable[[int], None] | None = None,
) -> str:
    normalized = str(prompt_text) if prompt_text is not None else ""
    if len(normalized) > max_chars:
        if on_compacted:
            on_compacted(len(normalized))
        return build_compact_v15_prompt()
    return normalized
