"""EventSub readiness state machine."""


class EventSubReadiness:
    """Tracks EventSub connection readiness as a simple state string."""

    def __init__(self) -> None:
        self.revoked = False

    def observe(self, status, *, connected, subscriptions_ok, subscriptions_failed):
        if status == "connecting":
            self.revoked = False
            return "starting"
        if status == "revoked":
            self.revoked = True
            return "degraded"
        if status in ("configuration_error", "subscription_error", "token_validation_error"):
            return "failed"
        if status == "stopped":
            return "stopped"
        if status in ("reconnecting", "reconnect_requested"):
            return "degraded"
        if status == "connected" and connected:
            if subscriptions_ok > 0 and subscriptions_failed == 0 and not self.revoked:
                return "ready"
            return "degraded"
        if not connected:
            return "starting"
        return "degraded"
