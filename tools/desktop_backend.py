"""Frozen desktop entry point. No source checkout or external Python is needed."""
import argparse
import json
import os
import platform
import sys


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--data-dir')
    parser.add_argument('--self-check', action='store_true')
    args, remaining = parser.parse_known_args()
    if args.data_dir:
        os.environ['QIAODING_DATA_DIR'] = args.data_dir
    from astock_hub.desktop_paths import user_data_dir
    from tools import intraday_macd_alert
    if args.self_check:
        import tkinter
        from zoneinfo import ZoneInfo
        from astock_hub.desktop_settings import validate_settings
        validate_settings({})
        ZoneInfo('Asia/Shanghai')
        print(json.dumps({
            'ok': True, 'bundled': bool(getattr(sys, 'frozen', False)),
            'version': '0.3.0', 'architecture': platform.machine(),
            'tk': tkinter.Tcl().eval('info patchlevel'), 'dataDir': str(user_data_dir()),
        }, ensure_ascii=False), flush=True)
        return 0
    sys.argv = [sys.argv[0], *remaining]
    return intraday_macd_alert.main()


if __name__ == '__main__':
    raise SystemExit(main())
