/*
 * Service worker do Comparador de Preços.
 *
 * Existe só para que Chrome/Edge permitam instalar o site como app (atalho na
 * área de trabalho). Não guarda nada em cache: toda requisição vai à rede, para
 * que os preços exibidos sejam sempre os atuais.
 */

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(fetch(event.request));
});
