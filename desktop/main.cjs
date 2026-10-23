'use strict';
const {app, BrowserWindow, session, dialog, Menu} = require('electron');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {sameOrigin, audioOnly} = require('./policy.cjs');

const defaultsPath = path.join(__dirname, 'launch.json');
const defaults = fs.existsSync(defaultsPath) ? JSON.parse(fs.readFileSync(defaultsPath, 'utf8')) : {};
const project = defaults.project || path.resolve(__dirname, '..');
const home = path.resolve(process.env.AGENT_ZERO_HOME || defaults.home || path.join(project, '.agent-zero'));
const python = defaults.python || path.join(project, '.venv', 'bin', 'python');
const configPath = path.join(home, 'config.json');
const config = fs.existsSync(configPath) ? JSON.parse(fs.readFileSync(configPath, 'utf8')) : {};
if (config.host && config.host !== '127.0.0.1') throw new Error('Desktop requires the loopback runtime');
const origin = `http://127.0.0.1:${config.port || 8420}`;
fs.mkdirSync(home, {recursive: true, mode: 0o700});
app.setPath('userData', path.join(home, 'electron'));
app.setName('Agent Zero');
let window;
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); } });
  app.whenReady().then(async () => {
    // The Python service owns all inference, credentials and kernel authority.
    async function alive() {
      try {
        const response = await fetch(`${origin}/api/status`, {signal: AbortSignal.timeout(1000)});
        return response.ok && (await response.json()).ok;
      } catch { return false; }
    }
    if (!await alive()) {
      const log = fs.openSync(path.join(home, 'daemon.log'), 'a', 0o600);
      const backend = spawn(python, ['-m', 'agent_zero', '--home', home, 'serve', '--graph'],
        {cwd: project, detached: true, stdio: ['ignore', log, log]});
      fs.closeSync(log);
      let startupError;
      backend.on('error', error => { startupError = error; });
      backend.unref();
      let ready = false;
      for (let attempt = 0; attempt < 100; attempt++) {
        if (startupError) throw startupError;
        if (await alive()) { ready = true; break; }
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      if (!ready) throw new Error(`Python runtime did not start. See ${path.join(home, 'daemon.log')}`);
    }
    const local = session.defaultSession;
    let microphoneAllowed = false;
    local.setPermissionCheckHandler((contents, permission, requestingOrigin, details) =>
      Boolean(contents && sameOrigin(contents.getURL(), origin) && sameOrigin(requestingOrigin, origin) && audioOnly(permission, details)));
    local.setPermissionRequestHandler(async (contents, permission, callback, details) => {
      if (!contents || !sameOrigin(contents.getURL(), origin) || !sameOrigin(details.requestingUrl, origin) || !audioOnly(permission, details)) return callback(false);
      if (!microphoneAllowed) {
        const choice = await dialog.showMessageBox(window, {type: 'question', title: 'Local microphone',
          message: 'Allow JARVIS to use the microphone during this app session?',
          detail: 'Speech recognition runs locally. Wake monitoring starts only when you enable it.',
          buttons: ['Allow microphone', 'Cancel'], defaultId: 0, cancelId: 1});
        microphoneAllowed = choice.response === 0;
      }
      callback(microphoneAllowed);
    });
    window = new BrowserWindow({title: 'Agent Zero', width: 1120, height: 850, minWidth: 720, minHeight: 580,
      backgroundColor: '#05090e', show: false, autoHideMenuBar: true,
      webPreferences: {nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, webviewTag: false}});
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      {label: 'Agent Zero', submenu: [{role: 'about'}, {type: 'separator'}, {role: 'hide'}, {role: 'quit'}]},
      {label: 'Edit', submenu: [{role: 'undo'}, {role: 'redo'}, {type: 'separator'}, {role: 'cut'}, {role: 'copy'}, {role: 'paste'}, {role: 'selectAll'}]},
      {label: 'View', submenu: [{role: 'reload'}, {role: 'togglefullscreen'}, {role: 'resetZoom'}, {role: 'zoomIn'}, {role: 'zoomOut'}]}
    ]));
    window.webContents.setWindowOpenHandler(() => ({action: 'deny'}));
    window.webContents.on('will-navigate', (event, url) => { if (!sameOrigin(url, origin)) event.preventDefault(); });
    window.webContents.on('will-redirect', (event, url) => { if (!sameOrigin(url, origin)) event.preventDefault(); });
    window.webContents.on('will-attach-webview', event => event.preventDefault());
    window.webContents.on('render-process-gone', (_event, details) => {
      dialog.showErrorBox('Agent Zero display stopped', `Renderer ${details.reason}. Reopen the app; the Python runtime remains separate.`);
    });
    window.once('ready-to-show', () => window.show());
    await window.loadURL(origin);
    app.on('activate', () => window?.show());
  }).catch(error => { dialog.showErrorBox('Agent Zero startup failed', error.message); app.quit(); });
  app.on('window-all-closed', () => app.quit());
}
