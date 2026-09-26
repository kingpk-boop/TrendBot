/* TrendBot service worker: keeps the app shell available, never caches trading data. */
const CACHE = "trendbot-shell-v1";
const SHELL = ["./", "index.html", "app.js", "styles.css", "manifest.webmanifest",
  "icons/icon.svg", "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  if (url.pathname.startsWith("/api/")) {
    // Live data: always the network. Offline gets a clear error instead of stale numbers.
    e.respondWith(fetch(req).catch(() => new Response(
      JSON.stringify({ detail: "Can't reach the TrendBot app. Is it still running on your computer?" }),
      { status: 503, headers: { "Content-Type": "application/json" } })));
    return;
  }

  // App shell: network first (so updates show up straight away), cache as the fallback.
  e.respondWith(fetch(req).then(res => {
    if (res.ok) {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(req, copy));
    }
    return res;
  }).catch(() => caches.match(req).then(hit => hit || caches.match("index.html"))));
});
