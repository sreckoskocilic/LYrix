"""macOS menu bar launcher for Lyrics apps.

Requires: pip install rumps
"""

import subprocess
import sys
from pathlib import Path

try:
    import rumps
except ImportError:
    print("rumps is required: pip install rumps", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).parent


class LyricsMenuBar(rumps.App):
    def __init__(self):
        super().__init__(name="Lyrics", title="♪", quit_button="Quit")
        self._procs: dict[str, subprocess.Popen] = {}
        self.menu = [
            rumps.MenuItem("Lyrics Search", callback=self._open_search),
            rumps.MenuItem("Lyrics Browser", callback=self._open_browser),
            rumps.MenuItem("Open Both", callback=self._open_both),
            None,  # separator before Quit
        ]

    def _launch(self, key: str, module: str):
        """Start module as a subprocess; skip if already running."""
        proc = self._procs.get(key)
        if proc is not None and proc.poll() is None:
            return
        self._procs[key] = subprocess.Popen(
            [sys.executable, "-m", module],
            cwd=str(ROOT),
        )

    def _open_search(self, _):
        self._launch("search", "lyrix.search")

    def _open_browser(self, _):
        self._launch("browser", "lyrix.browser")

    def _open_both(self, _):
        self._launch("search", "lyrix.search")
        self._launch("browser", "lyrix.browser")


if __name__ == "__main__":
    LyricsMenuBar().run()
