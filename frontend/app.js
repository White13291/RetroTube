/* JavaScript ES5 para Safari antigo. Busca por APIs publicas, sem servidor nosso. */
(function () {
  "use strict";
  var message = document.getElementById("message");
  var results = document.getElementById("results");
  var watch = document.getElementById("watch");
  var query = document.getElementById("search-query");
  var player = document.getElementById("player");
  var status = document.getElementById("video-status");
  var title = document.getElementById("video-title");
  var searchSources = [
    { url: "https://pipedapi.kavin.rocks/search?q=", tail: "&filter=videos", kind: "piped" },
    { url: "https://inv.nadeko.net/api/v1/search?q=", tail: "&type=video", kind: "invidious" },
    { url: "https://pipedapi.leptons.xyz/search?q=", tail: "&filter=videos", kind: "piped" },
    { url: "https://invidious.nerdvpn.de/api/v1/search?q=", tail: "&type=video", kind: "invidious" },
    { url: "https://pipedapi.nosebs.ru/search?q=", tail: "&filter=videos", kind: "piped" },
    { url: "https://yt.chocolatemoo53.com/api/v1/search?q=", tail: "&type=video", kind: "invidious" }
  ];

  function valid(id) {
    return /^[A-Za-z0-9_-]{11}$/.test(id || "");
  }
  function param(name) {
    var match = new RegExp("[?&]" + name + "=([^&]*)").exec(location.search);
    try { return match ? decodeURIComponent(match[1].replace(/\+/g, " ")) : ""; }
    catch (error) { return ""; }
  }
  function getId(value) {
    value = String(value || "").replace(/^\s+|\s+$/g, "");
    if (valid(value)) return value;
    var short = /^(?:https?:\/\/)?(?:www\.)?youtu\.be\/([A-Za-z0-9_-]{11})(?:[?&#/]|$)/i.exec(value);
    if (short) return short[1];
    var video = /^(?:https?:\/\/)?(?:(?:www|m|music)\.)?youtube\.com\/(?:embed|shorts|live)\/([A-Za-z0-9_-]{11})(?:[?&#/]|$)/i.exec(value);
    if (video) return video[1];
    if (/^(?:https?:\/\/)?(?:(?:www|m|music)\.)?youtube\.com\/watch\?/i.test(value)) {
      var watchId = /[?&]v=([A-Za-z0-9_-]{11})(?:[&#]|$)/.exec(value);
      if (watchId) return watchId[1];
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
    query.value = "https://www.youtube.com/watch?v=" + id;
    title.textContent = param("t").slice(0, 160) || "Vídeo " + id;
    status.textContent = "Carregando vídeo diretamente do YouTube…";
    player.src = "https://www.youtube.com/embed/" + id + "?playsinline=1";
    document.getElementById("youtube-link").href = "https://m.youtube.com/watch?v=" + id;
    document.getElementById("back-to-search").href = param("q") ?
      "?q=" + encodeURIComponent(param("q")) : "./";
    document.getElementById("alternate-player").onclick = function (event) {
      if (event && event.preventDefault) event.preventDefault();
      if (event) event.returnValue = false;
      player.src = "https://www.youtube-nocookie.com/embed/" + id + "?playsinline=1";
      status.textContent = "Tentando o endereço alternativo do YouTube…";
      return false;
    };
  }
  function normalize(data, kind) {
    var input = kind === "piped" ? data && data.items : data;
    var output = [];
    var seen = {};
    var i, entry, id, match;
    if (!input || Object.prototype.toString.call(input) !== "[object Array]") return output;
    for (i = 0; i < input.length && output.length < 24; i++) {
      entry = input[i];
      if (!entry) continue;
      if (kind === "piped") {
        if (entry.type && entry.type !== "stream") continue;
        match = /[?&]v=([A-Za-z0-9_-]{11})(?:[&#]|$)/.exec(entry.url || "");
        id = match ? match[1] : "";
      } else {
        if (entry.type && entry.type !== "video" && entry.type !== "shortVideo") continue;
        id = entry.videoId;
      }
      if (!valid(id) || seen[id] || !entry.title) continue;
      seen[id] = true;
      output.push({
        id: id,
        title: String(entry.title),
        channel: String(entry.uploaderName || entry.author || "YouTube")
      });
    }
    return output;
  }
  function videoCard(parent, video, q) {
    var row = document.createElement("div");
    row.className = "item";
    var link = document.createElement("a");
    link.href = "?v=" + encodeURIComponent(video.id) +
      "&q=" + encodeURIComponent(q) + "&t=" + encodeURIComponent(video.title.slice(0, 160));
    var thumb = document.createElement("img");
    thumb.className = "thumb";
    thumb.src = "https://i.ytimg.com/vi/" + video.id + "/mqdefault.jpg";
    thumb.alt = "";
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
  function showSearch(q) {
    message.className = "hide";
    watch.className = "hide";
    results.className = "panel";
    query.value = q;
    results.innerHTML = "";
    textNode(results, "h1", "Resultados para: " + q);
    var progress = textNode(results, "p", "Buscando vídeos…");
    progress.className = "muted";
    var cards = document.createElement("div");
    results.appendChild(cards);
    var next = 0;
    var success = false;
    function batch() {
      if (success) return;
      if (next >= searchSources.length) {
        progress.textContent = "Não foi possível carregar resultados agora. Os serviços públicos de pesquisa podem estar indisponíveis ou ser incompatíveis com este Safari.";
        var external = textNode(results, "a", "Tentar pesquisa alternativa");
        external.href = "https://duckduckgo.com/?q=" + encodeURIComponent(q) + "&ia=videos&iax=videos";
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
            for (j = 0; j < items.length; j++) videoCard(cards, items[j], q);
            return;
          }
          count--;
          if (!count) batch();
        });
      }
    }
    batch();
  }
  document.getElementById("search-form").onsubmit = function (event) {
    var id = getId(query.value);
    if (!id) return true;
    if (event && event.preventDefault) event.preventDefault();
    if (event) event.returnValue = false;
    location.href = "?v=" + encodeURIComponent(id);
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
  if (valid(id)) {
    showVideo(id);
  } else if (param("q")) {
    var q = param("q").slice(0, 150);
    var pastedId = getId(q);
    if (pastedId) showVideo(pastedId);
    else showSearch(q);
  }
}());
