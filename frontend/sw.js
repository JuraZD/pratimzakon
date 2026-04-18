/* PratimZakon Service Worker — Web Push handler */

self.addEventListener('push', event => {
  const data = event.data ? event.data.json() : {};
  const title = data.title || 'PratimZakon';
  const options = {
    body: data.body || 'Novi pronalasci u Narodnim novinama',
    icon: 'https://jurazd.github.io/pratimzakon/frontend/favicon.ico',
    badge: 'https://jurazd.github.io/pratimzakon/frontend/favicon.ico',
    data: { url: data.url || 'https://jurazd.github.io/pratimzakon/frontend/dashboard.html' },
    tag: 'pratimzakon-match',
    renotify: true,
    requireInteraction: false,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url)
    ? event.notification.data.url
    : 'https://jurazd.github.io/pratimzakon/frontend/dashboard.html';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
      for (const client of clientList) {
        if (client.url === url && 'focus' in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
