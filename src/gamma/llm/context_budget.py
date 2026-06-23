from __future__ import annotations

from dataclasses import dataclass

from ..errors import ContextBudgetError
from .capabilities import estimate_text_tokens


@dataclass(frozen=True, slots=True)
class PromptLayer:
    name: str
    text: str
    priority: int
    required: bool = False


@dataclass(frozen=True, slots=True)
class BuiltPrompt:
    system_prompt: str
    user_text: str
    estimated_input_tokens: int
    compaction_level: int
    compaction_actions: tuple[str, ...]
    included_layers: tuple[str, ...]


_KNOWN_HEADERS = (
    "Core Persona",
    "Boundaries",
    "Style",
    "Relationship State",
    "Persona Config",
    "Memory Config",
    "Core Memories",
    "Assistant Feeling State",
    "Relevant Emotional Episodes",
    "Relevant Emotional Patterns",
    "Current Speaker",
    "Runtime Memory",
    "Response Rules",
    "Active Working State",
    "Rolling Session Summary",
    "Recent Conversation Turns",
    "Stream Background Context",
    "Live Voice Brevity",
    "Live Voice Micro Reply",
    "Live Voice Formatting",
)

_POLICY: dict[str, tuple[int, bool]] = {
    "Core Persona": (1, True),
    "Boundaries": (1, True),
    "Response Rules": (1, True),
    "Current Speaker": (2, True),
    "Live Voice Brevity": (2, True),
    "Live Voice Micro Reply": (2, True),
    "Live Voice Formatting": (2, True),
    "Active Working State": (4, False),
    "Rolling Session Summary": (5, False),
    "Core Memories": (8, False),
    "Runtime Memory": (8, False),
    "Recent Conversation Turns": (7, False),
    "Style": (3, False),
    "Relationship State": (3, False),
    "Persona Config": (3, False),
    "Memory Config": (9, False),
    "Assistant Feeling State": (4, False),
    "Relevant Emotional Episodes": (6, False),
    "Relevant Emotional Patterns": (6, False),
    "Stream Background Context": (9, False),
}


def prompt_layers_from_system_prompt(system_prompt: str) -> list[PromptLayer]:
    markers: list[tuple[int, str, str]] = []
    for header in _KNOWN_HEADERS:
        marker = f"# {header}\n"
        start = system_prompt.find(marker)
        if start >= 0:
            markers.append((start, header, marker))
    markers.sort()
    if not markers:
        return [PromptLayer(name="system", text=system_prompt, priority=1, required=True)]
    layers: list[PromptLayer] = []
    for index, (start, header, marker) in enumerate(markers):
        end = markers[index + 1][0] if index + 1 < len(markers) else len(system_prompt)
        text = system_prompt[start:end].strip()
        priority, required = _POLICY.get(header, (9, False))
        layers.append(PromptLayer(name=header, text=text, priority=priority, required=required))
    return layers


class ContextBudgetManager:
    """Materialize priority-ordered prompt layers for one model budget."""

    def build(self, *, layers: list[PromptLayer], user_text: str, usable_input_tokens: int) -> BuiltPrompt:
        budget = max(512, int(usable_input_tokens))
        current = list(layers)
        actions: list[str] = []
        required_text = "\n\n".join(layer.text for layer in current if layer.required)
        required_tokens = estimate_text_tokens(required_text, user_text)
        if required_tokens > budget:
            raise ContextBudgetError(
                f"Mandatory prompt layers require about {required_tokens} tokens; model budget is {budget}."
            )

        def total_tokens(items: list[PromptLayer]) -> int:
            return estimate_text_tokens("\n\n".join(item.text for item in items), user_text)

        while total_tokens(current) > budget:
            optional = sorted(
                (layer for layer in current if not layer.required),
                key=lambda layer: (layer.priority, len(layer.text)),
                reverse=True,
            )
            if not optional:
                raise ContextBudgetError("Prompt cannot fit without removing mandatory context.")
            victim = optional[0]
            if victim.name in {"Runtime Memory", "Core Memories", "Recent Conversation Turns", "Rolling Session Summary"} and len(victim.text) > 1000:
                compacted = PromptLayer(
                    name=victim.name,
                    text=victim.text[: max(1000, len(victim.text) // 2)],
                    priority=victim.priority,
                    required=False,
                )
                current[current.index(victim)] = compacted
                actions.append(f"compact:{victim.name}")
                continue
            current.remove(victim)
            actions.append(f"drop:{victim.name}")

        system_prompt = "\n\n".join(layer.text for layer in current)
        return BuiltPrompt(
            system_prompt=system_prompt,
            user_text=user_text,
            estimated_input_tokens=estimate_text_tokens(system_prompt, user_text),
            compaction_level=len(actions),
            compaction_actions=tuple(actions),
            included_layers=tuple(layer.name for layer in current),
        )


def is_context_overflow_message(message: str) -> bool:
    lowered = str(message or "").lower()
    markers = (
        "context length",
        "context window",
        "maximum context",
        "too many tokens",
        "prompt is too long",
        "input length exceeds",
        "num_ctx",
    )
    return any(marker in lowered for marker in markers)
