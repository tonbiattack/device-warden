"""Entry point for Windows sign-in: start the Broker, then the tray."""

import sys
import threading

from . import autostart
from .autostart import ensure_broker, release_single_instance
from .gui import main as gui_main
from .store import WardenError


def main():
    # A packaged build is one executable, so the Broker runs as a mode of it.
    if '--broker' in sys.argv[1:]:
        from .server import main as broker_main
        return broker_main()
    mutex = autostart.acquire_single_instance()
    if not mutex:
        # A second launch should surface the window instead of opening a duplicate.
        autostart.notify_existing_instance()
        return 0
    show_event = autostart.create_show_event()
    try:
        ensure_broker()
        if show_event:
            from . import gui
            threading.Thread(target=autostart.watch_show_events,
                             args=(show_event, gui.show_window), daemon=True).start()
        gui_main()
    except WardenError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        release_single_instance(mutex)
    return 0


if __name__ == '__main__':
    sys.exit(main())
