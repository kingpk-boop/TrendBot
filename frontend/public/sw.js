/* TrendBot no longer uses offline caching: an old saved copy of the site could stop it from starting
   after an update. This worker replaces any older one, deletes its saved copies, removes itself and
   reloads open TrendBot tabs so they get the current site. */
self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    for (const key of await caches.keys()) await caches.delete(key);
    await self.registration.unregister();
    for (const client of await self.clients.matchAll({ type: "window" })) client.navigate(client.url);
  })());
});
