// Service Worker for Mai QRCode Getter PWA
const CACHE = 'maiqr-v1';

// 静态资源：安装时预缓存，后续 cache-first
const STATIC_ASSETS = [
    '/',
    '/qrc',
    '/static/js/qrcode.min.js',
    '/static/manifest.json',
];

// 安装：预缓存核心静态资源
self.addEventListener('install', function(e) {
    e.waitUntil(
        caches.open(CACHE).then(function(c) {
            return Promise.allSettled(STATIC_ASSETS.map(function(u) {
                return c.add(u).catch(function() { /* 忽略单个失败 */ });
            }));
        }).then(function() {
            return self.skipWaiting();
        })
    );
});

// 激活：清理旧缓存
self.addEventListener('activate', function(e) {
    e.waitUntil(
        caches.keys().then(function(keys) {
            return Promise.all(
                keys.filter(function(k) { return k !== CACHE; })
                    .map(function(k) { return caches.delete(k); })
            );
        }).then(function() {
            return self.clients.claim();
        })
    );
});

// 请求拦截
self.addEventListener('fetch', function(e) {
    var url = new URL(e.request.url);
    var path = url.pathname;

    // 跳过非 GET 请求
    if (e.request.method !== 'GET') return;

    // /maimai API：网络优先，失败时不缓存
    if (path === '/maimai') {
        e.respondWith(
            fetch(e.request).catch(function() {
                return new Response(
                    JSON.stringify({ success: false, error: '离线状态，无法获取二维码' }),
                    { status: 503, headers: { 'Content-Type': 'application/json' } }
                );
            })
        );
        return;
    }

    // 静态资源：cache-first
    if (isStaticAsset(path)) {
        e.respondWith(
            caches.match(e.request).then(function(cached) {
                return cached || fetch(e.request).then(function(resp) {
                    if (resp.ok) {
                        var clone = resp.clone();
                        caches.open(CACHE).then(function(c) { c.put(e.request, clone); });
                    }
                    return resp;
                });
            })
        );
        return;
    }

    // HTML 页面：network-first，失败时用缓存
    e.respondWith(
        fetch(e.request).catch(function() {
            return caches.match(e.request).then(function(cached) {
                return cached || caches.match('/');
            });
        })
    );
});

function isStaticAsset(path) {
    return /\.(css|js|png|jpg|jpeg|gif|ico|svg|webp|avif|woff2?|json)$/i.test(path) &&
           path.indexOf('/static/') !== -1;
}
