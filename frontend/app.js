/* JavaScript ES5 para Safari antigo. Na VPS, MP4 pela API local. */
(function () {
  "use strict";
  var message = document.getElementById("message");
  var results = document.getElementById("results");
  var watch = document.getElementById("watch");
  var query = document.getElementById("search-query");
  var sourceSelect = document.getElementById("search-source");
  var player = document.getElementById("player");
  var status = document.getElementById("video-status");
  var title = document.getElementById("video-title");
  var keyPanel = document.getElementById("api-settings");
  var keyInput = document.getElementById("api-key-input");
  var keyStatus = document.getElementById("api-key-status");
  var memoryKey = "";
  var keyName = "retrotube_youtube_api_key";
  var searchSources = [
    { url: "https://pipedapi.kavin.rocks/search?q=", tail: "&filter=videos", kind: "piped" },
    { url: "https://inv.nadeko.net/api/v1/search?q=", tail: "&type=video", kind: "invidious" },
    { url: "https://pipedapi.leptons.xyz/search?q=", tail: "&filter=videos", kind: "piped" },
    { url: "https://invidious.nerdvpn.de/api/v1/search?q=", tail: "&type=video", kind: "invidious" },
    { url: "https://pipedapi.nosebs.ru/search?q=", tail: "&filter=videos", kind: "piped" },
    { url: "https://yt.chocolatemoo53.com/api/v1/search?q=", tail: "&type=video", kind: "invidious" }
  ];

  function valid(id) {
    return /^(?:[A-Za-z0-9_-]{11}|dailymotion_[A-Za-z0-9]{5,14}|peertube_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$/.test(id || "");
  }
  function isDailymotion(id) {
    return /^dailymotion_[A-Za-z0-9]{5,14}$/.test(id || "");
  }
  function isPeerTube(id) {
    return /^peertube_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(id || "");
  }
  function selectedSource() {
    if (sourceSelect.value === "youtube" || sourceSelect.value === "dailymotion") return sourceSelect.value;
    return "peertube";
  }
  function getKey() {
    if (memoryKey) return memoryKey;
    try { return localStorage.getItem(keyName) || ""; }
    catch (error) { return ""; }
  }
  function saveKey(value) {
    memoryKey = value;
    try { localStorage.setItem(keyName, value); return true; }
    catch (error) { return false; }
  }
  function clearKey() {
    memoryKey = "";
    try { localStorage.removeItem(keyName); } catch (ignore) {}
  }
  function openKeySettings() {
    keyPanel.className = "api-box";
    keyStatus.textContent = getKey() ? "Uma chave já está configurada neste navegador." : "Configure sua chave para pesquisar.";
    keyInput.focus();
  }
  function param(name) {
    var match = new RegExp("[?&]" + name + "=([^&]*)").exec(location.search);
    try { return match ? decodeURIComponent(match[1].replace(/\+/g, " ")) : ""; }
    catch (error) { return ""; }
  }
  function getId(value) {
    value = String(value || "").replace(/^\s+|\s+$/g, "");
    if (valid(value)) return value;
    var daily = /^(?:https?:\/\/)?(?:(?:www|touch)\.)?dailymotion\.com\/video\/([A-Za-z0-9]{5,14})(?:[_?&#/]|$)/i.exec(value);
    if (daily) return "dailymotion_" + daily[1];
    var dailyShort = /^(?:https?:\/\/)?dai\.ly\/([A-Za-z0-9]{5,14})(?:[?&#/]|$)/i.exec(value);
    if (dailyShort) return "dailymotion_" + dailyShort[1];
    var short = /^(?:https?:\/\/)?(?:www\.)?youtu\.be\/([A-Za-z0-9_-]{11})(?:[?&#/]|$)/i.exec(value);
    if (short) return short[1];
    var video = /^(?:https?:\/\/)?(?:(?:www|m|music)\.)?youtube\.com\/(?:embed|shorts|live)\/([A-Za-z0-9_-]{11})(?:[?&#/]|$)/i.exec(value);
    if (video) return video[1];
    if (/^(?:https?:\/\/)?(?:(?:www|m|music)\.)?youtube\.com\/watch\?/i.test(value)) {
      var watchId = /[?&]v=([A-Za-z0-9_-]{11})(?:[&#]|$)/.exec(value);
      if (watchId) return watchId[1];
    }
    if (/^(?:https?:\/\/)?(?:inv\.nadeko\.net|invidious\.nerdvpn\.de|yt\.chocolatemoo53\.com|invidious\.tiekoetter\.com)\/watch\?/i.test(value)) {
      var alternateId = /[?&]v=([A-Za-z0-9_-]{11})(?:[&#]|$)/.exec(value);
      if (alternateId) return alternateId[1];
    }
    return "";
  }
  function textNode(parent, tag, value) {
    var el = document.createElement(tag);
    el.appendChild(document.createTextNode(value));
    parent.appendChild(el);
    return el;
  }
  function showVideo(id) {
    message.className = "hide";
    results.className = "hide";
    watch.className = "panel";
    var daily = isDailymotion(id);
    var peer = isPeerTube(id);
    var original = param("u");
    if (!/^https:\/\/[a-z0-9.-]+\//i.test(original)) original = "";
    query.value = peer ? (original || id) : daily ? "https://www.dailymotion.com/video/" + id.slice(12) : "https://www.youtube.com/watch?v=" + id;
    title.textContent = param("t").slice(0, 160) || "Vídeo " + id;
    status.textContent = "Preparando vídeo para o Safari antigo…";
    document.getElementById("youtube-link").className = peer && !original ? "hide" : "";
    player.removeAttribute("src");
    player.load();
    prepareVideo(id);
    document.getElementById("youtube-link").href = peer ? original : daily ?
      "https://www.dailymotion.com/video/" + id.slice(12) : "https://m.youtube.com/watch?v=" + id;
    document.getElementById("youtube-link").textContent = peer ? "Abrir no PeerTube" : daily ? "Abrir no Dailymotion" : "Abrir no YouTube";
    document.getElementById("back-to-search").href = param("q") ?
      "?q=" + encodeURIComponent(param("q")) + "&source=" + encodeURIComponent(param("source") || (peer ? "peertube" : daily ? "dailymotion" : "youtube")) : "./";
    document.getElementById("alternate-player").onclick = function (event) {
      if (event && event.preventDefault) event.preventDefault();
      if (event) event.returnValue = false;
      prepareVideo(id);
      return false;
    };
  }
  function showDemo() {
    message.className = "hide";
    results.className = "hide";
    watch.className = "panel";
    title.textContent = "Teste do player do iPad (8 segundos)";
    status.textContent = "Vídeo de teste gerado na VPS, sem acessar o YouTube. Toque em reproduzir.";
    player.src = "/api/demo.mp4";
    player.load();
    document.getElementById("youtube-link").className = "hide";
    document.getElementById("alternate-player").onclick = function (event) {
      if (event && event.preventDefault) event.preventDefault();
      if (event) event.returnValue = false;
      player.load();
      status.textContent = "Toque no botão de reprodução do vídeo.";
      return false;
    };
    document.getElementById("back-to-search").href = "./";
  }
  function backendRequest(path, callback) {
    var xhr = new XMLHttpRequest();
    try {
      xhr.open("GET", path, true);
      xhr.onreadystatechange = function () {
        if (xhr.readyState !== 4) return;
        try {
          var data = JSON.parse(xhr.responseText);
          callback(xhr.status >= 200 && xhr.status < 300 ? null : (data.error || "Erro no servidor"), data);
        } catch (error) { callback("Não foi possível conversar com a VPS.", null); }
      };
      xhr.onerror = function () { callback("Sem conexão com a VPS.", null); };
      xhr.send(null);
    } catch (error) { callback("Falha de conexão com o servidor.", null); }
  }
  function prepareVideo(id) {
    status.textContent = "Solicitando vídeo à VPS…";
    backendRequest("/api/prepare/" + id, function (error, data) {
      if (error) { status.textContent = error + ". Abra o RetroTube pelo endereço da sua VPS."; return; }
      updateVideo(id, data);
    });
  }
  function updateVideo(id, data) {
    if (!data) { status.textContent = "Servidor não respondeu."; return; }
    if (data.status === "pronto") {
      status.textContent = "Vídeo pronto! Toque no botão de reprodução.";
      player.src = "/api/video/" + id;
      player.load();
      return;
    }
    if (data.status === "erro") {
      status.textContent = "Não foi possível preparar este vídeo: " + (data.message || "erro desconhecido");
      return;
    }
    var labels = { na_fila: "Aguardando vaga", obtendo: "Consultando fonte de vídeo", baixando: "Obtendo o vídeo na VPS", alternativo: "Tentando outras fontes de vídeo", convertendo: "Convertendo para o iPad" };
    status.textContent = (labels[data.status] || "Processando") + "… Aguarde; isso pode levar alguns minutos.";
    setTimeout(function () {
      backendRequest("/api/status/" + id, function (error, latest) {
        if (error) { status.textContent = error; return; }
        updateVideo(id, latest);
      });
    }, 3500);
  }
  function normalize(data, kind) {
    var input = (kind === "piped" || kind === "google") ? data && data.items : data;
    var output = [];
    var seen = {};
    var i, entry, id, match, name, channel;
    if (!input || Object.prototype.toString.call(input) !== "[object Array]") return output;
    for (i = 0; i < input.length && output.length < 24; i++) {
      entry = input[i];
      if (!entry) continue;
      if (kind === "piped") {
        if (entry.type && entry.type !== "stream") continue;
        match = /[?&]v=([A-Za-z0-9_-]{11})(?:[&#]|$)/.exec(entry.url || "");
        id = match ? match[1] : "";
        name = entry.title;
        channel = entry.uploaderName;
      } else if (kind === "google") {
        id = entry.id && entry.id.videoId;
        name = entry.snippet && entry.snippet.title;
        channel = entry.snippet && entry.snippet.channelTitle;
      } else {
        if (entry.type && entry.type !== "video" && entry.type !== "shortVideo") continue;
        id = entry.videoId;
        name = entry.title;
        channel = entry.author;
      }
      if (!valid(id) || seen[id] || !name) continue;
      seen[id] = true;
      output.push({
        id: id,
        title: String(name),
        channel: String(channel || "YouTube")
      });
    }
    return output;
  }
  function videoCard(parent, video, q, source) {
    var row = document.createElement("div");
    row.className = "item";
    var link = document.createElement("a");
    link.href = "?v=" + encodeURIComponent(video.id) +
      "&q=" + encodeURIComponent(q) + "&source=" + encodeURIComponent(source || "youtube") +
      "&t=" + encodeURIComponent(video.title.slice(0, 160));
    if (isPeerTube(video.id) && /^https:\/\/[a-z0-9.-]+\//i.test(video.original_url || "")) {
      link.href += "&u=" + encodeURIComponent(video.original_url);
    }
    var thumb = document.createElement("img");
    thumb.className = "thumb";
    thumb.src = video.thumbnail || ((isDailymotion(video.id) || isPeerTube(video.id)) ? "logo.png" :
      "https://i.ytimg.com/vi/" + video.id + "/mqdefault.jpg");
    thumb.onerror = function () { this.onerror = null; this.src = "logo.png"; };
    thumb.alt = "Assistir: " + video.title.slice(0, 100);
    thumb.width = 155;
    thumb.height = 87;
    thumb.loading = "lazy";
    link.appendChild(thumb);
    row.appendChild(link);
    var heading = document.createElement("h2");
    var titleLink = document.createElement("a");
    titleLink.href = link.href;
    titleLink.appendChild(document.createTextNode(video.title));
    heading.appendChild(titleLink);
    row.appendChild(heading);
    var channel = textNode(row, "p", video.channel);
    channel.className = "muted";
    parent.appendChild(row);
  }
  function requestSearch(source, q, callback) {
    var xhr = new XMLHttpRequest();
    var finished = false;
    var timer = setTimeout(function () { finish(null); try { xhr.abort(); } catch (ignore) {} }, 8500);
    function finish(items) {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      callback(items);
    }
    try {
      xhr.open("GET", source.url + encodeURIComponent(q) + source.tail, true);
      xhr.onreadystatechange = function () {
        if (xhr.readyState !== 4) return;
        if (xhr.status < 200 || xhr.status >= 300) { finish(null); return; }
        try { finish(normalize(JSON.parse(xhr.responseText), source.kind)); }
        catch (error) { finish(null); }
      };
      xhr.onerror = function () { finish(null); };
      xhr.send(null);
    } catch (error) { finish(null); }
  }
  function requestGoogle(q, key, callback) {
    var xhr = new XMLHttpRequest();
    var finished = false;
    var timer = setTimeout(function () {
      finish(null, "A busca demorou demais. Verifique sua conexão e tente novamente.");
      try { xhr.abort(); } catch (ignore) {}
    }, 12000);
    function finish(items, error) {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      callback(items, error);
    }
    var url = "https://www.googleapis.com/youtube/v3/search?part=snippet&type=video" +
      "&maxResults=24&videoEmbeddable=true&q=" + encodeURIComponent(q) +
      "&key=" + encodeURIComponent(key);
    try {
      xhr.open("GET", url, true);
      xhr.onreadystatechange = function () {
        if (xhr.readyState !== 4) return;
        if (xhr.status === 200) {
          try { finish(normalize(JSON.parse(xhr.responseText), "google"), ""); }
          catch (error) { finish(null, "A resposta do YouTube não pôde ser lida."); }
        } else if (xhr.status === 400) {
          finish(null, "O Google recusou a chave ou a solicitação. Confira se ela está correta.");
        } else if (xhr.status === 403) {
          finish(null, "O Google bloqueou a solicitação: ative a YouTube Data API v3, confira as restrições da chave e a cota diária.");
        } else {
          finish(null, "Não foi possível consultar a API do Google neste navegador.");
        }
      };
      xhr.onerror = function () { finish(null, "Falha de conexão com a API do Google."); };
      xhr.send(null);
    } catch (error) { finish(null, "O Safari não conseguiu abrir a conexão com a API."); }
  }
  function searchWithPublicSources(q, progress, cards) {
    progress.textContent = "Tentando serviços públicos de busca…";
    var next = 0;
    var success = false;
    function batch() {
      if (success) return;
      if (next >= searchSources.length) {
        progress.textContent = "Os serviços públicos não retornaram resultados. Configure uma chave oficial do YouTube para pesquisar com mais confiabilidade.";
        return;
      }
      var start = next;
      var count = 0;
      next = Math.min(next + 2, searchSources.length);
      var i;
      for (i = start; i < next; i++) {
        count++;
        requestSearch(searchSources[i], q, function (items) {
          if (success) return;
          if (items && items.length) {
            success = true;
            progress.textContent = items.length + " vídeos encontrados. Toque para assistir no RetroTube.";
            var j;
            for (j = 0; j < items.length; j++) videoCard(cards, items[j], q, "youtube");
            return;
          }
          count--;
          if (!count) batch();
        });
      }
    }
    batch();
  }
  function addPublicButton(parent, progress, cards, q) {
    var button = textNode(parent, "button", "Tentar busca pública (experimental)");
    button.type = "button";
    button.onclick = function () {
      button.className = "hide";
      searchWithPublicSources(q, progress, cards);
    };
  }
  function showSearch(q) {
    message.className = "hide";
    watch.className = "hide";
    results.className = "panel";
    query.value = q;
    var source = param("source") || selectedSource();
    if (source !== "youtube" && source !== "dailymotion") source = "peertube";
    sourceSelect.value = source;
    results.innerHTML = "";
    textNode(results, "h1", "Resultados para: " + q);
    var progress = textNode(results, "p", "Preparando pesquisa…");
    progress.className = "muted";
    var cards = document.createElement("div");
    results.appendChild(cards);
    if (location.hostname && location.hostname !== "white13291.github.io") {
      progress.textContent = "Buscando vídeos pela VPS…";
      backendRequest("/api/search?q=" + encodeURIComponent(q) + "&source=" + encodeURIComponent(source), function (error, data) {
        if (error) {
          progress.textContent = error + (source === "youtube" ? ". Você também pode configurar a busca do YouTube." : "");
          if (source === "youtube") addPublicButton(results, progress, cards, q);
          return;
        }
        var items = data && data.items ? data.items : [];
        progress.textContent = items.length ? items.length + " vídeos encontrados. Toque para assistir." : "Nenhum vídeo encontrado.";
        for (var k = 0; k < items.length; k++) videoCard(cards, items[k], q, source);
      });
      return;
    }
    if (source !== "youtube") {
      progress.textContent = "A busca do " + (source === "peertube" ? "PeerTube" : "Dailymotion") + " funciona pelo endereço da sua VPS. Abra o RetroTube na VPS para pesquisar.";
      return;
    }
    var key = getKey();
    if (!key) {
      progress.textContent = "Para usar a pesquisa oficial, configure sua chave da YouTube Data API v3 no topo da página.";
      var setup = textNode(results, "button", "Configurar chave do YouTube");
      setup.type = "button";
      setup.onclick = openKeySettings;
      textNode(results, "p", " ");
      addPublicButton(results, progress, cards, q);
      return;
    }
    progress.textContent = "Buscando vídeos no YouTube…";
    requestGoogle(q, key, function (items, error) {
      if (error) {
        progress.textContent = error;
        var change = textNode(results, "button", "Verificar ou trocar chave");
        change.type = "button";
        change.onclick = openKeySettings;
        textNode(results, "p", " ");
        addPublicButton(results, progress, cards, q);
      } else if (!items || !items.length) {
        progress.textContent = "Nenhum vídeo encontrado para essa pesquisa.";
      } else {
        progress.textContent = items.length + " vídeos encontrados. Toque para assistir no RetroTube.";
        var i;
        for (i = 0; i < items.length; i++) videoCard(cards, items[i], q, "youtube");
      }
    });
  }
  document.getElementById("api-settings-link").onclick = function (event) {
    if (event && event.preventDefault) event.preventDefault();
    if (event) event.returnValue = false;
    if (keyPanel.className === "api-box") keyPanel.className = "api-box hide";
    else openKeySettings();
    return false;
  };
  document.getElementById("api-key-form").onsubmit = function (event) {
    if (event && event.preventDefault) event.preventDefault();
    if (event) event.returnValue = false;
    var key = String(keyInput.value || "").replace(/^\s+|\s+$/g, "");
    if (!/^[A-Za-z0-9_-]{20,}$/.test(key)) {
      keyStatus.textContent = "A chave parece inválida. Confira o valor no Google Cloud.";
      return false;
    }
    var persisted = saveKey(key);
    keyInput.value = "";
    keyStatus.textContent = persisted ? "Chave salva somente neste navegador." :
      "Chave guardada apenas nesta sessão; o Safari bloqueou o armazenamento local.";
    var q = param("q").slice(0, 150);
    if (q && !getId(q)) showSearch(q);
    return false;
  };
  document.getElementById("api-key-clear").onclick = function (event) {
    if (event && event.preventDefault) event.preventDefault();
    if (event) event.returnValue = false;
    clearKey();
    keyInput.value = "";
    keyStatus.textContent = "Chave removida deste navegador.";
    return false;
  };
  document.getElementById("search-form").onsubmit = function (event) {
    var id = getId(query.value);
    if (event && event.preventDefault) event.preventDefault();
    if (event) event.returnValue = false;
    if (id) {
      location.href = "?v=" + encodeURIComponent(id);
      return false;
    }
    var q = String(query.value || "").replace(/^\s+|\s+$/g, "").slice(0, 150);
    if (!q) return false;
    try {
      if (typeof history !== "undefined" && history.pushState) {
        history.pushState(null, "", "?q=" + encodeURIComponent(q) + "&source=" + encodeURIComponent(selectedSource()));
      }
    } catch (ignore) {}
    showSearch(q);
    return false;
  };
  document.getElementById("lite-search").onclick = function (event) {
    if (event && event.preventDefault) event.preventDefault();
    if (event) event.returnValue = false;
    var term = query.value.replace(/^\s+|\s+$/g, "");
    if (!term) { query.focus(); return false; }
    location.href = "https://lite.duckduckgo.com/lite/?q=" +
      encodeURIComponent(term + " site:youtube.com");
    return false;
  };
  var id = param("v");
  if (param("demo") === "1") {
    showDemo();
  } else if (valid(id)) {
    showVideo(id);
  } else if (param("q")) {
    var q = param("q").slice(0, 150);
    var pastedId = getId(q);
    if (pastedId) showVideo(pastedId);
    else showSearch(q);
  }
}());
