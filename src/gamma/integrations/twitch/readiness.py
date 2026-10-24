class ConnectionReadiness:
    def __init__(self, username, channel):
        self.username = username.casefold()
        self.channel = channel.lstrip('#').casefold()
        self.reset()

    def reset(self):
        self.authenticated = False
        self.joined = False

    def observe(self, message):
        command = (getattr(message, 'command', '') or '').upper()
        params = getattr(message, 'params', None) or []
        prefix = getattr(message, 'prefix', None)
        if command == '001':
            if params and (params[0] or '').casefold() == self.username:
                self.authenticated = True
        elif command == 'JOIN':
            nick = prefix.split('!')[0].casefold() if prefix else None
            if nick == self.username and params and (params[0] or '').lstrip('#').casefold() == self.channel:
                self.joined = True

    @property
    def ready(self):
        return self.authenticated and self.joined
