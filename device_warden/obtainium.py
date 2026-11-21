"""Build Obtainium deep links for the tracked app catalog.

Obtainium already fetches and installs updates from a release source, so Device
Warden does not reimplement any of that. It only knows which package comes from
which GitHub repository and turns that into the link Obtainium understands.

The link format is read from the Obtainium source (`lib/pages/home.dart`):

    obtainium://add/<url>          add one app from a source URL
    obtainium://apps/<json>        import a list of apps
    obtainium://app/<json>         import a single app
    obtainium://refresh[?id=<id>]  check for updates

`add` is preferred for registration: Obtainium standardises the URL and, when
the app is already registered, opens the existing entry instead of adding a
duplicate. The import form is only used when several apps must be added at once.
"""

import json
import re

# Obtainium's applicationId. The F-Droid build appends `.fdroid`; a device that
# has that build cannot receive intents addressed to the plain id.
OBTAINIUM_PACKAGE = 'dev.imranr.obtainium'
OBTAINIUM_FDROID_PACKAGE = 'dev.imranr.obtainium.fdroid'

GITHUB_REPO = re.compile(r'^https?://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/?$')
PACKAGE_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$')


class CatalogError(ValueError):
    """A catalog entry that cannot be turned into a usable link."""


def normalize_repo_url(url):
    """Return the canonical `https://github.com/owner/repo` form.

    Obtainium standardises URLs itself, but rejecting a non-repository URL here
    gives the operator a clear error instead of a link that silently fails on
    the device.
    """
    if not isinstance(url, str):
        raise CatalogError('リポジトリURLが文字列ではありません。')
    match = GITHUB_REPO.match(url.strip())
    if not match:
        raise CatalogError('GitHubのリポジトリURLを指定してください（例: https://github.com/owner/repo）。')
    return f"https://github.com/{match['owner']}/{match['repo']}"


def validate_package(package):
    """Check a package name before it reaches a device or a deep link."""
    if not isinstance(package, str) or not PACKAGE_NAME.match(package):
        raise CatalogError('パッケージ名が不正です（例: com.example.app）。')
    return package


def repo_slug(repo_url):
    """Return `owner/repo` for a GitHub repository URL."""
    match = GITHUB_REPO.match(normalize_repo_url(repo_url))
    return f"{match['owner']}/{match['repo']}"


def check_releases(repo_url, runner=None):
    """Report whether the repository has a release Obtainium could install.

    Obtainium resolves an app from its releases; a repository with none cannot
    be registered, and the Add screen simply does nothing. Checking here turns
    that silent failure into a clear message before anything reaches a device.
    """
    import json
    import subprocess

    slug = repo_slug(repo_url)
    runner = runner or subprocess.run
    try:
        result = runner(['gh', 'release', 'list', '--repo', slug, '--limit', '1',
                         '--json', 'tagName'], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return {'checked': False, 'reason': f'リリースを確認できませんでした: {exc}'}
    if result.returncode != 0:
        return {'checked': False, 'reason': 'リリースを確認できませんでした（gh が失敗）。'}
    try:
        releases = json.loads(result.stdout or '[]')
    except ValueError:
        return {'checked': False, 'reason': 'リリースの応答を解釈できませんでした。'}
    if not releases:
        return {'checked': True, 'has_release': False,
                'reason': f'{slug} にはリリースがありません。Obtainiumは登録できません。'}
    return {'checked': True, 'has_release': True, 'tag': releases[0].get('tagName')}


def add_link(repo_url):
    """Deep link that adds one app, or opens it when already registered."""
    return f'obtainium://add/{normalize_repo_url(repo_url)}'


def refresh_link(app_id=None):
    """Deep link that asks Obtainium to check for updates."""
    if app_id is None:
        return 'obtainium://refresh'
    return f'obtainium://refresh?id={app_id}'


def import_payload(entries):
    """Build the app list Obtainium's `apps` import expects.

    `App.fromJson` requires `id`, `url`, `author`, `name` and
    `additionalSettings`; the rest have defaults. `id` is the package name,
    which is what Obtainium compares against the installed app.
    """
    apps = []
    for entry in entries:
        package = validate_package(entry['package'])
        url = normalize_repo_url(entry['url'])
        apps.append({
            'id': package,
            'url': url,
            'author': entry.get('author') or url.split('/')[3],
            'name': entry.get('label') or package,
            'additionalSettings': '{}',
        })
    return apps


def import_link(entries):
    """Deep link that imports several apps at once.

    The `apps` action wraps the decoded JSON itself: `home.dart` builds
    `{'apps': parsedData}` and passes that to `import`. The link must therefore
    carry the bare list, not an object with an `apps` key, or the cast to
    `List<dynamic>` fails with "type '_Map<String, dynamic>' is not a subtype
    of type 'List<dynamic>'".
    """
    apps = import_payload(entries)
    if not apps:
        raise CatalogError('登録するアプリがありません。')
    # The JSON is a path segment, so it must be percent-encoded.
    from urllib.parse import quote
    return 'obtainium://apps/' + quote(json.dumps(apps, ensure_ascii=False), safe='')


def adb_open_args(link, package=OBTAINIUM_PACKAGE):
    """ADB arguments that open a deep link on the device.

    `-W` waits for the launch to settle so a failure is reported instead of
    being lost. `-a android.intent.action.VIEW` with the scheme is what the
    manifest's intent filter matches.
    """
    return ['shell', 'am', 'start', '-W', '-a', 'android.intent.action.VIEW',
            '-d', link, package]


# --- Installing Obtainium itself ---------------------------------------------
#
# Obtainium is an Android app, so every device needs its own copy. Fetching it
# by hand on each device is the step that makes the whole catalog workflow feel
# like a chore, so Device Warden can install it from the official release.
#
# The release publishes one APK per ABI plus a universal build. Picking the
# ABI-specific file keeps the download small; the universal build is the
# fallback when the device reports an ABI we do not recognise.

OBTAINIUM_REPO = 'ImranR98/Obtainium'
# ABI -> asset name in the release. `app-release.apk` is the universal build.
OBTAINIUM_ASSETS = {
    'arm64-v8a': 'app-arm64-v8a-release.apk',
    'armeabi-v7a': 'app-armeabi-v7a-release.apk',
    'x86_64': 'app-x86_64-release.apk',
}
OBTAINIUM_UNIVERSAL = 'app-release.apk'


def choose_asset(abi):
    """Pick the release asset for a device ABI, falling back to the universal build."""
    return OBTAINIUM_ASSETS.get((abi or '').strip(), OBTAINIUM_UNIVERSAL)


def release_assets(payload):
    """Map asset name -> {url, digest, size} from a GitHub release payload."""
    assets = {}
    for asset in payload.get('assets') or []:
        digest = asset.get('digest') or ''
        assets[asset.get('name')] = {
            'url': asset.get('browser_download_url'),
            'digest': digest.split(':', 1)[1].lower() if digest.startswith('sha256:') else None,
            'size': asset.get('size'),
        }
    return assets


def fetch_release(runner=None):
    """Read the latest Obtainium release from the GitHub API.

    `gh` is used rather than a raw HTTP call so the request carries the user's
    existing authentication and rate limit.
    """
    import json
    import subprocess

    runner = runner or subprocess.run
    try:
        result = runner(['gh', 'api', f'repos/{OBTAINIUM_REPO}/releases/latest'],
                        capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CatalogError(f'Obtainiumのリリースを取得できませんでした: {exc}') from exc
    if result.returncode != 0:
        raise CatalogError('Obtainiumのリリースを取得できませんでした（gh が失敗）。')
    try:
        payload = json.loads(result.stdout or '{}')
    except ValueError as exc:
        raise CatalogError('Obtainiumのリリース応答を解釈できませんでした。') from exc
    if not payload.get('tag_name'):
        raise CatalogError('Obtainiumのリリースが見つかりませんでした。')
    return payload


def verify_download(path, expected_digest):
    """Check a downloaded APK against the release's SHA-256.

    The APK is installed on a device, so a corrupted or substituted download
    must be rejected before it reaches one.
    """
    import hashlib
    from pathlib import Path

    if not expected_digest:
        return {'verified': False, 'reason': 'リリースにSHA-256が無いため検証できません。'}
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if digest != expected_digest.lower():
        return {'verified': False, 'reason': f'SHA-256が一致しません（{digest}）。'}
    return {'verified': True, 'digest': digest}
