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
    args = vars(parser.parse_args())
    command = args.pop('command')
    try:
        if command == 'claim':
            args['kind'] = 'ai'
        if command == 'adb' and args['args'] and args['args'][0] == '--':
            args['args'].pop(0)
        result = call('list' if command == 'status' else command, **args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except WardenError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
