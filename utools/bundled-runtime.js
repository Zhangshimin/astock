'use strict';
const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');

function dataDirectory() {
  return path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'QiaodingYigu');
}

function ensureRuntime(packageDirectory = __dirname, dataDir = dataDirectory()) {
  const manifestPath = path.join(packageDirectory, 'runtime-manifest.json');
  if (!fs.existsSync(manifestPath)) throw new Error('插件安装包缺少内置运行组件，请重新安装完整版本。');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  if (process.platform !== 'win32' || process.arch !== manifest.arch) throw new Error('此安装包仅支持 Windows x64。');
  if (!/^[a-f0-9]{64}$/.test(manifest.buildId)) throw new Error('运行组件清单无效。');
  const destination = path.join(dataDir, 'engine', manifest.buildId);
  // Extract through Electron fs so an asar-packaged plugin also works. Executables
  // always run from a real, user-writable directory outside the plugin archive.
  for (const file of manifest.files) {
    if (!file.path || path.isAbsolute(file.path) || file.path.split(/[\\/]/).some(part => part === '..')) throw new Error('运行组件路径无效。');
    const target = path.join(destination, file.path);
    if (fs.existsSync(target) && fs.statSync(target).size === file.size &&
        crypto.createHash('sha256').update(fs.readFileSync(target)).digest('hex') === file.sha256) continue;
    const bytes = fs.readFileSync(path.join(packageDirectory, 'runtime', file.path));
    if (crypto.createHash('sha256').update(bytes).digest('hex') !== file.sha256) throw new Error(`运行组件损坏：${file.path}`);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    const temporary = `${target}.${crypto.randomUUID()}.tmp`;
    fs.writeFileSync(temporary, bytes);
    fs.renameSync(temporary, target);
  }
  const executable = path.join(destination, 'qiaoding-backend.exe');
  if (!fs.existsSync(executable)) throw new Error('内置盯盘程序缺失。');
  return executable;
}

module.exports = { dataDirectory, ensureRuntime };
