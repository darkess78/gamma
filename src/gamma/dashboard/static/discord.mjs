// Owner dashboard controls for the configured Discord bot.
if (window.GAMMA_DASHBOARD_PAGE === 'presence') {
  const status = document.getElementById('discordBotStatus');
  const message = document.getElementById('discordBotMessage');
  const controls = document.getElementById('discordBotControls');
  let busy = false;
  async function request(url, options) {
    const response = await fetch(url, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Request failed');
    return result;
  }
  async function refresh() {
    try {
      const result = await request('/api/discord/text-worker/status');
      const connected = result.summary?.connected === true;
      status.textContent = result.summary?.label || 'Status unverified';
      for (const button of controls.querySelectorAll('button')) {
        button.disabled = busy || (button.dataset.voiceCommand && !connected);
      }
    } catch (error) { status.textContent = error.message; }
  }
  controls.addEventListener('click', async (event) => {
    const button = event.target.closest('button');
    if (!button || busy) return;
    busy = true;
    await refresh();
    try {
      if (button.dataset.botAction) {
        const result = await request(`/api/discord/text-worker/${button.dataset.botAction}`, {method: 'POST'});
        message.textContent = result.ok ? 'Bot action completed.' : result.detail || 'Bot action failed.';
      } else {
        let result = await request('/api/discord/voice/control', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({command: button.dataset.voiceCommand})
        });
        message.textContent = 'Waiting for Shana to confirm the command...';
        for (let i = 0; i < 40 && ['queued', 'running'].includes(result.status); i++) {
          await new Promise(resolve => setTimeout(resolve, 500));
          result = await request(`/api/discord/voice/commands/${encodeURIComponent(result.id)}`);
        }
        message.textContent = result.status === 'completed' ?
          (result.result?.ok ? `Voice ${result.result.state || 'command completed'}` : result.result?.reason || 'Voice command failed') :
          'Command outcome is still pending. Refresh status before trying again.';
      }
    } catch (error) { message.textContent = error.message; }
    finally { busy = false; await refresh(); }
  });
  refresh();
  setInterval(refresh, 3000);
}
