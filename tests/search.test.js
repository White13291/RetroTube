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
    focus: function () {}
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
function run(search, responses, done) {
  var nodes = {};
  var ids = ["message", "results", "watch", "search-query", "player",
    "video-status", "video-title", "youtube-link", "alternate-player",
    "lite-search", "search-form", "back-to-search"];
  ids.forEach(function (id) { nodes[id] = element("div"); });
  var document = {
    getElementById: function (id) { return nodes[id]; },
    createElement: element,
    createTextNode: function (value) { return { tagName: "#text", textContent: String(value), children: [] }; }
  };
  function FakeXHR() {}
  FakeXHR.prototype.open = function (method, url) { this.url = url; };
  FakeXHR.prototype.send = function () {
    var self = this;
    setTimeout(function () {
      var isPiped = self.url.indexOf("/api/v1/search?q=") === -1;
      var response = isPiped ? responses.piped : responses.invidious;
      self.readyState = 4;
      self.status = response ? 200 : 500;
      self.responseText = JSON.stringify(response || {});
      self.onreadystatechange();
    }, 1);
  };
  FakeXHR.prototype.abort = function () {};
  vm.runInNewContext(source, {
    document: document,
    location: { search: search },
    XMLHttpRequest: FakeXHR,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    encodeURIComponent: encodeURIComponent,
    decodeURIComponent: decodeURIComponent
  }, { filename: "app.js" });
  setTimeout(function () { done(nodes); }, 70);
}

run("?q=criacao", {
  piped: {
    items: [
      { type: "stream", url: "/watch?v=dQw4w9WgXcQ", title: "Vídeo de exemplo", uploaderName: "Canal 1" },
      { type: "channel", url: "/channel/abc", title: "Canal" }
    ]
  }
}, function (nodes) {
  var links = descendants(nodes.results, "a");
  assert.equal(links.length, 2);
  assert(links[0].href.indexOf("?v=dQw4w9WgXcQ&q=criacao") === 0);
  assert.equal(descendants(nodes.results, "img")[0].src, "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg");
  run("?q=historias", {
    invidious: [{ type: "video", videoId: "dQw4w9WgXcQ", title: "Outra história", author: "Canal 2" }]
  }, function (fallbackNodes) {
    assert.equal(descendants(fallbackNodes.results, "a").length, 2);
    run("?v=dQw4w9WgXcQ&q=historias&t=Outra%20historia", {}, function (watchNodes) {
      assert.equal(watchNodes.player.src, "https://www.youtube.com/embed/dQw4w9WgXcQ?playsinline=1");
      assert.equal(watchNodes["back-to-search"].href, "?q=historias");
      console.log("OK: busca interna, fallback e player local");
    });
  });
});
