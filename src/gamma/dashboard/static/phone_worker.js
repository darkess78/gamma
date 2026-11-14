/* Notification click bridge only. No push, fetch handler, cache or stored conversation. */
'use strict';
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const target = new URL('/dashboard/phone', self.location.origin).href;
    const windows = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    for (const client of windows) {
      let url;
      try { url = new URL(client.url); } catch (_) { continue; }
      if (url.origin === self.location.origin && url.pathname === '/dashboard/phone') return client.focus();
    }
    return self.clients.openWindow(target);
  })().catch(() => undefined));
});
