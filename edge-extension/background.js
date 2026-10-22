// Only extension pages and the allowlisted native host can request cookies.
const HOST = 'com.mediagrab.edge_bridge';
let port = null;
let status = {connected: false, message: 'Click Connect to enable this Edge profile.'};
let connecting = false;

async function connect(select = false) {
  if (connecting) return;
  connecting = true;
  try {
    const saved = await chrome.storage.local.get(['enabled', 'profile']);
    if (!select && !saved.enabled) return;
    if (port) {
      if (!select) return;
      const previous = port;
      port = null;
      previous.disconnect();
    }
    const profile = saved.profile || crypto.randomUUID();
    await chrome.storage.local.set({enabled: true, profile});
    const current = chrome.runtime.connectNative(HOST);
    port = current;
    status = {connected: false, message: 'Connecting to MediaGrab...'};
    current.onMessage.addListener(async message => {
      if (port !== current) return;
      if (message.type === 'status') {
        status = {connected: Boolean(message.ok), message: String(message.message)};
        return;
      }
      if (message.type !== 'cookies' || typeof message.id !== 'string') return;
      try {
        // Default, non-incognito store in this extension's Edge profile only.
        const groups = await Promise.all([
          chrome.cookies.getAll({domain: 'youtube.com'}),
          chrome.cookies.getAll({domain: 'google.com'})
        ]);
        const cookies = groups.flat().filter(c => !c.partitionKey);
        current.postMessage({type: 'cookies', id: message.id, cookies});
      } catch {
        try { current.postMessage({type: 'cookies', id: message.id, error: 'cookie_access_failed'}); } catch {}
      }
    });
    current.onDisconnect.addListener(() => {
      // Consume lastError, but never log raw browser or cookie data.
      const failed = Boolean(chrome.runtime.lastError);
      if (port !== current) return;
      port = null;
      if (status.connected || failed) status = {connected: false, message: 'Not connected. In MediaGrab, click Edge setup, then Connect here.'};
    });
    current.postMessage({type: 'hello', profile, select});
  } finally {
    connecting = false;
  }
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL('popup.html')) return;
  (async () => {
    if (message.type === 'connect') await connect(true);
    if (message.type === 'disconnect') {
      await chrome.storage.local.set({enabled: false});
      const previous = port;
      port = null;
      if (previous) previous.disconnect();
      status = {connected: false, message: 'Disconnected. No session is shared.'};
    }
    respond(status);
  })().catch(() => respond({connected: false, message: 'Connection failed. Try Edge setup in MediaGrab.'}));
  return true;
});

chrome.runtime.onStartup.addListener(() => connect());
chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create('reconnect', {periodInMinutes: 1});
});
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'reconnect') connect(); });
connect();
