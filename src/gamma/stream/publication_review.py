"""Safety review of stream replies before and while they are published: fast checks, the parallel LLM review and the late interrupt."""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from ..config import settings
from ..schemas.response import AssistantResponse
from .event_rules import _is_public_stream_event, _twitch_control_enabled
from .models import StreamInputEvent
from .output_events import _apply_output_target_policy, _filter_stream_output_events
from .safety_rules import _fast_stream_safety_review, _filtered_stream_response, _late_safety_override_events, _llm_review_payload, _stream_review_timeout_action
from .safety_rules import llm_review_enabled, _record_stream_floor_stop


class PublicationReviewMixin:
    """Safety review of stream replies before and while they are published: fast checks, the parallel LLM review and the late interrupt."""

    def _review_stream_output(
        self,
        event: StreamInputEvent,
        response: AssistantResponse | None,
        *,
        include_llm: bool = True,
    ) -> dict:
        if response is None or not _is_public_stream_event(event):
            return {}
        speech_filter = response.tts_metadata.get("speech_filter") if isinstance(response.tts_metadata, dict) else None
        if isinstance(speech_filter, dict) and speech_filter.get("blocked"):
            return {
                "action": "filtered",
                "blocked": True,
                "source": "conversation_speech_filter",
                "stage": "fast",
                "matched_rules": speech_filter.get("matched_rules", []),
                "layers": speech_filter.get("layers", []),
                "filter_action": speech_filter.get("action"),
                "safe_output": "filtered",
                "playback_approved": False,
            }
        output_text = response.speech_text or response.display_text or ""
        from ..safety.sensitive_terms import matches as private_terms_in
        private_hits = private_terms_in(" ".join(filter(None, {output_text, response.display_text or "", response.spoken_text or ""})))
        if private_hits:
            _record_stream_floor_stop(text=output_text, event=event, decision={
                "blocked": True, "layers": ["owner_sensitive_terms"],
                "matched_rules": [f"private_term:{h}" for h in private_hits],
                "source": "owner_sensitive_terms",
            })
            return {
                "action": "filtered",
                "blocked": True,
                "source": "owner_sensitive_terms",
                "stage": "fast",
                "matched_rules": [f"private_term:{h}" for h in private_hits],
                "safe_output": "filtered",
                "playback_approved": False,
            }
        from ..safety.parroting import parroted
        viewer_text = str(event.metadata.get("raw_text") or event.text or "")
        if event.actor.source.strip().lower() not in {"local_monitor", "local"} and parroted(output_text, viewer_text):
            return {
                "action": "filtered",
                "blocked": True,
                "source": "chat_parroting",
                "stage": "fast",
                "matched_rules": ["reply_repeats_viewer_words"],
                "safe_output": "filtered",
                "playback_approved": False,
            }
        fast_review = _fast_stream_safety_review(output_text)
        if fast_review["blocked"]:
            _record_stream_floor_stop(text=output_text, event=event, decision={
                **fast_review, "source": "stream_output_gate_fast",
            })
            return {
                "action": "filtered",
                "blocked": True,
                "source": "stream_output_gate_fast",
                "stage": "fast",
                "matched_rules": fast_review["matched_rules"],
                "layers": fast_review["layers"],
                "filter_action": fast_review["filter_action"],
                "safe_output": "filtered",
                "playback_approved": False,
            }
        llm_decision = self._review_stream_output_with_llm(event, output_text) if include_llm and output_text else None
        if llm_decision is not None:
            return llm_decision
        return {
            "action": "allow",
            "blocked": False,
            "source": "stream_output_gate_fast",
            "stage": "fast",
            "matched_rules": fast_review["matched_rules"],
            "layers": fast_review["layers"],
            "filter_action": fast_review["filter_action"],
            "playback_approved": True,
        }

    def _synthesize_with_parallel_review(
        self,
        event: StreamInputEvent,
        response: AssistantResponse,
    ) -> tuple[AssistantResponse, dict, tuple[Future, ThreadPoolExecutor] | None]:
        reviewed_text = response.speech_text or ""
        timeout_seconds = max(0.05, float(settings.stream_safety_review_timeout_seconds or 2.0))
        started_at = time.perf_counter()
        executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="stream-safety")
        review_future = None
        tts_future = None
        try:
            review_future = executor.submit(self._safety_reviewer.review, response.speech_text or "")
            tts_future = executor.submit(
                self._stream_speech_synthesizer().synthesize,
                response.speech_text or "",
                response.emotion,
                response.voice_styles,
            )
            try:
                review = review_future.result(timeout=max(0.0, timeout_seconds - (time.perf_counter() - started_at)))
            except FutureTimeoutError:
                return response, {
                    "action": _stream_review_timeout_action(),
                    "blocked": False,
                    "source": "stream_output_gate_llm",
                    "stage": "llm_parallel",
                    "review_timeout": True,
                    "timeout_seconds": timeout_seconds,
                    "playback_approved": False,
                }, None
            except Exception as exc:
                return response, {
                    "action": "skip",
                    "blocked": False,
                    "source": "stream_output_gate_llm",
                    "stage": "llm_parallel",
                    "review_error": type(exc).__name__,
                    "playback_approved": False,
                }, None
            decision = _llm_review_payload(review)
            _record_stream_floor_stop(text=reviewed_text, event=event, decision=decision)
            if decision["blocked"]:
                return _filtered_stream_response(), decision, None
            tts_result = tts_future.result()
            response.audio_path = tts_result.audio_path
            response.audio_content_type = tts_result.content_type
            response.tts_metadata = {
                **dict(response.tts_metadata or {}),
                **dict(tts_result.metadata or {}),
            }
            return response, decision, None
        finally:
            for future in (review_future, tts_future):
                if future is not None:
                    future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)

    def _arm_late_safety_interrupt(
        self,
        *,
        event: StreamInputEvent,
        turn_id: str,
        review_future: Future,
        executor: ThreadPoolExecutor,
        reviewed_text: str = "",
    ) -> None:
        def finish_review() -> None:
            try:
                review = review_future.result(timeout=max(0.05, float(settings.stream_safety_review_timeout_seconds or 2.0)))
            except FutureTimeoutError:
                review_future.cancel()
                return
            except Exception:
                return
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            decision = _llm_review_payload(review)
            _record_stream_floor_stop(text=reviewed_text, event=event, decision=decision)
            if not decision["blocked"]:
                return
            filtered = _filtered_stream_response()
            events = _late_safety_override_events(
                event=event,
                turn_id=turn_id,
                response=filtered,
                safety_decision=decision,
            )
            events = _apply_output_target_policy(event, events)
            events = _filter_stream_output_events(event, events)
            if events:
                self._output_dispatcher.dispatch(events)

        threading.Thread(target=finish_review, name=f"stream-safety-{turn_id[:8]}", daemon=True).start()

    def _review_stream_output_with_llm(self, event: StreamInputEvent, text: str) -> dict | None:
        if not llm_review_enabled():
            return None
        if not settings.speech_filter_llm_enabled:
            return None
        timeout_seconds = max(0.05, float(settings.stream_safety_review_timeout_seconds or 2.0))
        if event.actor.source == "discord":
            timeout_seconds = max(timeout_seconds, 20.0)
        started_at = time.perf_counter()
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self._safety_reviewer.review, text)
        try:
            review = future.result(timeout=timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            return {
                "action": _stream_review_timeout_action(),
                "blocked": False,
                "source": "stream_output_gate_llm",
                "stage": "llm",
                "review_timeout": True,
                "timeout_seconds": timeout_seconds,
                "review_ms": round((time.perf_counter() - started_at) * 1000, 1),
                "playback_approved": False,
            }
        except Exception as exc:
            executor.shutdown(wait=False, cancel_futures=True)
            return {
                "action": "skip",
                "blocked": False,
                "source": "stream_output_gate_llm",
                "stage": "llm",
                "review_error": str(exc),
                "review_ms": round((time.perf_counter() - started_at) * 1000, 1),
                "playback_approved": False,
            }
        finally:
            if future.done():
                executor.shutdown(wait=False, cancel_futures=True)
        _record_stream_floor_stop(text=text, event=event, decision=_llm_review_payload(review))
        review_ms = round((time.perf_counter() - started_at) * 1000, 1)
        if review.action != "allow":
            return {
                "action": "filtered",
                "blocked": True,
                "source": "stream_output_gate_llm",
                "stage": "llm",
                "matched_rules": [review.reason] if review.reason else [],
                "layers": ["llm"],
                "filter_action": review.action,
                "review_ms": review_ms,
                "safe_output": "filtered",
                "playback_approved": False,
            }
        return {
            "action": "allow",
            "blocked": False,
            "source": "stream_output_gate_llm",
            "stage": "llm",
            "matched_rules": [review.reason] if review.reason else [],
            "layers": ["llm"],
            "filter_action": review.action,
            "review_ms": review_ms,
            "playback_approved": True,
        }
