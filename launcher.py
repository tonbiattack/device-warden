"""PyInstaller entry point; running the package module directly avoids relative-import failures."""

import sys

from device_warden import msixmode

# Apply the development isolation before any module reads the environment, so a
# packaged experiment cannot attach to the production Broker or its database.
if msixmode.is_development_build():
    msixmode.apply_isolation()

from device_warden.tray import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
