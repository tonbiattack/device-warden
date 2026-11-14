"""PyInstaller entry point; running the package module directly avoids relative-import failures."""

import sys

from device_warden.tray import main

if __name__ == '__main__':
    sys.exit(main())
