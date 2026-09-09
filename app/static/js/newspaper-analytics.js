/* Tiny first-party newspaper analytics beacon helper.
 *
 * reader_id is a random UUID generated once per browser and kept in
 * localStorage -- it identifies a BROWSER, never a person, is never sent
 * anywhere except these beacons, and any visitor can reset it any time
 * by clearing site data. See app/analytics.py for exactly what the
 * server does (and does not) do with it.
 *
 * Uses navigator.sendBeacon when available (reliable even during page
 * unload, which is exactly when the session-summary beacon fires), with
 * a fetch(..., {keepalive:true}) fallback for older browsers.
 */
window.EHAnalytics = (function () {
  function getReaderId() {
    try {
      let id = localStorage.getItem("eh_reader_id");
      if (!id) {
        id = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
        localStorage.setItem("eh_reader_id", id);
      }
      return id;
    } catch (e) {
      return null;
    }
  }

  function send(issueId, eventType, extra) {
    try {
      const url = "/api/gazete/" + encodeURIComponent(issueId) + "/analitik/" + eventType;
      const body = JSON.stringify(extra || {});
      if (navigator.sendBeacon) {
        navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
      } else {
        fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true });
      }
    } catch (e) {
      /* analytics must never break the page */
    }
  }

  function deviceCategory() {
    const w = window.innerWidth || document.documentElement.clientWidth;
    if (w < 768) return "mobile";
    if (w < 1024) return "tablet";
    return "desktop";
  }

  return {
    getReaderId: getReaderId,
    send: send,
    deviceCategory: deviceCategory,
    ctaClick: function (issueId) {
      send(issueId, "cta_click");
    },
  };
})();
