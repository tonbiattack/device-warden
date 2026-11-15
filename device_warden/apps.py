"""Parse `pm` / `dumpsys package` output.

The Broker only needs a few fields, and the device output format varies between
Android versions and vendors, so parsing is intentionally line-oriented and
tolerant: a missing field stays None instead of rejecting the whole device.
"""

import re

PACKAGE_LINE = re.compile(r'^package:(?P<package>[A-Za-z0-9_.]+)\s*$')
VERSION_NAME = re.compile(r'\bversionName=(\S+)', re.IGNORECASE)
VERSION_CODE = re.compile(r'\bversionCode=(\d+)', re.IGNORECASE)
FIRST_INSTALL = re.compile(r'\bfirstInstallTime=([0-9-]+ [0-9:]+)', re.IGNORECASE)
LAST_UPDATE = re.compile(r'\blastUpdateTime=([0-9-]+ [0-9:]+)', re.IGNORECASE)
LABEL_LINE = re.compile(r"^\s*application-label[^:]*:\s*(?P<label>.+)$")


def user_packages(output):
    """Return the packages from `pm list packages -3`."""
    packages = []
    for line in output.splitlines():
        match = PACKAGE_LINE.match(line.strip())
        if match:
            packages.append(match['package'])
    return packages


def package_details(output):
    """Extract version and install times from `dumpsys package <pkg>`.

    A device can print several `versionName=` lines (the package plus its
    components), so the first occurrence is used.
    """
    details = {'version_name': None, 'version_code': None,
               'first_install_time': None, 'last_update_time': None}
    for line in output.splitlines():
        if details['version_name'] is None:
            match = VERSION_NAME.search(line)
            if match:
                details['version_name'] = match.group(1).strip()
        if details['version_code'] is None:
            match = VERSION_CODE.search(line)
            if match:
                details['version_code'] = int(match.group(1))
        if details['first_install_time'] is None:
            match = FIRST_INSTALL.search(line)
            if match:
                details['first_install_time'] = match.group(1)
        if details['last_update_time'] is None:
            match = LAST_UPDATE.search(line)
            if match:
                details['last_update_time'] = match.group(1)
        if all(details.values()):
            break
    return details


def apk_label(output):
    """Read `aapt dump badging` output; used only when an APK is supplied."""
    for line in output.splitlines():
        match = LABEL_LINE.match(line)
        if match:
            return match['label'].strip().strip("'\"") or None
    return None


def version_status(version_code, baseline):
    """Compare one cell against the newest version code seen for the app.

    Semantic version strings sort badly (`1.10` < `1.9`), so the version code is
    the only comparison used. A missing baseline or code is reported as unknown.
    """
    if version_code is None or baseline is None:
        return 'unknown'
    if version_code == baseline:
        return 'latest'
    return 'old' if version_code < baseline else 'newer'
