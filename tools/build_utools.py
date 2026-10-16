"""Build the self-contained Windows x64 plugin. Run in the isolated build venv."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'utools'


def main():
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise SystemExit('Build on Windows x64 with x64 Python.')
    build_parent = ROOT / 'var' / 'plugin-build'
    build_parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='build-', dir=build_parent))
    extra_binaries = []
    library_bin = Path(sys.base_prefix) / 'Library' / 'bin'
    for name in ('tk86t.dll', 'tcl86t.dll', 'libmpdec-4.dll'):
        candidate = library_bin / name
        if candidate.is_file():
            extra_binaries.extend(['--add-binary', f'{candidate};.'])
    build_env = dict(os.environ)
    if library_bin.is_dir():
        build_env['PATH'] = str(library_bin) + os.pathsep + build_env.get('PATH', '')
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--console',
        '--name', 'qiaoding-backend', '--distpath', str(work / 'dist'),
        '--workpath', str(work / 'work'), '--specpath', str(work), '--paths', str(ROOT),
        '--collect-data', 'tzdata', '--hidden-import', 'pystray._win32',
        '--add-data', f'{PLUGIN / "settings-schema.json"};utools',
        *extra_binaries,
        str(ROOT / 'tools' / 'desktop_backend.py'),
    ], cwd=ROOT, env=build_env, check=True)
    runtime = work / 'dist' / 'qiaoding-backend'
    licenses = runtime / 'licenses'
    licenses.mkdir()
    python_license = next((Path(sys.base_prefix) / name for name in ('LICENSE.txt', 'LICENSE_PYTHON.txt')
                           if (Path(sys.base_prefix) / name).is_file()), None)
    if python_license is None:
        raise RuntimeError('Python distribution license is required for the release')
    shutil.copy2(python_license, licenses / 'Python-LICENSE.txt')
    for name in ('pillow', 'pystray', 'tzdata', 'six', 'pyinstaller'):
        distribution = importlib.metadata.distribution(name)
        for file in distribution.files or []:
            if any(token in file.name.lower() for token in ('license', 'copying', 'notice')):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    target = licenses / name / str(file).replace('/', '_').replace('\\', '_')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    files = [
        {'path': str(file.relative_to(runtime)).replace('\\', '/'),
         'size': file.stat().st_size, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}
        for file in sorted(runtime.rglob('*')) if file.is_file()
    ]
    build_id = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    manifest = {'arch': 'x64', 'buildId': build_id, 'files': files}
    # Only replace this tool's generated runtime inside the fixed plugin directory.
    destination = (PLUGIN / 'runtime').resolve()
    if destination.parent != PLUGIN.resolve() or destination.name != 'runtime':
        raise RuntimeError('Unexpected runtime output path')
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(runtime, destination)
    (PLUGIN / 'runtime-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    version = json.loads((PLUGIN / 'plugin.json').read_text(encoding='utf-8'))['version']
    stage = ROOT / 'dist' / f'qiaoding-yigu-{version}-{build_id[:10]}'
    stage.mkdir(parents=True, exist_ok=True)
    for name in ('plugin.json', 'index.html', 'app.js', 'style.css', 'preload.js', 'runner.js',
                 'bundled-runtime.js', 'settings-schema.json', 'logo.png', 'runtime-manifest.json', 'INSTALL.md'):
        shutil.copy2(PLUGIN / name, stage / name)
    shutil.copytree(runtime, stage / 'runtime', dirs_exist_ok=True)
    archive = shutil.make_archive(str(stage), 'zip', root_dir=stage)
    print(json.dumps({'directory': str(stage), 'zip': archive, 'buildId': build_id,
                      'runtimeBytes': sum(file['size'] for file in files)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
