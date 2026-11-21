// VITALIS phone app: implements the renderer's `window.vitalis` interface (normally provided by Electron's preload)
// on top of the Android app's JavaScript interface `VitalisAndroid`. Loaded before the app bundle.
(function () {
  "use strict";
  var native = window.VitalisAndroid;
  var pending = new Map();
  var nextId = 1;
  var listeners = { "engine:event": new Set(), "ai:event": new Set() };
  window.__VITALIS_PLATFORM__ = "android";
  // Called by the app (Kotlin) with JSON strings.
  window.__vitalisNative = {
    resolve: function (id, json) {
      var done = pending.get(id);
      if (done) { pending.delete(id); done(JSON.parse(json)); }
    },
    emit: function (channel, json) {
      var set = listeners[channel];
      if (set) set.forEach(function (cb) { try { cb(JSON.parse(json)); } catch (e) { console.error(e); } });
    },
  };
  window.vitalis = {
    invoke: function (channel, payload) {
      if (!native) return Promise.resolve({ ok: false, error: { code: "no_bridge", message: "Android bridge unavailable" } });
      return new Promise(function (resolve) {
        var id = nextId++;
        pending.set(id, resolve);
        native.invoke(id, String(channel), JSON.stringify(payload === undefined ? null : payload));
      });
    },
    on: function (channel, cb) {
      var set = listeners[channel];
      if (!set) throw new Error("event not allowed: " + channel);
      set.add(cb);
      return function () { set.delete(cb); };
    },
    reportReady: function () {},
  };
})();
