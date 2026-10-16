'use strict';

const fs = require('fs');
const path = require('path');
const { execFile, spawn } = require('child_process');
const { randomUUID } = require('crypto');
const schema = require('./settings-schema.json');

const { dataDirectory, ensureRuntime } = require('./bundled-runtime');

function createRunner(options = {}) {
const dataDir = options.dataDir || dataDirectory();
const packageDirectory = options.packageDirectory || __dirname;

function normalizeCode(value) {
  const code = String(value || '').trim().toUpperCase()
    .replace(/^(SH|SZ|BJ)/, '').replace(/\.(SH|SZ|BJ)$/, '');
  if (!/^\d{6}$/.test(code)) throw new Error('请输入 6 位股票代码，例如 002837。');
  return code;
}

function normalizeSettings(value = {}) {
  const poll = Number(value.poll === undefined ? 5 : value.poll);
  if (!Number.isInteger(poll) || poll < 3 || poll > 3600) {
    throw new Error('刷新间隔必须是 3 至 3600 秒的整数。');
  }
  const result = { poll, defaultCode: normalizeCode(value.defaultCode || '002837') };
  for (const field of schema) {
    const item = value[field.key] === undefined ? field.default : value[field.key];
    let valid = true;
    if (field.type === 'boolean') valid = typeof item === 'boolean';
    else if (['integer', 'number'].includes(field.type)) {
      valid = typeof item === 'number' && Number.isFinite(item) && item >= field.min && item <= field.max;
      if (field.type === 'integer') valid = valid && Number.isInteger(item);
    } else if (field.type === 'select') valid = Object.prototype.hasOwnProperty.call(field.options, item);
    else if (field.type === 'time') valid = typeof item === 'string' && /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(item);
    else valid = typeof item === 'string';
    if (!valid) throw new Error(`${field.label}无效。`);
    result[field.key] = item;
  }
  if (result.sellStart >= result.sellCutoff) throw new Error('允许卖出开始时间必须早于结束时间。');
  if (result.displayStart === result.displayEnd) throw new Error('显示开始与结束时间不能相同。');
  if (result.alertStart === result.alertEnd) throw new Error('提醒开始与结束时间不能相同。');
  if (result.volumeShrink >= result.volumeSurge) throw new Error('缩量倍数必须小于放量倍数。');
  if (result.sectorCode && !/^BK\d{4,6}$/.test(result.sectorCode)) throw new Error('板块代码格式应为 BK 加 4–6 位数字。');
  return result;
}

function writeConfig(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.${randomUUID()}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(data, null, 2), 'utf8');
  fs.renameSync(temporary, file);
}

function applySettings(value) {
  const settings = normalizeSettings(value);
  const directory = path.join(dataDir, 'runtime');
  if (fs.existsSync(directory)) {
    for (const name of fs.readdirSync(directory).filter(name => /^(bar|gui-[\w-]+)\.json$/.test(name))) {
      const file = path.join(directory, name);
      const previous = JSON.parse(fs.readFileSync(file, 'utf8'));
      writeConfig(file, { ...settings, symbol: previous.symbol, requestId: randomUUID() });
    }
  }
  return settings;
}

function currentSettings(value) {
  if (value && typeof value.projectRoot === 'string' && path.isAbsolute(value.projectRoot)) {
    const source = path.join(value.projectRoot, 'var', 'intraday_trades.json');
    const target = path.join(dataDir, 'intraday_trades.json');
    if (!fs.existsSync(target) && fs.existsSync(source)) {
      const bytes = fs.readFileSync(source);
      if (!Array.isArray(JSON.parse(bytes.toString('utf8')))) throw new Error('旧版交易记录格式无效，请检查原文件。');
      fs.mkdirSync(dataDir, { recursive: true });
      fs.writeFileSync(target, bytes, { flag: 'wx' });
    }
  }
  const settings = normalizeSettings(value);
  const file = path.join(dataDir, 'runtime', 'bar.json');
  try {
    const latest = JSON.parse(fs.readFileSync(file, 'utf8'));
    return normalizeSettings({ ...settings, dockMode: latest.dockMode, windowX: latest.windowX, windowY: latest.windowY });
  } catch { return settings; }
}

async function checkRuntime() {
  const executable = ensureRuntime(packageDirectory, dataDir);
  return new Promise((resolve, reject) => {
    execFile(executable, ['--self-check', '--data-dir', dataDir], {
      cwd: path.dirname(executable), windowsHide: true, shell: false,
      timeout: 30000, maxBuffer: 1024 * 1024,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    }, (error, stdout, stderr) => {
      if (error) return reject(new Error(`内置运行组件检查失败：${stderr || error.message}`));
      try {
        const result = JSON.parse(stdout.trim());
        if (!result.ok || !result.bundled) throw new Error('运行组件无效');
        resolve({ ...result, executable });
      } catch (error) { reject(new Error(`运行组件返回异常：${error.message}`)); }
    });
  });
}

async function launch(value, rawCode, mode) {
  if (!['bar', 'gui'].includes(mode)) throw new Error('不支持的窗口类型。');
  const settings = normalizeSettings(value);
  const code = normalizeCode(rawCode);
  const runtime = await checkRuntime(settings);
  const requestId = randomUUID();
  const configPath = path.join(dataDir, 'runtime', mode === 'bar' ? 'bar.json' : `gui-${requestId}.json`);
  writeConfig(configPath, { ...settings, symbol: code, requestId });
  const statusPath = configPath.replace(/\.json$/, '.status.json');
  const logDir = path.join(dataDir, 'logs');
  fs.mkdirSync(logDir, { recursive: true });
  const logPath = path.join(logDir, `${Date.now()}-${code}-${mode}.log`);
  const descriptor = fs.openSync(logPath, 'a');
  let child;
  try {
    child = spawn(runtime.executable, [
      code,
      `--${mode}`, '--poll', String(settings.poll),
      '--settings', configPath, '--data-dir', dataDir,
    ], {
      cwd: path.dirname(runtime.executable), detached: true, windowsHide: true, shell: false,
      stdio: ['ignore', descriptor, descriptor],
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' },
    });
  } finally { fs.closeSync(descriptor); }
  return new Promise((resolve, reject) => {
    let finished = false;
    let duplicate = false;
    const finish = (error, status) => {
      if (finished) return;
      finished = true;
      clearInterval(timer);
      child.unref();
      if (error) reject(error);
      else resolve({ code, mode, logPath, pid: status.pid, exited: false, reused: status.pid !== child.pid, ...status });
    };
    const deadline = Date.now() + 20000;
    const timer = setInterval(() => {
      try {
        const status = JSON.parse(fs.readFileSync(statusPath, 'utf8'));
        if (status.requestId === requestId && status.ready) return finish(null, status);
      } catch { /* Wait for the Tk window to acknowledge the requested configuration. */ }
      if (Date.now() > deadline) finish(new Error(duplicate
        ? '旧版行情条正在运行，无法接收配置。请先关闭旧行情条，再从 uTools 启动。'
        : `窗口未确认就绪，请查看日志：${logPath}\n${fs.readFileSync(logPath, 'utf8').slice(-6000)}`));
    }, 100);
    child.once('error', error => finish(new Error(`启动失败：${error.message}`)));
    child.once('exit', exitCode => {
      if (exitCode === 3) { duplicate = true; return; }
      if (!finished) finish(new Error(`盯盘程序退出（${exitCode}）。\n${fs.readFileSync(logPath, 'utf8').slice(-6000)}\n日志：${logPath}`));
    });
  });
}

return { schema, normalizeCode, normalizeSettings, applySettings, currentSettings, checkRuntime, launch, dataDir };
}

module.exports = { ...createRunner(), createRunner };
