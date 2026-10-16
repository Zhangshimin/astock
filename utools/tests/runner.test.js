'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const runner = require('../runner');

test('stock codes preserve leading zeroes and normalize exchange notation', () => {
  for (const value of ['002837', 'sz002837', '002837.SZ', ' 002837 ']) {
    assert.equal(runner.normalizeCode(value), '002837');
  }
  for (const value of ['12345', '002837 & calc', '002837;exit', '', 'abcdef']) {
    assert.throws(() => runner.normalizeCode(value), /6 位/);
  }
});

test('settings reject invalid polling and discard legacy executable paths', () => {
  assert.equal(runner.normalizeSettings().poll, 5);
  for (const poll of [0, 2, 3.5, 3601, 'NaN']) {
    assert.throws(() => runner.normalizeSettings({ poll }), /刷新间隔/);
  }
  const clean = runner.normalizeSettings({ projectRoot: 'D:/old', python: 'python' });
  assert.equal(clean.projectRoot, undefined);
  assert.equal(clean.python, undefined);
});

test('manifest shortcuts match intended commands only and assets exist', () => {
  const manifest = require('../plugin.json');
  for (const file of [manifest.main, manifest.preload, manifest.logo]) {
    assert.ok(fs.existsSync(path.join(__dirname, '..', file)));
  }
  const matches = (feature, input) => {
    const entry = manifest.features.find(item => item.code === feature).cmds.find(item => typeof item === 'object');
    const [, pattern, flags] = entry.match.match(/^\/(.*)\/([a-z]*)$/);
    return new RegExp(pattern, flags).test(input);
  };
  assert.ok(matches('home', 'sz002837'));
  assert.ok(matches('bar', '盯盘 002837'));
  assert.ok(matches('gui', '图表 600519'));
  assert.ok(!matches('bar', '盯盘 002837 & calc'));
  assert.ok(!matches('gui', '600519'));
});

test('invalid launch requests are rejected before a process starts', async () => {
  await assert.rejects(runner.launch({}, '002837', 'arbitrary'), /窗口类型/);
  await assert.rejects(runner.launch({}, '002837 & calc', 'bar'), /6 位/);
});

test('desktop settings default to visible and reject inconsistent strategy values', () => {
  assert.equal(runner.normalizeSettings().displayMode, 'always');
  assert.equal(runner.normalizeSettings().dockMode, 'taskbar');
  assert.throws(() => runner.normalizeSettings({ sellStart: '14:30', sellCutoff: '09:45' }), /开始时间/);
  assert.throws(() => runner.normalizeSettings({ displayStart: '25:00' }), /无效/);
  assert.throws(() => runner.normalizeSettings({ sound: 'false' }), /无效/);
  assert.throws(() => runner.normalizeSettings({ buybackDiscount: -1 }), /无效/);
  assert.throws(() => runner.normalizeSettings({ sectorCode: 'anything' }), /板块代码/);
});

test('legacy records migrate once without overwriting existing user data', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'qiaoding-migrate-'));
  const old = path.join(root, 'old'); const dataDir = path.join(root, 'data');
  fs.mkdirSync(path.join(old, 'var'), {recursive:true});
  fs.writeFileSync(path.join(old, 'var', 'intraday_trades.json'), '[]');
  const instance = runner.createRunner({dataDir});
  instance.currentSettings({projectRoot:old});
  assert.equal(fs.readFileSync(path.join(dataDir,'intraday_trades.json'),'utf8'),'[]');
  fs.writeFileSync(path.join(dataDir,'intraday_trades.json'),'[{}]');
  instance.currentSettings({projectRoot:old});
  assert.equal(fs.readFileSync(path.join(dataDir,'intraday_trades.json'),'utf8'),'[{}]');
  fs.rmSync(root,{recursive:true,force:true});
});

test('bundled engine runs with no external Python and writes to isolated user data', {
  skip: !process.env.ASTOCK_TEST_PACKAGE,
}, async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), '悄盯 test data '));
  const instance = runner.createRunner({packageDirectory:process.env.ASTOCK_TEST_PACKAGE, dataDir});
  const previousPath = process.env.PATH;
  process.env.PATH = process.env.SystemRoot + '/System32';
  try {
    const result = await instance.checkRuntime();
    assert.equal(result.bundled, true);
    assert.equal(result.dataDir, dataDir);
    assert.ok(result.executable.startsWith(dataDir));
    instance.applySettings({sound:false});
    assert.equal(instance.currentSettings({}).python, undefined);
  } finally {
    process.env.PATH = previousPath;
    fs.rmSync(dataDir,{recursive:true,force:true});
  }
});
