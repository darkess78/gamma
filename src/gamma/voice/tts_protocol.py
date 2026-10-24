def validate_synthesis(request):
    if not isinstance(request, dict):
        raise ValueError
    allowed = {"schema_version", "request_id", "type", "text", "emotion", "styles", "job_id"}
    if not set(request.keys()) <= allowed:
        raise ValueError
    text = request.get("text")
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 16384:
        raise ValueError
    emotion = request.get("emotion")
    if emotion is not None and (not isinstance(emotion, str) or len(emotion.encode("utf-8")) > 128):
        raise ValueError
    styles = request.get("styles")
    if styles is not None:
        if not isinstance(styles, list) or len(styles) > 16:
            raise ValueError
        for s in styles:
            if not isinstance(s, str) or not s or len(s.encode("utf-8")) > 128:
                raise ValueError
    result = {"text": text, "emotion": emotion, "styles": styles}
    if request.get("job_id") is not None:
        import re
        if not isinstance(request["job_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", request["job_id"]):
            raise ValueError
        result["job_id"] = request["job_id"]
    return result
