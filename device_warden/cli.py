import argparse
import json
import subprocess
import sys
import threading
from .client import call
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
            print(json.dumps({'apk': args['apk'], 'devices': [
                {'id': device['id'], 'name': device['display_name'],
                 'connection': device['connection']}
                for device in matrix['devices']]}, ensure_ascii=False, indent=2))
            print('実際にインストールするには --yes を付けてください。', file=sys.stderr)
            return 2
        args['acknowledge'] = True
        result = call('apps-install', **args)
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


def main():
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
    install.add_argument('apk')
    install.add_argument('--device', dest='device_ids', action='append')
    install.add_argument('--yes', action='store_true', help='インストールを実行する')
    args = vars(parser.parse_args())
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
        result = call('list' if command == 'status' else command, **args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except WardenError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
