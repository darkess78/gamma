"""Private version-1 brain protocol. This process has no commit/delivery API."""
import contextlib
import os
import sys
from types import SimpleNamespace

from ..config import settings
from ..errors import ConversationError, ConfigurationError, ExternalServiceError
from ..conversation.service import ConversationService
from ..conversation.turn_contract import StructuredTurnParser
from ..identity.profile import SpeakerProfile
from ..persona.emotion_models import AssistantEmotionState, EmotionalEpisode, EmotionalPattern
from ..persona.loader import build_system_prompt
from ..safety.speech_filter import SpeechFilterResult
from ..schemas.response import ToolExecutionResult, VisionAnalysis
from ..supervisor.lifecycle_lock import lifecycle_lock
from .image_payloads import load_images
from .wire import encode, decode, MAX_FRAME


class RemoteMemory:
    def __init__(self, call):
        self.call = call

    def get_profile_facts(self, **fields):
        return [SimpleNamespace(**item) for item in self.call("profile", fields)]

    def search_memories(self, user_text, **fields):
        return [SimpleNamespace(**item) for item in self.call("memories", fields)]


class Computation(ConversationService):
    def __init__(self, call):
        # Deliberately do not initialize ConversationService's authority stores.
        self._brain_worker = False
        self._llm = None
        self._call = call
        self._memory = RemoteMemory(call)
        self._continuity = SimpleNamespace(prompt_context=lambda *a, **k: call("continuity", {}))
        self._tools = SimpleNamespace(tool_summaries=lambda: call("tool_summaries", {}))
        self._speech_filter = SimpleNamespace(apply=lambda text, **kw:
            SpeechFilterResult(**call("safety", {"text": text, **kw})))
        self._turn_parser = StructuredTurnParser()

    def _build_system_prompt(self, **fields):
        snapshot = self._call("persona", {})
        context = snapshot["emotion_context"]
        context["state"] = AssistantEmotionState(**context["state"])
        context["episodes"] = [EmotionalEpisode(**item) for item in context["episodes"]]
        context["patterns"] = [EmotionalPattern(**item) for item in context["patterns"]]
        return build_system_prompt(**fields, **snapshot)

    def _execute_tool_calls(self, calls, **kwargs):
        return [ToolExecutionResult.model_validate(value) for value in self._call("tools", {
            "calls": [call.model_dump(mode="json") for call in calls]})]

    def _append_timing_log(self, **fields):
        fields["response"] = fields["response"].model_dump(mode="json")
        self._call("timing", fields)


def serve(input_stream, output_stream):
    generation = os.environ["GAMMA_BRAIN_GENERATION"]
    current = None

    def emit(kind, **fields):
        output_stream.write(encode(dict(schema_version=1, generation=generation,
            request_id=current, type=kind, **fields)))
        output_stream.flush()

    def receive():
        raw = input_stream.readline(MAX_FRAME + 1)
        if not raw:
            raise EOFError()
        request = decode(raw)
        if request["generation"] != generation:
            raise ValueError("obsolete generation")
        return request

    def call(operation, args):
        emit("callback", operation=operation, args=args)
        reply = receive()
        if reply["type"] != "callback_result" or reply["request_id"] != current:
            raise ValueError("invalid authority response")
        return reply["value"]

    computation = Computation(call)
    # Provider selection and shared admission retain the configured runtime.
    with lifecycle_lock(settings.data_dir / "runtime/brain-owner.lock", timeout=0):
        emit("ready", pid=os.getpid(), model_loaded=False)
        while True:
            try:
                request = receive()
            except EOFError:
                return
            if request["type"] != "execute":
                raise ValueError("expected execute")
            current = request["request_id"]
            try:
                operation, fields = request["operation"], request["fields"]
                if operation == "stream_decide":
                    from ..stream.brain import StreamBrain
                    from ..stream.models import StreamInputEvent
                    brain = StreamBrain.__new__(StreamBrain)
                    brain._conversation = computation
                    value = brain.decide(StreamInputEvent.model_validate(fields["event"]))
                elif operation in {"respond", "wake", "vision"}:
                    fields = dict(fields)
                    fields["speaker"] = SpeakerProfile(**fields["speaker"]) if fields.get("speaker") else None
                    images = load_images(fields.pop("images", []))
                    if operation == "respond":
                        from ..vision.service import VisionImage
                        fields["image"] = VisionImage(images[0].data, images[0].media_type) if images else None
                        if fields.get("vision_analysis") is not None:
                            fields["vision_analysis"] = VisionAnalysis.model_validate(fields["vision_analysis"])
                        value = computation._respond(**fields)
                    elif operation == "vision":
                        from ..vision.service import VisionImage, VisionService
                        value = VisionService().analyze_image(llm_adapter=computation._llm_adapter(),
                            image=VisionImage(images[0].data, images[0].media_type),
                            user_text=fields["user_text"], mode=fields.get("mode"))
                    else:
                        fields.pop("state_proposals", None)
                        value = computation._generate_presence_wake(**fields)
                else:
                    raise ValueError("unsupported operation")
                emit("result", value=value.model_dump(mode="json"))
            except ConversationError as exc:
                code = "vision_unsupported" if "does not support image analysis" in str(exc) else "conversation"
                emit("error", error_code=code)
            except ConfigurationError:
                emit("error", error_code="configuration")
            except ExternalServiceError:
                emit("error", error_code="provider")
            except Exception:
                # No provider exception, prompt or private state enters diagnostics.
                emit("error", reason="computation_failed")


def main():
    output = sys.stdout.buffer
    with contextlib.redirect_stdout(sys.stderr):
        try:
            serve(sys.stdin.buffer, output)
        finally:
            from .model_runtime import close_model_worker
            close_model_worker()


if __name__ == "__main__":
    main()
