"""Render the MSIX manifest for a development or Store build.

The two builds differ only in identity values. Keeping them in one template,
rendered from explicit arguments, makes the switch to the reserved Partner
Center identity a single command instead of an edit hunt.
"""

import argparse
import re
import sys
from pathlib import Path

TOKENS = ('IDENTITY_NAME', 'PUBLISHER', 'PUBLISHER_DISPLAY_NAME', 'DISPLAY_NAME',
          'VERSION', 'EXECUTABLE')

# Publisher is the developer-account-wide value shown in Partner Center; it is
# shared with the other Store apps and is not a secret.
STORE_PUBLISHER = 'CN=2F14A66E-02DF-43E2-93E0-5C5900B38D2E'
STORE_PUBLISHER_DISPLAY_NAME = 'tonbiattack'
STORE_DISPLAY_NAME = 'Device Warden'

# Device Warden recognises the development payload by its executable name, so
# the Store build must not use that name: it would start in the isolated mode
# that refuses ADB and ignores the normal data folder.
PROFILES = {
    'dev': {
        'identity_name': 'Tonbiattack.DeviceWarden.Dev',
        'publisher': 'CN=Device Warden Development',
        'publisher_display_name': 'Device Warden Development',
        'display_name': 'Device Warden (Development)',
        'executable': 'DeviceWardenMsixDev\\DeviceWardenMsixDev.exe',
    },
    'store': {
        'identity_name': None,
        'publisher': STORE_PUBLISHER,
        'publisher_display_name': STORE_PUBLISHER_DISPLAY_NAME,
        'display_name': STORE_DISPLAY_NAME,
        'executable': 'DeviceWarden\\DeviceWarden.exe',
    },
}


def project_version(root):
    """Return the four-part package version derived from pyproject.toml."""
    text = (Path(root) / 'pyproject.toml').read_text(encoding='utf-8')
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise ValueError('pyproject.toml に version がありません。')
    parts = match.group(1).split('.')
    if len(parts) > 4 or not all(part.isdigit() for part in parts):
        raise ValueError(f'MSIX のバージョンに使えない値です: {match.group(1)}')
    return '.'.join(parts + ['0'] * (4 - len(parts)))


def render(template, values):
    """Replace every token; an unreplaced token means a value was forgotten."""
    text = template
    for token in TOKENS:
        text = text.replace('{{' + token + '}}', values[token])
    remaining = re.findall(r'\{\{[A-Z_]+\}\}', text)
    if remaining:
        raise ValueError('未設定のトークン: ' + ', '.join(sorted(set(remaining))))
    return text


def payload_name(profile):
    """Return the PyInstaller output folder and executable for a profile."""
    executable = PROFILES[profile]['executable']
    return executable.split('\\')[0]


def values_for(profile, root, identity_name=None, publisher=None,
               publisher_display_name=None, display_name=None, version=None):
    if profile not in PROFILES:
        raise ValueError(f'不明なプロファイルです: {profile}')
    base = dict(PROFILES[profile])
    if identity_name:
        base['identity_name'] = identity_name
    base['publisher'] = publisher or base['publisher']
    base['publisher_display_name'] = publisher_display_name or base['publisher_display_name']
    base['display_name'] = display_name or base['display_name']
    base['version'] = version or project_version(root)
    if not base['identity_name']:
        raise ValueError('Identity Name が未確定です。Partner Center の予約後に '
                         '--identity-name で指定してください。')
    return {token.upper(): base[token.lower()] for token in TOKENS}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='device_warden.msixmanifest')
    parser.add_argument('--profile', choices=sorted(PROFILES), required=True)
    parser.add_argument('--template', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--identity-name')
    parser.add_argument('--publisher')
    parser.add_argument('--publisher-display-name')
    parser.add_argument('--display-name')
    parser.add_argument('--version')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    try:
        values = values_for(args.profile, root, args.identity_name, args.publisher,
                            args.publisher_display_name, args.display_name, args.version)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    template = Path(args.template).read_text(encoding='utf-8')
    try:
        text = render(template, values)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    Path(args.output).write_text(text, encoding='utf-8')
    print(f"{values['IDENTITY_NAME']} / {values['PUBLISHER']} / {values['VERSION']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
