'use strict';

const path = require('path');
const fs = require('fs');
const runner = require('./runner');
const SETTINGS_KEY = 'astock.settings.v1';

window.astock = {
  schema: runner.schema,
  getSettings() { return runner.currentSettings(utools.dbStorage.getItem(SETTINGS_KEY) || {}); },
  saveSettings(value) {
    const settings = runner.applySettings(value);
    utools.dbStorage.setItem(SETTINGS_KEY, settings);
    return settings;
  },
  checkRuntime: runner.checkRuntime,
  launch: runner.launch,
  openFolder(value, kind) {
    const folders = { reports: [], logs: ['logs'] };
    if (!Object.hasOwnProperty.call(folders, kind)) throw new Error('不支持的目录。');
    const target = path.join(runner.dataDir, ...folders[kind]);
    fs.mkdirSync(target, { recursive: true });
    utools.shellOpenPath(target);
  },
  onEnter(callback) {
    utools.onPluginEnter(event => {
      if (utools.setExpendHeight) utools.setExpendHeight(620);
      callback(event);
    });
  },
};
