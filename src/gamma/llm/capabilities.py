from __future__ import annotations

from dataclasses import dataclass

from ..config import MODELS_CONFIG


@dataclass(frozen=True, slots=True)
class ModelCapability:
    provider: str
    model: str
    advertised_context_tokens: int
    usable_input_tokens: int
    reserved_output_tokens: int
    safety_margin_tokens: int
    persona_capable: bool = True
    vision_capable: bool = False
    tools_capable: bool = False
    provenance: str = "configured"


def model_capability(provider: str, model: str | None) -> ModelCapability:
    normalized_provider = str(provider or "").strip().lower()
    normalized_model = str(model or "").strip()
    entries = MODELS_CONFIG.get("model_capabilities", [])
    if isinstance(entries, list):
        for raw in entries:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("provider") or "").strip().lower() != normalized_provider:
                continue
            if str(raw.get("model") or "").strip() != normalized_model:
                continue
            advertised = max(2048, int(raw.get("advertised_context_tokens") or 8192))
            reserved = max(256, int(raw.get("reserved_output_tokens") or 1024))
            margin = max(256, int(raw.get("safety_margin_tokens") or 1024))
            usable = max(1024, int(raw.get("usable_input_tokens") or advertised - reserved - margin))
            usable = min(usable, max(1024, advertised - reserved - margin))
            return ModelCapability(
                provider=normalized_provider,
                model=normalized_model,
                advertised_context_tokens=advertised,
                usable_input_tokens=usable,
                reserved_output_tokens=reserved,
                safety_margin_tokens=margin,
                persona_capable=bool(raw.get("persona_capable", True)),
                vision_capable=bool(raw.get("vision_capable", False)),
                tools_capable=bool(raw.get("tools_capable", False)),
                provenance=str(raw.get("provenance") or "configured"),
            )
    return ModelCapability(
        provider=normalized_provider,
        model=normalized_model,
        advertised_context_tokens=8192,
        usable_input_tokens=6144,
        reserved_output_tokens=1024,
        safety_margin_tokens=1024,
        persona_capable=normalized_provider != "mock",
        provenance="estimated",
    )


def estimate_text_tokens(*parts: str) -> int:
    chars = sum(len(part or "") for part in parts)
    return max(1, (chars + 3) // 4)
