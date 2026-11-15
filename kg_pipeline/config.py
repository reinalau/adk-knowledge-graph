"""Model selection. Default: Amazon Nova on Bedrock through LiteLLM (no Gemini quota needed)."""
import os
from pathlib import Path

from dotenv import load_dotenv
from google.adk.models.lite_llm import LiteLlm
from google.genai import types

# One .env at the project root works for `adk web`, `main.py` and `evals/`.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Nova Lite (v1) returned empty relation objects ({}) in the aggregator step; Nova 2 Lite and Pro did not.
# In a side-by-side run on a real abstract, Nova Pro was not clearly better and costs more.
# Set BEDROCK_MODEL_ID=us.amazon.nova-pro-v1:0 to try it.
DEFAULT_BEDROCK_MODEL = "us.amazon.nova-2-lite-v1:0"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# Low temperature: extraction/validation should be as repeatable as possible.
GENERATE_CONFIG = types.GenerateContentConfig(temperature=0.2, max_output_tokens=2048)


def get_model():
    """Return the model for every agent, selected with MODEL_PROVIDER (bedrock | gemini)."""
    provider = os.getenv("MODEL_PROVIDER", "bedrock").lower()
    if provider == "bedrock":
        model_id = os.getenv("BEDROCK_MODEL_ID", DEFAULT_BEDROCK_MODEL)
        # Credentials and region come from the standard AWS chain (env vars, profile, IAM role).
        return LiteLlm(model=f"bedrock/{model_id}")
    if provider == "gemini":
        if not os.getenv("GOOGLE_API_KEY"):
            raise RuntimeError("MODEL_PROVIDER=gemini requires GOOGLE_API_KEY")
        return os.getenv("GEMINI_MODEL_ID", DEFAULT_GEMINI_MODEL)
    raise ValueError(f"Unknown MODEL_PROVIDER: {provider!r} (use 'bedrock' or 'gemini')")
