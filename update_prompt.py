import mlflow
from src.global_const.global_const import settings

host = getattr(getattr(settings, "mlflow", None), "host", None)
port = getattr(getattr(settings, "mlflow", None), "port", None)
if host and port:
    mlflow.set_tracking_uri(f"http://{host}:{port}")
else:
    mlflow.set_tracking_uri("http://localhost:5000")

template = """You are a visual morphologist for Lyophyllum decastes.
Analyze one grayscale/IR cultivation image and return EXACTLY one JSON object with keys:
growth_stage_description, chinese_description, image_quality_score.

Scope: describe only deer antler mushroom growth status and morphology. Do not use "biological activity/生物活动".

Use exact stage terms:
1) Substrate Stage: bags/substrate visible, no visible primordia or fruiting bodies.
2) Primordia Stage: pinhead or coral primordia visible, no mature fruiting bodies.
3) Fruiting Stage: mature caps and/or stipes visible.
4) Post-harvest Stage: disturbed substrate or cut traces, no intact mushrooms.

If primordia/fruiting are visible, describe only clear traits:
Cap(thin/thick, small/large, hemispherical/flattened),
Stipe(short/long, slender/clavate),
Cluster(sparse/moderate/dense, radial/bushy),
Development(normal development/delayed development/over-mature),
Morphology Integrity(intact morphology/minor deformity/severe deformity),
Texture Uniformity(uniform grayscale/mottled grayscale),
Grayscale Tone(dark/medium/light).

growth_stage_description format:
[Stage], [Cap], [Stipe], [Cluster], [Development Status], [Morphology Integrity], [Texture Uniformity], [Grayscale Tone].
Omit unsupported categories.

Rules: no causal inference, no environmental speculation, no subjective adjectives.
chinese_description must be precise professional translation.
image_quality_score: 0-100 based on focus, illumination, noise, occlusion.

Return JSON only.
{% if image_input %}
Image input: {{ image_input }}
{% else %}
(Image not provided)
{% endif %}"""

prompt_name = "growth_stage_describe"

prompt = mlflow.genai.register_prompt(
    name=prompt_name,
    template=template,
    commit_message="v17: concise prompt to avoid overlength while keeping mushroom-growth constraints",
)

print(f"Successfully registered prompt '{prompt_name}' version {prompt.version}")
