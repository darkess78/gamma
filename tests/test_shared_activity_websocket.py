"""Optional observer receives only authenticated, parsed and accepted messages."""
import pytest

from gamma.integrations.minecraft.coordinator import ReceiveDisposition
from gamma.integrations.minecraft.websocket import create_minecraft_control_router
from test_minecraft_websocket import (FakeRouteWebSocket, NOW, RecordingCoordinator,
                                       TOKEN, hello_model, run_router_endpoint, text_event)


def test_observer_sees_accepted_result_after_receive():
    coordinator = RecordingCoordinator(clock=lambda: NOW)
    calls = []
    def observed(message, result):
        assert coordinator.received[-1] is message
        assert result.disposition == ReceiveDisposition.ACCEPTED
        calls.append(message.type)
    router = create_minecraft_control_router(coordinator=coordinator, control_token=TOKEN,
                                            received_observer=observed)
    socket = FakeRouteWebSocket(authorization='Bearer ' + TOKEN,
                               events=[text_event(hello_model().model_dump(mode='json'))])
    run_router_endpoint(router, socket)
    assert calls == ['hello'] and coordinator.detach_count == 1


@pytest.mark.parametrize('authorization,payload', [('Bearer wrong', hello_model().model_dump(mode='json')),
                                                  ('Bearer ' + TOKEN, {'execute': 'bad'})])
def test_observer_never_receives_unauthenticated_or_unparsed_messages(authorization, payload):
    calls = []
    router = create_minecraft_control_router(coordinator=RecordingCoordinator(clock=lambda: NOW),
                                            control_token=TOKEN, received_observer=lambda *args: calls.append(args))
    socket = FakeRouteWebSocket(authorization=authorization, events=[text_event(payload)])
    run_router_endpoint(router, socket)
    assert calls == []


def test_observer_failure_closes_and_detaches_control():
    coordinator = RecordingCoordinator(clock=lambda: NOW)
    def failed(*_):
        raise ValueError('private details must stay private')
    router = create_minecraft_control_router(coordinator=coordinator, control_token=TOKEN,
                                            received_observer=failed)
    socket = FakeRouteWebSocket(authorization='Bearer ' + TOKEN,
                               events=[text_event(hello_model().model_dump(mode='json'))])
    run_router_endpoint(router, socket)
    assert socket.closes[-1] == (1011, 'internal control failure')
    assert coordinator.detach_count == 1 and not coordinator.status().transport_attached


def test_invalid_observer_is_rejected_when_router_constructed():
    with pytest.raises(TypeError, match='received_observer'):
        create_minecraft_control_router(coordinator=RecordingCoordinator(), control_token=TOKEN,
                                        received_observer='not_callable')
