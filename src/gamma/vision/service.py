from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from ..errors import ConversationError
from ..llm.base import LLMAdapter, LLMCallContext, LLMImageInput
from ..schemas.response import VisionAnalysis, VisionInterfaceElement, VisionObject, VisionTextBlock


ALLOWED_IMAGE_MEDIA_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


@dataclass(slots=True)
class VisionImage:
    data: bytes
    media_type: str
    filename: str | None = None
    stored_path: Path | None = None

    def to_llm_input(self) -> LLMImageInput:
        return LLMImageInput(data=self.data, media_type=self.media_type, filename=self.filename)


class VisionService:
    def prepare_image(self, *, image_bytes: bytes, media_type: str, filename: str | None = None) -> VisionImage:
        if not image_bytes:
            raise ConversationError("image_file must not be empty.")
        normalized_media_type = (media_type or "").strip().lower()
        if normalized_media_type not in ALLOWED_IMAGE_MEDIA_TYPES:
            allowed = ", ".join(sorted(ALLOWED_IMAGE_MEDIA_TYPES))
            raise ConversationError(f"Unsupported image content type: {media_type or 'unknown'}. Allowed: {allowed}.")
        if len(image_bytes) > settings.vision_max_image_bytes:
            raise ConversationError(
                f"Image exceeds SHANA_VISION_MAX_IMAGE_BYTES ({settings.vision_max_image_bytes} bytes)."
            )

        digest = hashlib.sha256(image_bytes).hexdigest()[:16]
        extension = ALLOWED_IMAGE_MEDIA_TYPES[normalized_media_type]
        stored_path = settings.image_input_dir / f"{digest}{extension}"
        if not stored_path.exists():
            stored_path.write_bytes(image_bytes)

        return VisionImage(
            data=image_bytes,
            media_type=normalized_media_type,
            filename=filename,
            stored_path=stored_path,
        )

    def analyze_image(
        self,
        *,
        llm_adapter: LLMAdapter,
        image: VisionImage,
        user_text: str | None = None,
        mode: str | None = None,
    ) -> VisionAnalysis:
        if not llm_adapter.supports_vision:
            raise ConversationError(
                "The configured LLM provider does not support image analysis. Set SHANA_LLM_PROVIDER=openai to enable vision."
            )
        normalized_mode = self._normalize_mode(mode)
        analysis_prompt = (
            "You are a vision analysis engine for a multimodal assistant.\n"
            "Inspect the attached image and return exactly one JSON object with these keys:\n"
            "image_type: one of photo, screenshot, document, drawing, unknown.\n"
            "summary: short scene/document summary.\n"
            "visible_text: plain text visible in the image, or null if none. Preserve important wording when possible.\n"
            "objects: array of up to 8 objects with keys name, description, confidence.\n"
            "key_text_blocks: array of up to 8 blocks with keys label, text, block_type.\n"
            "interface_elements: array of up to 8 UI items with keys name, element_type, role, state.\n"
            "document_structure: array of up to 8 short notes about sections, headings, lists, tables, or layout.\n"
            "likely_actions: array of up to 6 short action suggestions a user could take based on the image.\n"
            "spatial_notes: array of short notes about positions, layout, or relationships.\n"
            "suggested_follow_ups: array of up to 3 helpful follow-up questions the user might ask.\n"
            "confidence: overall confidence from 0.0 to 1.0.\n"
            "Do not wrap the JSON in markdown fences.\n"
            "Be especially careful extracting text from screenshots, signs, labels, and documents."
        )
        if normalized_mode == "screen":
            analysis_prompt += (
                "\nThe user's priority is screen understanding. Focus on interface layout, visible labels, controls, menus, error text, and task-relevant UI state."
            )
        elif normalized_mode == "document":
            analysis_prompt += (
                "\nThe user's priority is document reading. Focus on extracting readable text, headings, lists, tables, and important document structure."
            )
        elif normalized_mode == "photo":
            analysis_prompt += (
                "\nThe user's priority is scene understanding. Focus on objects, people, actions, environment, and spatial relationships."
            )
        analysis_input = (
            f"User request context:\n{(user_text or 'Analyze this image.').strip()}\n\n"
            f"Requested vision mode: {normalized_mode}.\n"
            "Return the JSON object only."
        )
        try:
            raw = llm_adapter.generate_reply(
                system_prompt=analysis_prompt,
                user_text=analysis_input,
                image_inputs=[image.to_llm_input()],
                call_context=LLMCallContext(
                    purpose="vision_analysis",
                    reasoning_depth="normal",
                    interaction_mode="vision",
                ),
            ).text
            payload = self._parse_json_object(raw)
        except Exception as exc:
            raise ConversationError(f"Vision analysis failed: {exc}") from exc
        return self._normalize_analysis(payload)

    def _parse_json_object(self, raw: str) -> dict:
        stripped = raw.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 3:
                stripped = "\n".join(lines[1:-1]).strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("no JSON object found")
        return json.loads(stripped[start : end + 1])

    def _normalize_analysis(self, payload: dict) -> VisionAnalysis:
        image_type = str(payload.get("image_type", "unknown")).strip().lower() or "unknown"
        if image_type not in {"photo", "screenshot", "document", "drawing", "unknown"}:
            image_type = "unknown"
        summary = " ".join(str(payload.get("summary", "")).strip().split())
        if not summary:
            summary = "Image analysis was inconclusive."
        visible_text_raw = payload.get("visible_text")
        visible_text = None
        if isinstance(visible_text_raw, str):
            normalized_text = visible_text_raw.strip()
            visible_text = normalized_text[:4000] if normalized_text else None
        key_text_blocks = self._normalize_text_blocks(payload.get("key_text_blocks", []))
        interface_elements = self._normalize_interface_elements(payload.get("interface_elements", []))
        document_structure = self._normalize_string_list(payload.get("document_structure", []), limit=8, max_len=240)
        likely_actions = self._normalize_string_list(payload.get("likely_actions", []), limit=6, max_len=160)
        spatial_raw = payload.get("spatial_notes", [])
        follow_raw = payload.get("suggested_follow_ups", [])
        objects_raw = payload.get("objects", [])
        confidence_raw = payload.get("confidence", 0.0)
        try:
            confidence = max(0.0, min(1.0, float(confidence_raw)))
        except (TypeError, ValueError):
            confidence = 0.0
        objects: list[VisionObject] = []
        if isinstance(objects_raw, list):
            for item in objects_raw[:8]:
                if not isinstance(item, dict):
                    continue
                name = " ".join(str(item.get("name", "")).strip().split())
                if not name:
                    continue
                description_raw = item.get("description")
                description = None
                if isinstance(description_raw, str):
                    description = " ".join(description_raw.strip().split()) or None
                object_confidence_raw = item.get("confidence", confidence)
                try:
                    object_confidence = max(0.0, min(1.0, float(object_confidence_raw)))
                except (TypeError, ValueError):
                    object_confidence = confidence
                objects.append(
                    VisionObject(
                        name=name[:120],
                        description=description[:240] if description else None,
                        confidence=object_confidence,
                    )
                )
        spatial_notes = self._normalize_string_list(spatial_raw, limit=8, max_len=240)
        suggested_follow_ups = self._normalize_string_list(follow_raw, limit=3, max_len=160)
        return VisionAnalysis(
            image_type=image_type,
            summary=summary[:600],
            visible_text=visible_text,
            objects=objects,
            key_text_blocks=key_text_blocks,
            interface_elements=interface_elements,
            document_structure=document_structure,
            likely_actions=likely_actions,
            spatial_notes=spatial_notes,
            suggested_follow_ups=suggested_follow_ups,
            confidence=confidence,
        )

    def _normalize_string_list(self, value: object, *, limit: int, max_len: int) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value[:limit]:
            text = " ".join(str(item).strip().split())
            if text:
                normalized.append(text[:max_len])
        return normalized

    def _normalize_text_blocks(self, value: object) -> list[VisionTextBlock]:
        if not isinstance(value, list):
            return []
        blocks: list[VisionTextBlock] = []
        for item in value[:8]:
            if not isinstance(item, dict):
                continue
            label = " ".join(str(item.get("label", "")).strip().split())
            text = str(item.get("text", "")).strip()
            block_type = " ".join(str(item.get("block_type", "text")).strip().split()).lower() or "text"
            if not text:
                continue
            blocks.append(
                VisionTextBlock(
                    label=label[:120] if label else "text",
                    text=text[:800],
                    block_type=block_type[:60],
                )
            )
        return blocks

    def _normalize_interface_elements(self, value: object) -> list[VisionInterfaceElement]:
        if not isinstance(value, list):
            return []
        elements: list[VisionInterfaceElement] = []
        for item in value[:8]:
            if not isinstance(item, dict):
                continue
            name = " ".join(str(item.get("name", "")).strip().split())
            if not name:
                continue
            element_type = " ".join(str(item.get("element_type", "unknown")).strip().split()).lower() or "unknown"
            role_raw = item.get("role")
            state_raw = item.get("state")
            role = " ".join(str(role_raw).strip().split())[:120] if isinstance(role_raw, str) and role_raw.strip() else None
            state = " ".join(str(state_raw).strip().split())[:120] if isinstance(state_raw, str) and state_raw.strip() else None
            elements.append(
                VisionInterfaceElement(
                    name=name[:120],
                    element_type=element_type[:60],
                    role=role,
                    state=state,
                )
            )
        return elements

    def _normalize_mode(self, value: str | None) -> str:
        normalized = (value or "").strip().lower()
        if normalized in {"auto", "screen", "document", "photo"}:
            return normalized
        return "auto"
