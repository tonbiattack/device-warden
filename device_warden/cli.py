import argparse
import json
import subprocess
import sys
import threading
from .client import call
from .console import configure as configure_console
from .store import WardenError


def apps_command(parser, args):
    """Handle `device-warden apps ...`; scanning and installing stay separate."""
    action = args.pop('apps_command')
    if action == 'list':
        result = call('apps-list')
    elif action == 'track':
        result = call('apps-track', **args)
    elif action == 'untrack':
        result = call('apps-untrack', **args)
    elif action == 'scan':
        result = call('apps-scan')
    elif action == 'matrix':
        result = call('apps-matrix')
    else:
        if not args.pop('yes'):
            # Show what would happen and stop: writing to a device needs an explicit go-ahead.
            matrix = call('apps-matrix')
            print(json.dumps({'apk': args['apk_path'], 'devices': [
                {'id': device['id'], 'name': device['display_name'],
                 'connection': device['connection']}
                for device in matrix['devices']]}, ensure_ascii=False, indent=2))
            print('実際にインストールするには --yes を付けてください。', file=sys.stderr)
            return 2
        args['acknowledge'] = True
        result = call('apps-install', **args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def catalog_command(parser, args):
    """Handle `device-warden catalog ...`; Obtainium does the fetching."""
    action = args.pop('catalog_command')
    if action == 'list':
        result = call('catalog')
    elif action == 'add':
        result = call('catalog-add', **args)
    elif action == 'remove':
        result = call('catalog-remove', **args)
    elif action == 'links':
        result = call('catalog-links', **args)
    elif action == 'import-link':
        result = call('catalog-import-link', **args)
    elif action == 'signatures':
        result = call('catalog-signatures', **args)
    else:
        if not args.pop('yes'):
            # Opening Obtainium writes to the device, so show the plan first.
            plan = call('catalog-links', device_id=args['device_id'],
                        packages=args.get('packages'))
            print(json.dumps({'device': plan['serial'], 'connection': plan['connection'],
                              'apps': [{'package': app['package'], 'url': app['url']}
                                       for app in plan['apps']]},
                             ensure_ascii=False, indent=2))
            print('実際に登録するには --yes を付けてください。', file=sys.stderr)
            return 2
        args['acknowledge'] = True
        result = call('catalog-register', **args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def keep_alive(lease_id, interval=30):
    """Refresh a lease while a child process runs.

    `claim` only records the lease; the Broker moves it to `recovery_required`
    after 120s without a heartbeat, and a device in that state is not offered to
    the next caller. A command that runs longer than that would therefore block
    the device for everyone else, so the heartbeat has to follow the work.
    """
    stopped = threading.Event()

    def loop():
        while not stopped.wait(interval):
            try:
                call('heartbeat', lease_id=lease_id)
            except WardenError:
                # The lease is gone or the Broker is unreachable. Stop quietly:
                # the child process is still the user's work, and the release
                # attempt at the end reports the real problem.
                return

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return stopped, thread


def run_command(args):
    """Run a command while holding a lease, so long work stays inside one claim."""
    command = args.pop('command_args')
    if command and command[0] == '--':
        command.pop(0)
    if not command:
        raise WardenError('実行するコマンドを指定してください。')
    claim = call('claim', kind='ai', **args)
    lease_id = claim['lease_id']
    stopped, thread = keep_alive(lease_id)
    try:
        try:
            completed = subprocess.run(command)
        except OSError as exc:
            # The command never started, so nothing touched the device. Free the
            # lease instead of leaving it for the next caller to trip over.
            stopped.set()
            thread.join(timeout=5)
            call('release', lease_id=lease_id)
            raise WardenError(f'コマンドを実行できませんでした: {exc}') from exc
        stopped.set()
        thread.join(timeout=5)
        if completed.returncode != 0:
            # Keep the lease: a failed command usually means the operator needs
            # to look at the device, and silently freeing it invites a second
            # session to start on top of an unfinished investigation.
            print('コマンドが失敗したためリースを保持しました。状況を確認してください。', file=sys.stderr)
            print(json.dumps({'lease_id': lease_id, 'exit_code': completed.returncode},
                             ensure_ascii=False), file=sys.stderr)
            return completed.returncode if completed.returncode > 0 else 1
        call('release', lease_id=lease_id)
        print(json.dumps({'exit_code': 0, 'device_id': claim['device_id']}, ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        stopped.set()
        print('操作が中断されたためリースを保持しました。状況を確認してください。', file=sys.stderr)
        print(json.dumps({'lease_id': lease_id}, ensure_ascii=False), file=sys.stderr)
        return 1


# Options each lease-taking subcommand accepts. Anything else that starts with
# '-' in the first position is a lease ID, not an option.
LEASE_COMMAND_OPTIONS = {
    'heartbeat': {'-h', '--help'},
    'release': {'-h', '--help', '--device', '--session-id'},
    'adb': {'-h', '--help'},
}


def protect_lease_id(argv):
    """Insert `--` before a lease ID that starts with '-'.

    Lease IDs come from `secrets.token_urlsafe`, which produces a leading '-'
    about 1.5% of the time. argparse reads such a value as an option and
    rejects the command, so `release <lease_id>` would fail for those leases
    and the device would stay held. `--` ends option parsing, which makes the
    value positional again.
    """
    if not argv or argv[0] not in LEASE_COMMAND_OPTIONS:
        return argv
    known = LEASE_COMMAND_OPTIONS[argv[0]]
    for index, value in enumerate(argv[1:], start=1):
        if value == '--':
            return argv
        if value.startswith('-'):
            # A real option (or its value) comes first; leave the line alone.
            if value.split('=', 1)[0] in known:
                return argv
            # Otherwise this is a lease ID that happens to start with '-'.
            return [*argv[:index], '--', *argv[index:]]
        return argv
    return argv


def main():
    # The help text and error messages are Japanese. On a console whose code
    # page cannot represent them (cp1252 on an English Windows, for example)
    # argparse would crash while printing --help, so match the console first.
    configure_console()
    parser = argparse.ArgumentParser(prog='device-warden')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('list', 'status', 'refresh', 'audit'):
        sub.add_parser(name)
    sub.choices['audit'].add_argument('--device', dest='device_id')
    for name in ('reconfirm', 'scrcpy'):
        sub.add_parser(name).add_argument('device_id')
    recover = sub.add_parser('recover', help='要確認のリースを復旧する')
    recover.add_argument('device_id')
    recover.add_argument('--acknowledge', action='store_true', required=True)
    claim = sub.add_parser('claim')
    claim.add_argument('--device', dest='device_id')
    claim.add_argument('--tool', required=True)
    for option in ('session-id', 'title', 'project', 'workdir'):
        claim.add_argument('--' + option)
    claim.add_argument('--pid', type=int)
    claim.add_argument('--resume', action='store_true', help='自分が中断した要確認リースを引き継ぐ')
    for name in ('heartbeat',):
        sub.add_parser(name).add_argument('lease_id')
    release = sub.add_parser('release')
    release.add_argument('lease_id', nargs='?')
    release.add_argument('--device', dest='device_id')
    release.add_argument('--session-id', required=False)
    force = sub.add_parser('force-release')
    force.add_argument('device_id')
    force.add_argument('--acknowledge', action='store_true', required=True)
    adb = sub.add_parser('adb')
    adb.add_argument('lease_id')
    adb.add_argument('args', nargs=argparse.REMAINDER)
    scoped = sub.add_parser('with-device', help='端末を確保し、Broker経由でADBを1回実行')
    scoped.add_argument('--device', dest='device_id')
    scoped.add_argument('--tool', required=True)
    scoped.add_argument('--session-id')
    scoped.add_argument('args', nargs=argparse.REMAINDER)
    runner = sub.add_parser('run', help='端末を確保し、コマンド実行中はheartbeatを送り続ける')
    runner.add_argument('--device', dest='device_id')
    runner.add_argument('--tool', required=True)
    for option in ('session-id', 'title', 'project', 'workdir'):
        runner.add_argument('--' + option)
    runner.add_argument('command_args', nargs=argparse.REMAINDER)
    apps = sub.add_parser('apps', help='端末ごとのアプリ・バージョン管理')
    apps_sub = apps.add_subparsers(dest='apps_command', required=True)
    apps_sub.add_parser('list')
    apps_sub.add_parser('scan')
    apps_sub.add_parser('matrix')
    track = apps_sub.add_parser('track')
    track.add_argument('package')
    track.add_argument('--label')
    untrack = apps_sub.add_parser('untrack')
    untrack.add_argument('package')
    install = apps_sub.add_parser('install')
    # The Broker action takes `apk_path`; keep the CLI flag short but send the
    # name the Store method actually accepts, so the two cannot drift apart.
    install.add_argument('apk_path', metavar='apk')
    install.add_argument('--device', dest='device_ids', action='append')
    install.add_argument('--yes', action='store_true', help='インストールを実行する')
    catalog = sub.add_parser('catalog', help='Obtainiumで配布するアプリのカタログ')
    catalog_sub = catalog.add_subparsers(dest='catalog_command', required=True)
    catalog_sub.add_parser('list')
    catalog_add = catalog_sub.add_parser('add', help='パッケージとGitHubリポジトリを登録')
    catalog_add.add_argument('package')
    catalog_add.add_argument('url')
    catalog_add.add_argument('--author')
    catalog_remove = catalog_sub.add_parser('remove')
    catalog_remove.add_argument('package')
    catalog_links = catalog_sub.add_parser('links', help='Obtainiumのディープリンクを表示')
    catalog_links.add_argument('device_id')
    catalog_links.add_argument('--package', dest='packages', action='append')
    catalog_import = catalog_sub.add_parser('import-link', help='一括インポート用リンクを表示')
    catalog_import.add_argument('--package', dest='packages', action='append')
    catalog_register = catalog_sub.add_parser('register', help='端末のObtainiumで登録画面を開く')
    catalog_register.add_argument('device_id')
    catalog_register.add_argument('--package', dest='packages', action='append')
    catalog_register.add_argument('--yes', action='store_true', help='実際に端末で開く')
    catalog_signatures = catalog_sub.add_parser('signatures', help='端末の署名証明書を確認')
    catalog_signatures.add_argument('device_id')
    catalog_signatures.add_argument('--package', dest='packages', action='append')
    args = vars(parser.parse_args(protect_lease_id(sys.argv[1:])))
    command = args.pop('command')
    try:
        if command == 'claim':
            args['kind'] = 'ai'
        if command == 'adb' and args['args'] and args['args'][0] == '--':
            args['args'].pop(0)
        if command == 'with-device':
            adb_args = args.pop('args')
            if adb_args and adb_args[0] == '--':
                adb_args.pop(0)
            if not adb_args:
                raise WardenError('実行するADBサブコマンドを指定してください。')
            claim = call('claim', kind='ai', **args)
            lease_id = claim['lease_id']
            try:
                result = call('adb', lease_id=lease_id, args=adb_args)
                if result['exit_code'] != 0:
                    print('ADBが失敗したためリースを保持しました。状況を確認してください。', file=sys.stderr)
                    print(json.dumps({'lease_id': lease_id, **result}, ensure_ascii=False), file=sys.stderr)
                    return result['exit_code'] if result['exit_code'] > 0 else 1
                call('release', lease_id=lease_id)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            except (WardenError, KeyboardInterrupt):
                print('操作が中断されたためリースを保持しました。状況を確認してください。', file=sys.stderr)
                print(json.dumps({'lease_id': lease_id}, ensure_ascii=False), file=sys.stderr)
                return 1
        if command == 'force-release':
            args['acknowledge'] = True
        if command == 'run':
            return run_command(args)
        if command == 'claim' and args.get('resume') and not args.get('session_id'):
            raise WardenError('--resume には --session-id が必要です。')
        if command == 'apps':
            return apps_command(parser, args)
        if command == 'catalog':
            return catalog_command(parser, args)
        result = call('list' if command == 'status' else command, **args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except WardenError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
