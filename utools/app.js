'use strict';

const $ = id => document.getElementById(id);
const api = window.astock;
let settings;
let busy = false;
let preferredMode = 'bar';
const fields = api ? api.schema : [];
const commonLabels = {
  dockMode: '显示位置', topmost: '窗口置顶', dragEnabled: '允许拖拽',
  chameleon: '变色龙底色', notifications: '信号提醒', audioEnabled: '声音提醒',
};

function buildSettings() {
  const groups = new Map();
  for (const field of fields) {
    const common = Object.prototype.hasOwnProperty.call(commonLabels, field.key);
    if (!common && !groups.has(field.group)) {
      const section = document.createElement('fieldset');
      const legend = document.createElement('legend');
      legend.textContent = field.group;
      section.appendChild(legend);
      $(field.group === '声音提醒' ? 'sound-settings' : 'advanced-settings').appendChild(section);
      groups.set(field.group, section);
    }
    const label = document.createElement('label');
    label.htmlFor = `setting-${field.key}`;
    label.textContent = commonLabels[field.key] || field.label;
    label.title = field.label;
    const input = document.createElement(field.type === 'select' ? 'select' : 'input');
    input.id = label.htmlFor;
    if (field.type === 'select') {
      for (const [value, title] of Object.entries(field.options)) {
        const option = document.createElement('option');
        option.value = value; option.textContent = title; input.appendChild(option);
      }
    } else {
      input.type = field.type === 'boolean' ? 'checkbox' : ['number', 'integer'].includes(field.type) ? 'number' : field.type;
      if (field.min !== undefined) { input.min = field.min; input.max = field.max; input.step = field.step || 1; }
      input.required = field.type !== 'boolean' && field.type !== 'text';
    }
    const cell = document.createElement('div');
    if (field.type === 'boolean') { cell.className = 'check-setting'; cell.append(input, label); }
    else cell.append(label, input);
    (common ? $('common-settings') : groups.get(field.group)).appendChild(cell);
  }
}
buildSettings();
function updatePositionFields() {
  if (!api) return;
  const automatic = $('setting-dockMode').value !== 'floating';
  for (const key of ['windowX', 'windowY']) $(`setting-${key}`).disabled = automatic;
}
if (api) $('setting-dockMode').addEventListener('change', updatePositionFields);

function showStatus(message, detail = '', error = false) {
  $('status-box').hidden = false;
  $('status-box').classList.toggle('error', error);
  $('status').textContent = message;
  $('details').textContent = detail;
  $('details').hidden = !detail;
  $('status-box').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function setBusy(value) {
  busy = value;
  document.querySelectorAll('[data-action]').forEach(button => { button.disabled = value; });
}

async function perform(message, action) {
  if (busy) return;
  setBusy(true);
  showStatus(message);
  try { await action(); }
  catch (error) { showStatus('操作未完成', error.message, true); }
  finally { setBusy(false); }
}

function fillSettings() {
  $('poll').value = settings.poll;
  $('default-code').value = settings.defaultCode;
  for (const field of fields) {
    const value = settings[field.key] === undefined ? field.default : settings[field.key];
    $(`setting-${field.key}`)[field.type === 'boolean' ? 'checked' : 'value'] = value;
  }
  updatePositionFields();
}

function setSettingsVisible(visible) {
  $('settings').hidden = !visible;
  $('settings-toggle').setAttribute('aria-expanded', String(visible));
  $('settings-toggle').textContent = visible ? '返回工作台' : '设置';
  $('workbench').hidden = visible;
  $('workbench-tools').hidden = visible;
}

$('settings-form').addEventListener('invalid', event => {
  for (let parent = event.target.parentElement; parent; parent = parent.parentElement) {
    if (parent.tagName === 'DETAILS') parent.open = true;
  }
}, true);

function launch(mode) {
  return perform('正在检查运行环境并启动窗口…', async () => {
    const result = await api.launch(settings, $('stock-code').value, mode);
    showStatus(result.visible ? `${result.code} ${mode === 'bar' ? '行情条' : '分时图表'}已确认显示。`
      : '窗口已就绪，但当前显示时段或工作日设置将它隐藏。可点击“立即显示行情条”。',
    `窗口独立运行。启动日志：\n${result.logPath}`);
  });
}

$('settings-toggle').addEventListener('click', () => setSettingsVisible($('settings').hidden));
$('launch-form').addEventListener('submit', event => { event.preventDefault(); launch(preferredMode); });
$('launch-bar').addEventListener('click', () => { preferredMode = 'bar'; });
$('launch-gui').addEventListener('click', () => launch('gui'));
$('show-now').addEventListener('click', async () => {
  if (busy) return;
  let saved = false;
  await perform('正在切换为全天显示…', async () => {
    settings = api.saveSettings({ ...settings, displayMode: 'always', weekdaysOnly: false, topmost: true });
    fillSettings();
    saved = true;
  });
  if (saved) launch('bar');
});
$('settings-form').addEventListener('submit', event => {
  event.preventDefault();
  perform('正在保存设置…', async () => {
    const advanced = {};
    for (const field of fields) {
      const input = $(`setting-${field.key}`);
      advanced[field.key] = field.type === 'boolean' ? input.checked
        : ['number', 'integer'].includes(field.type) ? Number(input.value) : input.value.trim();
    }
    settings = api.saveSettings({ ...settings, ...advanced,
      poll: $('poll').value, defaultCode: $('default-code').value,
    });
    fillSettings();
    showStatus('设置已保存。运行中的新版窗口将在约 1 秒内应用；旧版窗口需关闭后重新启动。');
  });
});
$('runtime').addEventListener('click', () => perform('正在检查内置组件…', async () => {
  const result = await api.checkRuntime();
  showStatus('内置组件正常，无需安装 Python。', `版本 ${result.version} · ${result.architecture}\n用户数据：${result.dataDir}`);
}));
for (const kind of ['reports', 'logs']) {
  $(kind).addEventListener('click', () => perform('正在打开目录…', async () => {
    api.openFolder(settings, kind);
    showStatus(kind === 'reports' ? '已请求打开用户数据目录。' : '已请求打开启动日志目录。');
  }));
}

if (!api) {
  setBusy(true);
  $('settings-toggle').disabled = true;
  showStatus('这是 uTools 插件页面，请在 uTools 开发者工具中导入本目录的 plugin.json。');
} else {
  try {
    settings = api.getSettings();
  } catch (error) {
    // Keep settings editable even when old saved settings are invalid.
    settings = { poll: 5, defaultCode: '002837' };
    setSettingsVisible(true);
    showStatus('请重新配置运行设置', error.message, true);
  }
  fillSettings();
  $('stock-code').value = settings.defaultCode;
  api.onEnter(event => {
    if (!busy) {
      try { settings = api.getSettings(); fillSettings(); }
      catch (error) { showStatus('读取设置失败', error.message, true); }
    }
    setSettingsVisible(event.code === 'settings');
    if (event.code === 'settings') {
      setSettingsVisible(true);
      $('settings').scrollIntoView({ behavior: 'smooth' });
      return;
    }
    const payload = typeof event.payload === 'string' ? event.payload.trim() : '';
    const code = payload.replace(/^(盯盘|图表)\s+/, '');
    if (/^(sh|sz|bj)?\d{6}(\.(SH|SZ|BJ))?$/i.test(code)) $('stock-code').value = code;
    preferredMode = event.code === 'gui' ? 'gui' : 'bar';
    $('stock-code').focus();
    $('stock-code').select();
    if (['bar', 'gui'].includes(event.code) && /^(盯盘|图表)\s+\d{6}$/.test(payload)) launch(preferredMode);
  });
}
