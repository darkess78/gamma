from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

from PIL import Image, ImageDraw
import pystray

from ..config import settings
from ..supervisor.manager import ProcessManager


class TrayApp:
    def __init__(self) -> None:
        self._manager = ProcessManager()
        self._icon = pystray.Icon(
            "gamma-tray",
            icon=self._build_icon(),
            title="Gamma",
            menu=pystray.Menu(
                pystray.MenuItem("Open Dashboard", self._open_dashboard, default=True),
                pystray.MenuItem("Start Dashboard", self._start_dashboard),
                pystray.MenuItem("Start Shana", self._start_shana),
                pystray.MenuItem("Restart Shana", self._restart_shana),
                pystray.MenuItem("Stop Shana", self._stop_shana),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit Tray", self._quit_tray),
                pystray.MenuItem("Quit All", self._quit_all),
            ),
        )

    def run(self) -> None:
        self._icon.run(self._setup)

    def _setup(self, icon: pystray.Icon) -> None:
        self._refresh_title()
        threading.Thread(target=self._periodic_refresh, daemon=True).start()

    def _periodic_refresh(self) -> None:
        while True:
            self._refresh_title()
            self._icon.update_menu()
            threading.Event().wait(5)

    def _refresh_title(self) -> None:
        dashboard_running = self._manager.find_process("dashboard") is not None
        shana_running = self._manager.find_process("shana") is not None
        dashboard_state = "up" if dashboard_running else "down"
        shana_state = "up" if shana_running else "down"
        self._icon.title = f"Gamma tray | dashboard {dashboard_state} | shana {shana_state}"

    def _build_icon(self) -> Image.Image:
        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((4, 4, 60, 60), fill=(140, 47, 27, 255))
        draw.ellipse((12, 12, 52, 52), fill=(241, 230, 214, 255))
        draw.polygon([(30, 16), (42, 30), (30, 48), (20, 30)], fill=(29, 109, 99, 255))
        return image

    def _open_dashboard(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._manager.start("dashboard")
        webbrowser.open(settings.dashboard_base_url)

    def _start_dashboard(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._manager.start("dashboard")
        self._refresh_title()

    def _start_shana(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._manager.start("shana")
        self._refresh_title()

    def _restart_shana(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._manager.restart("shana")
        self._refresh_title()

    def _stop_shana(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._manager.stop("shana")
        self._refresh_title()

    def _quit_tray(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        icon.stop()

    def _quit_all(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._manager.stop("shana")
        self._manager.stop("dashboard")
        icon.stop()


def main() -> int:
    TrayApp().run()
    return 0
