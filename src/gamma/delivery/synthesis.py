import time


def synthesize_response(response, synthesizer, *, authority, grant_id, context):
    response.audio_path = None
    response.audio_content_type = None

    authority.check(grant_id, response, **context())
    response.tts_metadata['delivery_grant'] = grant_id

    if not response.speech_text:
        response.timing_ms['tts_ms'] = 0
        return response

    start = time.perf_counter()
    authority.begin_synthesis(grant_id, response, **context())
    try:
        synth_result = synthesizer.synthesize(
            text=response.speech_text, emotion=response.emotion, styles=response.voice_styles,
        )
        authority.finish_synthesis(grant_id, response, **context())
    except Exception:
        authority.cancel(grant_id)
        raise
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    response.audio_path = synth_result.audio_path
    response.audio_content_type = synth_result.content_type

    # Merge provider metadata, preserving speech_filter and delivery_state
    provider_meta = getattr(synth_result, 'metadata', None) or {}
    for key, value in provider_meta.items():
        if key not in ('speech_filter', 'delivery_state', 'delivery_grant', 'playback_approved'):
            response.tts_metadata[key] = value
    response.tts_metadata['delivery_state'] = 'synthesized'

    # Update timing
    response.timing_ms['tts_ms'] = elapsed_ms
    response.timing_ms['total_ms'] = response.timing_ms.get('total_ms', 0) + elapsed_ms

    return response
