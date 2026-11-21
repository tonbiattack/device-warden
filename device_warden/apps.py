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
# `signatures=PackageSignatures{... signatures:[b2d95fc0], past signatures:[]}`
# The first `signatures:[...]` is the current one; `past signatures` follows it.
SIGNATURE = re.compile(r'\bsignatures:\[(?P<signatures>[^\]]*)\]', re.IGNORECASE)
# `Signer #1 certificate SHA-256 digest: 3a29d602...` from apksigner.
CERT_DIGEST = re.compile(r'certificate SHA-256 digest:\s*(?P<digest>[0-9a-fA-F:]+)')


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


def signing_certificates(output):
    """Return the short signature hashes from `dumpsys package <pkg>`.

    These are Android's internal package-signature hashes, not the certificate
    digest. They are stable per signing key, so they can tell two installs of
    the same app apart, but they do NOT match the SHA-256 digest Obtainium
    shows. Use `certificate_digest` when the value must be compared with
    Obtainium or apksigner.
    """
    for line in output.splitlines():
        match = SIGNATURE.search(line)
        if match:
            return [part.strip() for part in match['signatures'].split(',') if part.strip()]
    return []


def certificate_digest(apksigner_output):
    """Read the SHA-256 certificate digest from `apksigner verify --print-certs`.

    This is the value Obtainium displays as "Certificate Hash", so it is the
    only one that can be compared against what the user sees there.
    """
    for line in apksigner_output.splitlines():
        match = CERT_DIGEST.search(line)
        if match:
            return match['digest'].replace(':', '').lower()
    return None


def signature_matches(installed, expected):
    """Compare two certificate lists.

    Returns None when either side is unknown: an unreadable signature must not
    be reported as a mismatch, because that would block a legitimate update.
    """
    if not installed or not expected:
        return None
    return set(installed) == set(expected)
