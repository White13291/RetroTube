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
    "lite-search", "search-form", "back-to-search", "api-settings",
    "api-key-input", "api-key-form", "api-key-status", "api-settings-link", "api-key-clear"];
  ids.forEach(function (id) { nodes[id] = element("div"); });
  var saved = {};
  var localStorage = {
    getItem: function (k) { return saved[k] || null; },
    setItem: function (k, v) { saved[k] = v; },
    removeItem: function (k) { delete saved[k]; }
  };
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
      var kind = self.url.indexOf("www.googleapis.com/youtube/v3/search") !== -1 ?
        "google" : (self.url.indexOf("/api/v1/search?q=") !== -1 ? "invidious" : "piped");
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
    location: { search: search },
    XMLHttpRequest: FakeXHR,
    localStorage: localStorage,
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
              assert.equal(watchNodes["back-to-search"].href, "?q=historias");
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
