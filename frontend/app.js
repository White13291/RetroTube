/* ES5 for older Safari; YouTube itself supplies the video player. */
(function () {
  "use strict";
  var message = document.getElementById("message");
  var results = document.getElementById("results");
  var watch = document.getElementById("watch");
  var query = document.getElementById("search-query");
  var player = document.getElementById("player");
  var status = document.getElementById("video-status");
  var title = document.getElementById("video-title");

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
    title.textContent = "Vídeo " + id;
    status.textContent = "Carregando vídeo diretamente do YouTube…";
    player.src = "https://www.youtube.com/embed/" + id + "?playsinline=1";
    document.getElementById("youtube-link").href = "https://m.youtube.com/watch?v=" + id;
    document.getElementById("alternate-player").onclick = function (event) {
      if (event && event.preventDefault) event.preventDefault();
      if (event) event.returnValue = false;
      player.src = "https://www.youtube-nocookie.com/embed/" + id + "?playsinline=1";
      status.textContent = "Tentando o endereço alternativo do YouTube…";
      return false;
    };
  }
  function showSearch(q) {
    message.className = "hide";
    watch.className = "hide";
    results.className = "panel";
    query.value = q;
    results.innerHTML = "";
    textNode(results, "h1", "Busca de vídeos");
    textNode(results, "p", "Somente com GitHub Pages não é possível pesquisar automaticamente todos os vídeos do YouTube. Encontre o vídeo no YouTube e cole o link aqui.");
    var link = textNode(results, "a", "Pesquisar no YouTube por: " + q);
    link.href = "https://www.youtube.com/results?search_query=" + encodeURIComponent(q);
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    textNode(results, "p", "A pesquisa do YouTube pode exigir um navegador mais recente.");
  }
  document.getElementById("search-form").onsubmit = function (event) {
    var id = getId(query.value);
    if (!id) return true;
    if (event && event.preventDefault) event.preventDefault();
    if (event) event.returnValue = false;
    location.href = "?v=" + encodeURIComponent(id);
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
