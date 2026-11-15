import argparse
import json
import sys
from .client import call
from .store import WardenError


def main():
    parser = argparse.ArgumentParser(prog='device-warden')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('list', 'status', 'refresh', 'audit'):
        sub.add_parser(name)
    sub.choices['audit'].add_argument('--device', dest='device_id')
    for name in ('reconfirm', 'scrcpy'):
        sub.add_parser(name).add_argument('device_id')
    claim = sub.add_parser('claim')
    claim.add_argument('--device', dest='device_id')
    claim.add_argument('--tool', required=True)
    for option in ('session-id', 'title', 'project', 'workdir'):
        claim.add_argument('--' + option)
    claim.add_argument('--pid', type=int)
    for name in ('heartbeat', 'release'):
        sub.add_parser(name).add_argument('lease_id')
    adb = sub.add_parser('adb')
    adb.add_argument('lease_id')
    adb.add_argument('args', nargs=argparse.REMAINDER)
    scoped = sub.add_parser('with-device', help='端末を確保し、Broker経由でADBを1回実行')
    scoped.add_argument('--device', dest='device_id')
    scoped.add_argument('--tool', required=True)
    scoped.add_argument('--session-id')
    scoped.add_argument('args', nargs=argparse.REMAINDER)
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
        result = call('list' if command == 'status' else command, **args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except WardenError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
