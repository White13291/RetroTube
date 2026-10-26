/* Testes locais sem dependencia externa: node tests/search.test.js */
var assert = require("assert");
var fs = require("fs");
var vm = require("vm");
var path = require("path");
var source = fs.readFileSync(path.join(__dirname, "../frontend/app.js"), "utf8");

function element(tag) {
  return {
    tagName: tag || "",
    children: [],
    className: "",
    textContent: "",
    appendChild: function (child) { this.children.push(child); return child; },
    removeAttribute: function (name) { delete this[name]; },
    load: function () {},
    focus: function () {},
    setAttribute: function (name, value) { this[name] = value; }
  };
}
function descendants(node, tag) {
  var all = [];
  var i;
  if (node.tagName === tag) all.push(node);
  for (i = 0; i < (node.children || []).length; i++) {
    all = all.concat(descendants(node.children[i], tag));
  }
  return all;
}
function run(search, responses, done, hostname, storage) {
  var nodes = {};
  var ids = ["message", "results", "watch", "search-query", "player",
    "related", "related-status", "related-cards",
    "search-source",
    "video-status", "video-title", "youtube-link", "alternate-player",
    "lite-search", "search-form", "back-to-search", "api-settings",
    "api-key-input", "api-key-form", "api-key-status", "api-settings-link", "api-key-clear",
    "theme-light", "theme-dark"];
  ids.forEach(function (id) { nodes[id] = element("div"); });
  nodes["search-source"].value = "youtube";
  var saved = storage || {};
  nodes.body = element("body");
  nodes.saved = saved;
  var localStorage = {
    getItem: function (k) { return saved[k] || null; },
    setItem: function (k, v) { saved[k] = v; },
    removeItem: function (k) { delete saved[k]; }
  };
  var document = {
    getElementById: function (id) { return nodes[id]; },
    body: nodes.body,
    createElement: element,
    createTextNode: function (value) { return { tagName: "#text", textContent: String(value), children: [] }; }
  };
  function FakeXHR() {}
  FakeXHR.prototype.open = function (method, url) { this.url = url; };
  FakeXHR.prototype.send = function () {
    var self = this;
    setTimeout(function () {
      var kind = self.url.indexOf("/api/search?") === 0 ? "backend" :
        (self.url.indexOf("www.googleapis.com/youtube/v3/search") !== -1 ?
        "google" : (self.url.indexOf("/api/v1/search?q=") !== -1 ? "invidious" : "piped"));
      var response = responses[kind];
      self.readyState = 4;
      self.status = response ? 200 : 500;
      self.responseText = JSON.stringify(response || {});
      self.onreadystatechange();
    }, 1);
  };
  FakeXHR.prototype.abort = function () {};
  vm.runInNewContext(source, {
    document: document,
    location: { search: search, hostname: hostname || "" },
    XMLHttpRequest: FakeXHR,
    localStorage: localStorage,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    encodeURIComponent: encodeURIComponent,
    decodeURIComponent: decodeURIComponent
  }, { filename: "app.js" });
  setTimeout(function () { done(nodes); }, 70);
}

run("", {}, function (nodes) {
  assert.equal(nodes.body.className, "");
  assert.equal(nodes["theme-light"]["aria-pressed"], "true");
  nodes["theme-dark"].onclick();
  assert.equal(nodes.body.className, "dark");
  assert.equal(nodes.saved.retrotube_theme, "dark");
  assert.equal(nodes["theme-dark"]["aria-pressed"], "true");
  run("?demo=1", {}, function (reload) {
    assert.equal(reload.body.className, "dark");
    reload["theme-light"].onclick();
    assert.equal(reload.body.className, "");
    assert.equal(reload.saved.retrotube_theme, "light");
    console.log("OK: temas claro e escuro persistem após navegar");
  }, "137.131.147.37", nodes.saved);
});

run("?v=peertube_da880324-5efa-4ff0-8767-7697503bc746&q=brasil&source=peertube&t=Hino", {
  backend: {items: [
    {id: "peertube_da880324-5efa-4ff0-8767-7697503bc746", title: "Hino atual", channel: "Canal BR"},
    {id: "peertube_fbcf43ff-0706-4634-a9d2-ff70b2763f92", title: "Brasil em vídeo", channel: "Outro canal", thumbnail: "/api/thumb/peertube_fbcf43ff-0706-4634-a9d2-ff70b2763f92"}
  ]}
}, function (nodes) {
  assert.equal(nodes.related.className, "panel");
  assert.equal(descendants(nodes["related-cards"], "a").length, 2);
  assert(descendants(nodes["related-cards"], "a")[0].href.indexOf("?v=peertube_fbcf43ff-0706-4634-a9d2-ff70b2763f92") === 0);
  assert.equal(nodes["related-status"].textContent, "Mais vídeos sobre este assunto");
  console.log("OK: vídeos relacionados abaixo do player, sem repetir o atual");
}, "137.131.147.37");

run("?q=criacao", {
  piped: {
    items: [
      { type: "stream", url: "/watch?v=dQw4w9WgXcQ", title: "Vídeo de exemplo", uploaderName: "Canal 1" },
      { type: "channel", url: "/channel/abc", title: "Canal" }
    ]
  }
}, function (nodes) {
  assert.equal(descendants(nodes.results, "a").length, 0);
  descendants(nodes.results, "button")[1].onclick();
  setTimeout(function () {
    var links = descendants(nodes.results, "a");
    assert.equal(links.length, 2);
    assert(links[0].href.indexOf("?v=dQw4w9WgXcQ&q=criacao") === 0);
    assert.equal(descendants(nodes.results, "img")[0].src, "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg");
    run("?q=historias", {
      invidious: [{ type: "video", videoId: "dQw4w9WgXcQ", title: "Outra história", author: "Canal 2" }]
    }, function (fallbackNodes) {
      descendants(fallbackNodes.results, "button")[1].onclick();
      setTimeout(function () {
        assert.equal(descendants(fallbackNodes.results, "a").length, 2);
        run("?q=calmaria", {
          google: {
            items: [{ id: {videoId: "dQw4w9WgXcQ"}, snippet: {title: "Música calma", channelTitle: "Canal 3"} }]
          }
        }, function (googleNodes) {
          googleNodes["api-key-input"].value = "AIza12345678901234567890123456789012345";
          googleNodes["api-key-form"].onsubmit({ preventDefault: function () {} });
          setTimeout(function () {
            var googleLinks = descendants(googleNodes.results, "a");
            assert.equal(googleLinks.length, 2);
            assert(googleLinks[0].href.indexOf("?v=dQw4w9WgXcQ&q=calmaria") === 0);
            run("?v=dQw4w9WgXcQ&q=historias&t=Outra%20historia", {}, function (watchNodes) {
              assert.equal(watchNodes["video-status"].textContent.indexOf("VPS") !== -1, true);
              assert.equal(watchNodes["back-to-search"].href, "?q=historias&source=youtube");
              assert.equal(watchNodes["search-query"].value, "");
              console.log("OK: busca oficial, fallback e player local");
            });
          }, 70);
        });
      }, 70);
    });
  }, 70);
});

run("?demo=1", {}, function (demoNodes) {
  assert.equal(demoNodes.player.src, "/api/demo.mp4");
  assert.equal(demoNodes["youtube-link"].className, "hide");
  assert.equal(demoNodes.watch.className, "panel");
  console.log("OK: modo de teste sem YouTube");
});

run("?q=" + encodeURIComponent("https://inv.nadeko.net/watch?v=eWNhOwEIdRk"), {}, function (nodes) {
  assert.equal(nodes.watch.className, "panel");
  assert.equal(nodes["youtube-link"].href, "https://m.youtube.com/watch?v=eWNhOwEIdRk");
  console.log("OK: links Invidious são aceitos no player do RetroTube");
});

run("?q=" + encodeURIComponent("https://www.dailymotion.com/video/x5kesuj"), {}, function (nodes) {
  assert.equal(nodes.watch.className, "panel");
  assert.equal(nodes["youtube-link"].href, "https://www.dailymotion.com/video/x5kesuj");
  console.log("OK: links Dailymotion são aceitos no player");
});

run("?q=noticias&source=dailymotion", {backend: {items: [
  {id: "dailymotion_x5kesuj", title: "Notícias brasileiras", channel: "Canal BR", thumbnail: "https://s1.dmcdn.net/v/test"}
]}}, function (nodes) {
  var links = descendants(nodes.results, "a");
  assert.equal(links.length, 2);
  assert(links[0].href.indexOf("?v=dailymotion_x5kesuj&q=noticias&source=dailymotion") === 0);
  assert.equal(descendants(nodes.results, "img")[0].src, "https://s1.dmcdn.net/v/test");
  console.log("OK: busca Dailymotion na VPS");
}, "137.131.147.37");

run("?q=brasil&source=peertube", {backend: {items: [
  {id: "peertube_da880324-5efa-4ff0-8767-7697503bc746",
   title: "Hino Nacional Brasileiro", channel: "Canal BR",
   thumbnail: "https://tube.tchncs.de/lazy-static/thumbnails/demo.jpg",
   original_url: "https://tube.tchncs.de/videos/watch/da880324-5efa-4ff0-8767-7697503bc746"}
]}}, function (nodes) {
  var links = descendants(nodes.results, "a");
  assert.equal(links.length, 2);
  assert(links[0].href.indexOf("?v=peertube_da880324-5efa-4ff0-8767-7697503bc746&q=brasil&source=peertube") === 0);
  assert(links[0].href.indexOf("&u=https%3A%2F%2Ftube.tchncs.de") !== -1);
  run("?v=peertube_da880324-5efa-4ff0-8767-7697503bc746&source=peertube&u=" +
    encodeURIComponent("https://tube.tchncs.de/videos/watch/da880324-5efa-4ff0-8767-7697503bc746"), {}, function (watchNodes) {
    assert.equal(watchNodes.watch.className, "panel");
    assert.equal(watchNodes["youtube-link"].textContent, "Abrir no PeerTube");
    assert.equal(watchNodes["search-query"].value, "");
    console.log("OK: busca PeerTube sem API e player na VPS");
  });
}, "137.131.147.37");
