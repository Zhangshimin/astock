"""Run a copied release without Python on PATH; verify and close only test windows."""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path


def close_windows(pids):
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    visible = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            if user32.IsWindowVisible(hwnd):
                visible.add(pid.value)
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
        return True

    user32.EnumWindows(visit, 0)
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    for pid in pids:
        handle = kernel.OpenProcess(0x100000, False, pid)
        if handle:
            kernel.WaitForSingleObject(handle, 10000)
            kernel.CloseHandle(handle)
    return visible


def main():
    source = Path(sys.argv[1]).resolve()
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Developer smoke test requires Node (end users do not).')
    with tempfile.TemporaryDirectory(prefix='悄盯 独立发布 ') as temporary:
        root = Path(temporary)
        package = root / 'plugin'
        shutil.copytree(source, package)
        data = root / '用户数据'
        before = sorted(str(p.relative_to(package)) for p in package.rglob('*') if p.is_file())
        env = dict(os.environ)
        env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        script = '''
const path = require('path');
const root = process.argv[1], dataDir = process.argv[2];
const runner = require(path.join(root, 'runner')).createRunner({packageDirectory:root, dataDir});
(async () => {
  for (const mode of ['gui', 'bar']) {
    const result = await runner.launch({notifications:false, dockMode:'floating', windowX:120, windowY:120}, '002837', mode);
    console.log(JSON.stringify(result));
  }
})().catch(error => {console.error(error.message);process.exitCode=1;});
'''
        result = subprocess.run([node, '-e', script, str(package), str(data)], cwd=root,
                                env=env, capture_output=True, encoding='utf-8', timeout=90)
        windows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        try:
            assert result.returncode == 0, result.stderr
            assert len(windows) == 2 and all(window['visible'] for window in windows), windows
            assert all(str(data) in window['logPath'] for window in windows)
            assert before == sorted(str(p.relative_to(package)) for p in package.rglob('*') if p.is_file())
        finally:
            pids = {window['pid'] for window in windows if not window['reused']}
            visible = close_windows(pids)
        assert visible == pids and len(visible) == 2, visible
        print('PASS: copied release, isolated Chinese data path, no Python PATH; chart and taskbar visible; plugin directory unchanged.')


if __name__ == '__main__':
    main()
