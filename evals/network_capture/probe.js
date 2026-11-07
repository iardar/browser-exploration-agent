// Fixed fixture code only: the agent is never given an arbitrary-JavaScript tool.
(async () => {
  const {mode, token, fingerprint} = window.probe;
  const key = "network-eval-isolation";
  const heading = document.getElementById("status");
  try {
    const database = await new Promise((resolve, reject) => {
      const request = indexedDB.open(key, 1);
      request.onupgradeneeded = () => request.result.createObjectStore("markers");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const stored = await new Promise((resolve, reject) => {
      const request = database.transaction("markers").objectStore("markers").get(key);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const cachePresent = (await caches.keys()).includes(key);
    const dirty = document.cookie.includes(key + "=") || localStorage.getItem(key) !== null ||
      sessionStorage.getItem(key) !== null || stored !== undefined || cachePresent;
    if (mode === "seed") {
      document.cookie = key + "=" + token + "; Path=/; SameSite=Strict";
      localStorage.setItem(key, token);
      sessionStorage.setItem(key, token);
      await new Promise((resolve, reject) => {
        const transaction = database.transaction("markers", "readwrite");
        transaction.objectStore("markers").put(token, key);
        transaction.oncomplete = resolve;
        transaction.onerror = () => reject(transaction.error);
      });
      const cache = await caches.open(key);
      await cache.put("/__eval__/cache", new Response(token));
      await fetch("/__eval__/traffic/" + token, {cache: "no-store"});
    }
    database.close();
    heading.textContent = "Isolation " + (mode === "seed" ? "seeded" : dirty ? "dirty" : "clean") +
      " " + token + " " + fingerprint;
  } catch (_) {
    heading.textContent = "Isolation error " + token;
  }
})();
