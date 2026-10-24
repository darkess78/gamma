"""Real owned commits for delivery tests that do not exercise conversation inference."""
from uuid import uuid4


def commit_response(authority, response, *, session_id=None):
    state = authority._state
    exchange = uuid4().hex
    session_id = session_id or uuid4().hex
    with state.transaction() as session:
        state.begin(session, exchange, session_id, "local_private")
        state.complete(session, exchange, session_id, "local_private",
                       {"display_text": response.display_text, "output_target": "dashboard_monitor"})
        session.commit()
    response.route_trace_id = exchange
    return response
