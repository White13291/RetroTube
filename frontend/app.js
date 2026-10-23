/* Intentionally ES5: Safari on iOS 8.3 supports this, but not modern JS modules/fetch. */
(function () {
  "use strict";
  var api = (window.RETROTUBE_API || "").replace(/\/+$/, "");
  var message = document.getElementById("message");
  var results = document.getElementById("results");
  var watch = document.getElementById("watch");
  var queryField = document.getElementById("search-query");
  var statusEl = document.getElementById("video-status");
  var titleEl = document.getElementById("video-title");
  var player = document.getElementById("player");

  function param(name) {
    var match = new RegExp("[?&]" + name + "=([^&]*)").exec(window.location.search);
    return match ? decodeURIComponent(match[1].replace(/\+/g, " ")) : "";
  }
  function validId(id) { return /^[A-Za-z0-9_-]{11}$/.test(id || ""); }
  function fromLink(text) {
    var match = text.match(/(?:youtube\.com\/(?:watch\?[^#]*?v=|shorts\/|embed\/)|youtu\.be\/)([A-Za-z0-9_-]{11})/i);
    if (validId(text)) return text;
    return match ? match[1] : "";
  }
  function ajax(path, callback) {
    var xhr = new XMLHttpRequest();
    xhr.open("GET", api + path, true);
    xhr.timeout = 25000;
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) return;
      var data = null;
      try { data = JSON.parse(xhr.responseText); } catch (error) {}
      if (xhr.status >= 200 && xhr.status < 300 && data) callback(null, data);
      else callback((data && data.error) || "O servidor não respondeu. Tente novamente.");
    };
    xhr.ontimeout = function () { callback("O servidor demorou demais."); };
    xhr.onerror = function () { callback("Não foi possível conectar ao servidor de vídeos."); };
    xhr.send();
  }
  function showError(text) {
    message.className = "panel";
    message.innerHTML = "";
    var h = document.createElement("h1");
    h.appendChild(document.createTextNode("Não foi possível carregar"));
    var p = document.createElement("p");
    p.appendChild(document.createTextNode(text));
    message.appendChild(h);
    message.appendChild(p);
  }
  function showResults(q) {
    queryField.value = q;
    message.className = "hide";
    results.className = "panel";
    results.innerHTML = "<h1>Pesquisando…</h1>";
    ajax("/api/search?q=" + encodeURIComponent(q), function (err, data) {
      if (err) { results.className = "hide"; showError(err); return; }
      results.innerHTML = "";
      var h = document.createElement("h1");
      h.appendChild(document.createTextNode("Resultados para: " + q));
      results.appendChild(h);
      var items = data.items || [];
      if (!items.length) {
        var empty = document.createElement("p");
        empty.appendChild(document.createTextNode("Nenhum vídeo encontrado."));
        results.appendChild(empty);
      }
      for (var i = 0; i < items.length; i++) {
        if (!validId(items[i].id)) continue;
        var row = document.createElement("div");
        row.className = "item";
        var link = document.createElement("a");
        link.href = "?v=" + encodeURIComponent(items[i].id);
        var image = document.createElement("img");
        image.className = "thumb";
        image.alt = "";
        image.src = api + "/api/thumb/" + items[i].id;
        link.appendChild(image);
        row.appendChild(link);
        var title = document.createElement("h2");
        var textLink = document.createElement("a");
        textLink.href = link.href;
        textLink.appendChild(document.createTextNode(items[i].title || "Vídeo"));
        title.appendChild(textLink);
        row.appendChild(title);
        var subtitle = document.createElement("div");
        subtitle.className = "muted";
        subtitle.appendChild(document.createTextNode(items[i].channel || ""));
        row.appendChild(subtitle);
        results.appendChild(row);
      }
    });
  }
  function showWatch(id) {
    message.className = "hide";
    watch.className = "panel";
    titleEl.innerHTML = "";
    titleEl.appendChild(document.createTextNode("Vídeo " + id));
    function poll() {
      ajax("/api/status/" + id, function (err, data) {
        if (err) { statusEl.textContent = err; return; }
        if (data.title) titleEl.textContent = data.title;
        if (data.status === "pronto") {
          statusEl.textContent = "Pronto! Toque no botão de reproduzir.";
          player.className = "player";
          player.src = api + "/api/video/" + id;
          player.load();
        } else if (data.status === "erro") {
          statusEl.textContent = data.message || "Não foi possível preparar o vídeo.";
        } else {
          statusEl.textContent = "Preparando: " + data.status + ". Esta página atualiza automaticamente.";
          window.setTimeout(poll, 4000);
        }
      });
    }
    statusEl.textContent = "Solicitando vídeo ao servidor…";
    ajax("/api/prepare/" + id, function (err) {
      if (err) { statusEl.textContent = err; return; }
      poll();
    });
  }

  document.getElementById("search-form").onsubmit = function (event) {
    var id = fromLink(queryField.value);
    if (id) {
      if (event.preventDefault) event.preventDefault();
      event.returnValue = false;
      window.location.href = "?v=" + encodeURIComponent(id);
      return false;
    }
    return true;
  };
  if (!api || api.indexOf("https://") !== 0) {
    showError("O endereço HTTPS do servidor de vídeos ainda não foi configurado.");
  } else if (validId(param("v"))) {
    showWatch(param("v"));
  } else if (param("q")) {
    showResults(param("q").slice(0, 90));
  }
}());
