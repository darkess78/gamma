"""Explicit Gamma release profile; observations never select inference routes."""
_PROFILE = {'name': 'gamma_warm_local_v1', 'review_model': 'gemma4:e4b'}


def release_inference_options(profile):
    if profile is None:
        return {}
    if type(profile) is not dict or profile != _PROFILE:
        raise ValueError('unknown_private_inference_profile')
    return {'primary_keep_alive': '30m', 'primary_temperature': .2,
            'primary_prefix_cache': True,
            'review_endpoint': 'http://127.0.0.1:11435',
            'review_keep_alive': '30m', 'review_num_gpu': 32,
            'review_num_threads': 8}


def validate_inference_runtime(profile, settings):
    options = release_inference_options(profile)
    if options:
        model = ((settings.speech_filter_llm_model or '').strip()
                 or (settings.local_llm_tagging_model or '').strip()
                 or (settings.local_llm_light_model or '').strip())
        if (not settings.speech_filter_llm_enabled or model != profile['review_model']
                or settings.local_llm_endpoint.rstrip('/') != 'http://127.0.0.1:11434'):
            raise ValueError('private_inference_runtime_binding_changed')
    return options


def warm_release_inference(profile, settings, transcribe=None, *, maximum_seconds=30, private_prefix=None):
    """Bounded startup preparation; creates no Core turn or delivered speech."""
    import io
    import json
    import time
    import wave
    from urllib import request
    from ..llm.backend_admission import backend_slot
    from ..llm.local_metrics import local_generation_metrics

    import math
    if type(maximum_seconds) not in {int, float} or not math.isfinite(maximum_seconds) or not 0 < maximum_seconds <= 30:
        raise ValueError('invalid_startup_warmup_budget')
    options = validate_inference_runtime(profile, settings)
    if not options:
        return {'status': 'not_requested'}
    report = {'status': 'ready', 'models': {}}
    deadline = time.monotonic() + maximum_seconds
    targets = [('primary', settings.local_llm_endpoint, settings.local_llm_model,
                {'num_ctx': 8192}, options['primary_keep_alive']),
               ('review', options['review_endpoint'], profile['review_model'],
                {'num_ctx': 8192, 'num_gpu': options['review_num_gpu'],
                 'num_thread': options['review_num_threads']}, options['review_keep_alive'])]
    for role, endpoint, model, model_options, keep_alive in targets:
        started = time.monotonic()
        try:
            remaining = deadline - started
            if remaining <= 0:
                raise TimeoutError('startup_warmup_deadline')
            payload = {'model': model, 'prompt': '', 'stream': False,
                       'keep_alive': keep_alive, 'options': model_options}
            req = request.Request(endpoint.rstrip('/') + '/api/generate',
                data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
            with backend_slot(endpoint, foreground=False, timeout=min(10, remaining)):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('startup_warmup_deadline')
                with request.urlopen(req, timeout=min(10, remaining)) as response:
                    result = json.loads(response.read())
            if result.get('done') is not True or result.get('model') != model:
                raise ValueError('startup_model_not_ready')
            report['models'][role] = {'status': 'ready', **local_generation_metrics(result)}
        except Exception as exc:
            report['status'] = 'degraded'
            report['models'][role] = {'status': 'unavailable', 'error_type': type(exc).__name__}
        report['models'][role]['warmup_ms'] = round((time.monotonic() - started) * 1000, 3)
    if private_prefix is not None and options.get('primary_prefix_cache') is True:
        from hashlib import sha256
        from ..errors import GammaError
        from ..llm.local_adapter import prepare_private_prefix
        started = time.monotonic()
        prefix_report = {'status': 'unavailable'}
        try:
            if type(private_prefix) is not str:
                raise ValueError('invalid_private_startup_prefix')
            remaining = deadline - started
            if remaining <= 0:
                raise TimeoutError('startup_warmup_deadline')
            with backend_slot(settings.local_llm_endpoint, foreground=False, timeout=min(10, remaining)):
                metrics = prepare_private_prefix('<|im_start|>system\n' + private_prefix,
                    endpoint=settings.local_llm_endpoint.rstrip('/'),
                    timeout_seconds=max(0, deadline - time.monotonic()), deadline=deadline)
            prefix_report = {'status': 'ready' if metrics.get('prefix_prime_succeeded') == 1 else 'unavailable',
                             'prefix_sha256': sha256(private_prefix.encode()).hexdigest(), **metrics}
        except GammaError:
            raise
        except Exception as exc:
            prefix_report['error_type'] = type(exc).__name__
        prefix_report['warmup_ms'] = round((time.monotonic() - started) * 1000, 3)
        # Cache preparation is optional. Ordinary checked drafts remain available
        # when its metadata gate or bounded provider request cannot complete.
        report['prefix_cache'] = prefix_report
    if transcribe is not None:
        import threading
        started = time.monotonic()
        raw = io.BytesIO()
        with wave.open(raw, 'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
            stream.writeframes(bytes(8000))
        complete, errors = threading.Event(), []

        def initialize_stt():
            try:
                # No Core input, transcript retention, or unsolicited speech.
                transcribe(raw.getvalue())
            except Exception as exc:
                errors.append(type(exc).__name__)
            finally:
                complete.set()

        remaining = deadline - time.monotonic()
        if remaining > 0:
            threading.Thread(target=initialize_stt, daemon=True, name='private-stt-warmup').start()
            ready = complete.wait(timeout=min(10, remaining))
            report['stt'] = {'status': 'unavailable' if errors else 'ready' if ready else 'warming'}
            if errors:
                report['stt']['error_type'] = errors[0]
            if not ready or errors:
                report['status'] = 'degraded'
        else:
            report['stt'] = {'status': 'not_started', 'reason': 'warmup_deadline'}
            report['status'] = 'degraded'
        report['stt']['warmup_ms'] = round((time.monotonic() - started) * 1000, 3)
    return report
