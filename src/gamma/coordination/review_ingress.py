"""Opt-in path ingress for the existing private dashboard; no backend proxy."""
import json
import ipaddress
import re
from http.cookies import SimpleCookie, CookieError
from urllib.parse import urlsplit, urlunsplit

from starlette.responses import PlainTextResponse


INNER_COOKIE = 'gamma_dashboard_session'
MAX_REWRITE_BYTES = 2_000_000
_PREFIX = re.compile(r'^/review/[a-z][a-z0-9-]{0,40}$')
_OWNED = re.compile(r'^/(?:api|static|dashboard|login|logout|monitor|overlay)(?:/|[?#]|$)')
_UNSAFE = re.compile(r'%2[ef]|%5c', re.IGNORECASE)
_JSON_URL_FIELDS = {
    '/api/coordination/interaction/poll': (('output', 'audio_url'),),
    '/api/coordination/interaction/input': (('output', 'audio_url'),),
    '/api/coordination/channel-audio/render': (('audio_url',),),
}
_STORAGE_KEYS = {
    '/static/phone.js': ('shana-phone-device-v1', 'shana-phone-pending-v1'),
    '/static/private_presence.js': ('shana-presence-placement',),
    '/static/monitor.js': ('gammaMonitorTheme', 'gammaMonitor.statusPanel'),
}


def _safe_path(path):
    return (path.startswith('/') and not path.startswith('//') and '\\' not in path
            and not any(ord(c) < 32 or ord(c) == 127 for c in path)
            and not _UNSAFE.search(path) and not any(p in {'.', '..'} for p in path.split('/')))


def prefix_url(value, prefix):
    """Translate a local owned path only, preserving its query and fragment."""
    if not isinstance(value, str):
        return value
    if not value.startswith('/') or value.startswith('//'):
        return value
    path = urlsplit(value).path
    if not _safe_path(path) or not (value == '/' or _OWNED.match(value)):
        return value
    return prefix + value


def rewrite_literals(text, prefix):
    """Only code-owned HTML/JS/CSS root string starts; never API text."""
    quoted = re.compile(r'''(["'`])(/[^\s"'`<>]*)''')
    text = quoted.sub(lambda m: m[1] + prefix_url(m[2], prefix), text)
    return re.sub(r'(?i)(url\(\s*)(/[^\s)"\']*)',
                  lambda m: m[1] + prefix_url(m[2], prefix), text)


class _InvalidResponse(ValueError):
    pass


class ReviewIngress:
    """ASGI wrapper. Construct only for an explicitly opted-in candidate app."""

    def __init__(self, app, prefix):
        if not isinstance(prefix, str) or not _PREFIX.fullmatch(prefix):
            raise ValueError('review prefix must be /review/<lowercase-name>')
        self.app, self.prefix = app, prefix
        self.cookie_name = 'gamma_review_' + prefix.rsplit('/', 1)[1] + '_session'

    def __getattr__(self, name):
        return getattr(self.app, name)

    def _cookies(self, headers):
        values = []
        for key, value in headers:
            if key.lower() == b'cookie':
                for item in value.decode('latin-1').split(';'):
                    if item.strip().split('=', 1)[0] == self.cookie_name:
                        values.append(item.strip())
        if len(values) > 1:
            raise ValueError('ambiguous review cookie')
        filtered = [(k, v) for k, v in headers if k.lower() not in {b'cookie', b'accept-encoding'}]
        filtered.append((b'accept-encoding', b'identity'))
        if values:
            try:
                cookie = SimpleCookie(values[0])
                if set(cookie) != {self.cookie_name}:
                    raise ValueError('invalid review cookie')
                value = cookie[self.cookie_name].coded_value
            except CookieError as exc:
                raise ValueError('invalid review cookie') from exc
            filtered.append((b'cookie', (INNER_COOKIE + '=' + value).encode('latin-1')))
        return filtered

    def _set_cookie(self, value):
        try:
            cookie = SimpleCookie(value.decode('latin-1'))
        except CookieError as exc:
            raise _InvalidResponse('invalid dashboard cookie') from exc
        if set(cookie) != {INNER_COOKIE}:
            raise _InvalidResponse('unsupported dashboard cookie')
        original = cookie[INNER_COOKIE]
        scoped = SimpleCookie()
        scoped[self.cookie_name] = original.value
        for attr in original.keys():
            if original[attr] and attr not in {'path', 'domain', 'secure'}:
                scoped[self.cookie_name][attr] = original[attr]
        scoped[self.cookie_name]['path'] = self.prefix
        scoped[self.cookie_name]['secure'] = True
        return scoped.output(header='').strip().encode('latin-1')

    def _location(self, value, scope):
        raw = value.decode('latin-1')
        url = urlsplit(raw)
        if url.scheme or url.netloc:
            host = next((v.decode('latin-1') for k, v in scope['headers'] if k.lower() == b'host'), '')
            if url.scheme != scope.get('scheme') or url.netloc != host:
                raise _InvalidResponse('dashboard redirect escaped review origin')
            raw = urlunsplit(('', '', url.path, url.query, url.fragment))
        prefixed = prefix_url(raw, self.prefix)
        if prefixed == raw:
            raise _InvalidResponse('dashboard redirect escaped review path')
        return prefixed.encode('latin-1')

    def _headers(self, headers, scope):
        result = []
        for key, value in headers:
            name = key.lower()
            if name == b'set-cookie':
                value = self._set_cookie(value)
            elif name == b'location':
                value = self._location(value, scope)
            elif name == b'service-worker-allowed':
                original = value.decode('latin-1')
                value = prefix_url(original, self.prefix).encode('latin-1')
                if value.decode('latin-1') == original:
                    raise _InvalidResponse('unsupported service worker scope')
            result.append((key, value))
        return result

    def _connect_policy(self, scope):
        """A second CSP only intersects with any inner policy; no self escape."""
        scheme = scope.get('scheme')
        hosts = [v for k, v in scope['headers'] if k.lower() == b'host']
        if scheme not in {'http', 'https'} or len(hosts) != 1:
            raise _InvalidResponse('invalid review origin')
        try:
            host = hosts[0].decode('ascii')
            if not host or re.search(r'[\s\\/%?#@,;<>]', host):
                raise ValueError('invalid host')
            url = urlsplit(scheme + '://' + host)
            hostname, port = url.hostname, url.port
            if not hostname or url.username or url.password or url.path or port is not None and not 1 <= port <= 65535:
                raise ValueError('invalid host')
            try:
                ipaddress.ip_address(hostname)
            except ValueError:
                if len(hostname) > 253 or not all(re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', part)
                    for part in hostname.split('.')):
                    raise ValueError('invalid host')
        except (UnicodeError, ValueError) as exc:
            raise _InvalidResponse('invalid review origin') from exc
        return ('connect-src ' + scheme + '://' + host + self.prefix + '/').encode('ascii')

    def _rewrite(self, body, kind, path):
        try:
            if kind == 'code':
                text = rewrite_literals(body.decode('utf-8'), self.prefix)
                # These exact literals are owned asset constants, never user data.
                namespace = 'shana-review-' + self.prefix.rsplit('/', 1)[1] + '-'
                for key in _STORAGE_KEYS.get(path, ()):
                    for quote in ('"', "'", '`'):
                        text = text.replace(quote + key + quote, quote + namespace + key + quote)
                return text.encode('utf-8')
            fields = _JSON_URL_FIELDS[path]
            value = json.loads(body)
            changed = False
            for location in fields:
                item = value
                for key in location[:-1]:
                    item = item.get(key) if isinstance(item, dict) else None
                key = location[-1]
                if isinstance(item, dict) and isinstance(item.get(key), str):
                    entry = item[key]
                    new = prefix_url(entry, self.prefix)
                    if new != entry:
                        item[key], changed = new, True
            return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8') if changed else body
        except (UnicodeError, ValueError) as exc:
            raise _InvalidResponse('invalid rewrite source') from exc

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            return await self.app(scope, receive, send)
        if scope['type'] == 'websocket':
            return await send({'type': 'websocket.close', 'code': 1008})
        if scope['type'] != 'http':
            return
        outer_path = scope.get('path', '')
        if outer_path != self.prefix and not outer_path.startswith(self.prefix + '/'):
            return await PlainTextResponse('Not found', status_code=404)(scope, receive, send)
        path = outer_path[len(self.prefix):] or '/'
        raw_path = scope.get('raw_path', outer_path.encode('utf-8')).decode('latin-1')
        if not _safe_path(raw_path) or not _safe_path(path) or not (path == '/' or _OWNED.match(path)):
            return await PlainTextResponse('Not found', status_code=404)(scope, receive, send)
        inner = dict(scope)
        inner.update(path=path, raw_path=path.encode('utf-8'), root_path='')
        inner['extensions'] = {k: v for k, v in scope.get('extensions', {}).items() if k != 'http.response.pathsend'}
        try:
            inner['headers'] = self._cookies(scope['headers'])
        except ValueError:
            return await PlainTextResponse('Invalid review cookie', status_code=400)(scope, receive, send)
        # A transformed asset must not reuse an unprefixed byte-range or validator.
        # Binary routes retain the incoming request contract, including ranges.
        might_rewrite = (path in _JSON_URL_FIELDS or
            not path.startswith(('/api/', '/static/')) or
            path.startswith('/static/') and path.endswith(('.js', '.css', '.html')))
        if might_rewrite:
            inner['headers'] = [(k, v) for k, v in inner['headers']
                                if k.lower() not in {b'range', b'if-range', b'if-none-match', b'if-modified-since'}]
        start, kind, chunks, size, started = None, None, [], 0, False

        async def translated(message):
            nonlocal start, kind, size, started
            if message['type'] == 'http.response.start':
                start = dict(message)
                start['headers'] = self._headers(message.get('headers', []), scope)
                headers = {k.lower(): v for k, v in start['headers']}
                mime = headers.get(b'content-type', b'').split(b';', 1)[0].strip().lower()
                page = path == '/' or not path.startswith('/api/')
                if ((page and mime == b'text/html') or
                    (path.startswith('/static/') or path == '/dashboard/phone-worker.js') and mime in {
                        b'text/javascript', b'application/javascript', b'text/css'}):
                    kind = 'code'
                elif path in _JSON_URL_FIELDS and mime == b'application/json':
                    kind = 'json'
                if kind:
                    if mime == b'text/html':
                        start['headers'].append((b'content-security-policy', self._connect_policy(scope)))
                    if headers.get(b'content-encoding', b'identity').lower() != b'identity':
                        raise _InvalidResponse('compressed rewrite source is unsupported')
                    try:
                        if int(headers.get(b'content-length', b'0')) > MAX_REWRITE_BYTES:
                            raise _InvalidResponse('rewrite source exceeds limit')
                    except ValueError as exc:
                        raise _InvalidResponse('invalid rewrite size') from exc
                else:
                    started = True
                    await send(start)
            elif message['type'] == 'http.response.body' and kind:
                chunk = message.get('body', b'')
                size += len(chunk)
                if size > MAX_REWRITE_BYTES:
                    raise _InvalidResponse('rewrite source exceeds limit')
                chunks.append(chunk)
                if not message.get('more_body'):
                    body = self._rewrite(b''.join(chunks), kind, path) if scope.get('method') != 'HEAD' else b''
                    start['headers'] = [(k, v) for k, v in start['headers'] if k.lower() not in {
                        b'content-length', b'etag', b'content-md5', b'last-modified', b'cache-control'}]
                    if scope.get('method') != 'HEAD':
                        start['headers'].append((b'content-length', str(len(body)).encode('ascii')))
                    start['headers'].append((b'cache-control', b'no-store'))
                    started = True
                    await send(start)
                    await send({'type': 'http.response.body', 'body': body})
            else:
                await send(message)
        try:
            await self.app(inner, receive, translated)
        except _InvalidResponse:
            if started:
                raise
            await PlainTextResponse('Unsupported review response', status_code=502)(scope, receive, send)
