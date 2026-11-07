/* Site-wide: reset countdowns and the cookie notice. No analytics, no tracking. */
(() => {
  "use strict";

  function pad(n) { return String(n).padStart(2, "0"); }

  // Counts down to a server-supplied UTC timestamp. The offset corrects for a wrong local clock.
  function initCountdowns(scope) {
    (scope || document).querySelectorAll("[data-countdown]").forEach((el) => {
      if (el.dataset.ready) return;
      el.dataset.ready = "1";
      const target = Number(el.dataset.target);
      const offset = Number(el.dataset.now) - Date.now();
      let reloaded = false;
      const tick = () => {
        const left = Math.max(0, Math.floor((target - (Date.now() + offset)) / 1000));
        el.textContent = `${pad(Math.floor(left / 3600))}:${pad(Math.floor((left % 3600) / 60))}:${pad(left % 60)}`;
        if (left === 0 && el.hasAttribute("data-reload") && !reloaded) {
          reloaded = true;
          setTimeout(() => location.reload(), 1500);
        }
      };
      tick();
      setInterval(tick, 1000);
    });
  }
  window.initCountdowns = initCountdowns;
  initCountdowns();

  // A link like /faq/#contact opens the collapsed section it points at.
  function openTarget() {
    const el = location.hash.length > 1 ? document.getElementById(decodeURIComponent(location.hash.slice(1))) : null;
    if (el && el.tagName === "DETAILS") { el.open = true; el.scrollIntoView(); }
  }
  openTarget();
  window.addEventListener("hashchange", openTarget);

  // Cookie notice. We only set strictly necessary cookies, so this is informational.
  // Remembering that you dismissed it uses localStorage (not a cookie).
  const banner = document.getElementById("cookie-banner");
  const ok = document.getElementById("cookie-ok");
  const KEY = "osu_trivia_cookie_notice";
  if (banner && ok) {
    let seen = false;
    try { seen = localStorage.getItem(KEY) === "1"; } catch (e) { /* storage blocked: just show it */ }
    if (!seen) banner.hidden = false;
    ok.addEventListener("click", () => {
      banner.hidden = true;
      try { localStorage.setItem(KEY, "1"); } catch (e) { /* ignore */ }
    });
  }
})();
