from urllib.parse import urlsplit


def validate_route(provider, endpoint, allowed_endpoints):
    if type(provider) is not str or provider not in ("mock", "local", "ollama"):
        raise ValueError("invalid route")
    normalized = "local" if provider == "ollama" else provider
    if normalized == "mock" and endpoint is not None:
        raise ValueError("invalid route")
    if endpoint is not None:
        if type(endpoint) is not str or len(endpoint) > 2048:
            raise ValueError("invalid route")
        if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in endpoint):
            raise ValueError("invalid route")
        try:
            parts = urlsplit(endpoint)
            port = parts.port
        except ValueError:
            raise ValueError("invalid route") from None
        if parts.scheme not in ("http", "https"):
            raise ValueError("invalid route")
        if not parts.hostname or not parts.netloc:
            raise ValueError("invalid route")
        if parts.username is not None or parts.password is not None:
            raise ValueError("invalid route")
        if parts.query or parts.fragment:
            raise ValueError("invalid route")
        if port is not None and not (1 <= port <= 65535):
            raise ValueError("invalid route")
        stripped = endpoint.rstrip("/")
        if stripped not in {a.rstrip("/") for a in allowed_endpoints}:
            raise ValueError("invalid route")
    return (normalized, endpoint)
