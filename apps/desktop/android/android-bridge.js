// VITALIS phone app: implements the renderer's `window.vitalis` interface (normally provided by Electron's preload)
// over the app's origin-restricted message channel (AndroidX WebViewCompat.addWebMessageListener, object
// `VitalisAndroid`, allowed only for https://appassets.androidplatform.net). Loaded before the app bundle.
(function () {
  "use strict";
  var native = window.VitalisAndroid;
  var pending = new Map();
  var nextId = 1;
  var listeners = { "engine:event": new Set(), "ai:event": new Set(), "phone:event": new Set() };
  window.__VITALIS_PLATFORM__ = "android";
  if (native) {
    native.onmessage = function (ev) {
      var m = JSON.parse(ev.data);
      if (m.kind === "resolve") {
        var done = pending.get(m.id);
        if (done) { pending.delete(m.id); done(m.result); }
      } else if (m.kind === "emit") {
        var set = listeners[m.channel];
        if (set) set.forEach(function (cb) { try { cb(m.payload); } catch (e) { console.error(e); } });
      }
    };
    native.postMessage(JSON.stringify({ kind: "hello" })); // lets the app address events to this page
  }
  window.vitalis = {
    invoke: function (channel, payload) {
      if (!native) return Promise.resolve({ ok: false, error: { code: "no_bridge", message: "Android bridge unavailable" } });
      return new Promise(function (resolve) {
        var id = nextId++;
        pending.set(id, resolve);
        native.postMessage(JSON.stringify({ kind: "invoke", id: id, channel: String(channel), payload: payload === undefined ? null : payload }));
      });
    },
    on: function (channel, cb) {
      var set = listeners[channel];
      if (!set) throw new Error("event not allowed: " + channel);
      set.add(cb);
      return function () { set.delete(cb); };
    },
    reportReady: function () { if (native) native.postMessage(JSON.stringify({ kind: "reportReady" })); },
  };
})();
