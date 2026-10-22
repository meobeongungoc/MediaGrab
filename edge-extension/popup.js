async function request(type) {
  try {
    const status = await chrome.runtime.sendMessage({type});
    document.getElementById('status').textContent = status.message;
  } catch {
    document.getElementById('status').textContent = 'Reload the extension and try Connect again.';
  }
}
document.getElementById('connect').addEventListener('click', () => request('connect'));
document.getElementById('disconnect').addEventListener('click', () => request('disconnect'));
request('status');
setInterval(() => request('status'), 1000);
const theme = document.getElementById('theme');
chrome.storage.local.get('theme').then(saved => {
  if ([...theme.options].some(option => option.value === saved.theme)) theme.value = saved.theme;
  document.body.dataset.theme = theme.value;
});
theme.addEventListener('change', () => {
  document.body.dataset.theme = theme.value;
  chrome.storage.local.set({theme: theme.value});
});
