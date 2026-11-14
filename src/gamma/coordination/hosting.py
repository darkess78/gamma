"""Bounded loopback hosting for explicit isolated review sessions."""
from contextlib import contextmanager
import socket
import os
import threading
import time


@contextmanager
def loopback_server(app, port=0, *, startup_timeout=20, shutdown_timeout=150):
    import uvicorn
    listener = socket.socket()
    try:
        if os.name == 'posix':
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', port))
        runner = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
        thread = threading.Thread(target=runner.run, kwargs={'sockets': [listener]}, daemon=True,
                                  name=f'coordination-http-{listener.getsockname()[1]}')
        thread.start()
        try:
            deadline = time.monotonic() + startup_timeout
            while not runner.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.02)
            if not runner.started:
                raise RuntimeError('isolated server failed to start')
            yield f'http://127.0.0.1:{listener.getsockname()[1]}'
        finally:
            runner.should_exit = True
            thread.join(timeout=shutdown_timeout)
            if thread.is_alive():
                raise RuntimeError('isolated server did not finish bounded work')
    finally:
        listener.close()
