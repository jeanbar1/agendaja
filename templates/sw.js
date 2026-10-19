{% load static %}/*
 * Service worker do AgendaJá.
 *
 * Ele fica entre o navegador e a rede, e e o que faz o sistema poder
 * ser instalado no celular (RNF02).
 *
 * A decisao mais importante aqui e a estrategia de cache, e ela nao e
 * a mesma pra tudo:
 *
 *   - Arquivo estatico (CSS, JS, icone): pega do cache primeiro. Nao
 *     muda quase nunca e carrega instantaneo.
 *
 *   - Pagina: vai na rede primeiro, SEMPRE. Se eu guardasse a agenda em
 *     cache, o profissional podia abrir o aplicativo e ver a lista de
 *     ontem, com um horario que ja foi cancelado. Mostrar informacao
 *     velha de agenda e pior que nao mostrar nada - por isso o cache de
 *     pagina so entra quando a rede falha de vez, e ai eu mando a tela
 *     de offline avisando.
 *
 * Esse arquivo e servido em /sw.js, na raiz, e nao em /static/. Tem que
 * ser assim: o service worker so controla o que esta na pasta dele ou
 * abaixo. Se ficasse em /static/js/, controlaria so o /static/js/.
 */

const VERSAO = "agendaja-v1";
const CACHE_ESTATICO = VERSAO + "-estatico";
const CACHE_PAGINAS = VERSAO + "-paginas";

// O "esqueleto" do aplicativo: o que precisa estar guardado pra tela de
// offline conseguir aparecer mesmo sem rede nenhuma.
const ESSENCIAIS = [
  "{% static 'css/estilo.css' %}",
  "{% static 'js/app.js' %}",
  "{% static 'icones/icone-192.png' %}",
  "/offline/",
];

// Nao mexo em nada disso. O admin e area de administracao e o webhook e
// a Meta chamando o servidor - cache ali so atrapalharia.
const FORA_DO_CACHE = ["/admin/", "/webhook/"];


self.addEventListener("install", (evento) => {
  evento.waitUntil(
    caches.open(CACHE_ESTATICO)
      .then((cache) => cache.addAll(ESSENCIAIS))
      // skipWaiting faz a versao nova assumir na hora, sem esperar o
      // usuario fechar todas as abas.
      .then(() => self.skipWaiting())
  );
});


self.addEventListener("activate", (evento) => {
  // Apaga os caches das versoes antigas. Sem isso eles iam se acumulando
  // no celular a cada atualizacao.
  evento.waitUntil(
    caches.keys()
      .then((nomes) => Promise.all(
        nomes
          .filter((nome) => !nome.startsWith(VERSAO))
          .map((nome) => caches.delete(nome))
      ))
      .then(() => self.clients.claim())
  );
});


self.addEventListener("fetch", (evento) => {
  const requisicao = evento.request;
  const url = new URL(requisicao.url);

  // So mexo em GET. POST e formulario sendo enviado - se eu guardasse
  // em cache, daria pra gravar o mesmo agendamento duas vezes.
  if (requisicao.method !== "GET") { return; }

  // Nada de outro site.
  if (url.origin !== self.location.origin) { return; }

  if (FORA_DO_CACHE.some((caminho) => url.pathname.startsWith(caminho))) {
    return;
  }

  // Arquivo estatico: cache primeiro.
  if (url.pathname.startsWith("/static/")) {
    evento.respondWith(
      caches.match(requisicao).then((guardado) => {
        if (guardado) { return guardado; }
        return fetch(requisicao).then((resposta) => {
          if (resposta.ok) {
            const copia = resposta.clone();
            caches.open(CACHE_ESTATICO).then((c) => c.put(requisicao, copia));
          }
          return resposta;
        });
      })
    );
    return;
  }

  // Pagina: rede primeiro, cache so como ultimo recurso.
  if (requisicao.mode === "navigate") {
    evento.respondWith(
      fetch(requisicao)
        .then((resposta) => {
          if (resposta.ok) {
            const copia = resposta.clone();
            caches.open(CACHE_PAGINAS).then((c) => c.put(requisicao, copia));
          }
          return resposta;
        })
        .catch(() =>
          // Sem rede. Tento a ultima versao que eu vi dessa pagina; se
          // nunca vi, mando a tela de offline.
          caches.match(requisicao).then(
            (guardado) => guardado || caches.match("/offline/")
          )
        )
    );
  }
});