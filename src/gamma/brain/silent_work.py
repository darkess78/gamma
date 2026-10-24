"""Choose bounded internal work from owner-approved goals and disposable input."""
from .observations import ObservationInbox
from ..stream.self_goals import StreamSelfGoalStore


class SilentWork:
    def __init__(self, *, goals=None, inbox=None):
        self.goals = goals if goals is not None else StreamSelfGoalStore()
        self.inbox = inbox if inbox is not None else ObservationInbox()
        self._completed = set()

    def reset(self):
        self._completed.clear()

    def observe(self, **observation):
        goal = self.goals.get(observation.get("goal_id"))
        if goal is None or goal.status != "approved":
            raise ValueError("observation requires an approved goal")
        return self.inbox.offer(**observation)

    def select(self):
        goals = self.goals.list_goals(status="approved", limit=32)["items"]
        fallback = None
        for goal in goals:
            # Only an explicit, bounded goal description crosses into cognition.
            bounded = {key: goal[key] for key in ("id", "title", "description", "updated_at")}
            observations = self.inbox.take(goal["id"])
            if observations:
                return {"goal": bounded, "observations": observations}
            if fallback is None and (goal["id"], goal["updated_at"]) not in self._completed:
                fallback = {"goal": bounded, "observations": []}
        return fallback

    def complete(self, selected):
        goal = selected["goal"]
        # At most eight successful cycles per explicit enable operation.
        self._completed.add((goal["id"], goal["updated_at"]))
