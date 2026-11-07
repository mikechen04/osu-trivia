/* Leaderboard: friendly country names in the filter, and apply on change. */
(() => {
  "use strict";
  const select = document.querySelector("[data-country-select]");
  if (!select) return;

  let names = null;
  try { names = new Intl.DisplayNames(["en"], { type: "region" }); } catch (e) { /* older browsers: keep codes */ }
  const flag = (cc) => String.fromCodePoint(...[...cc].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65));

  select.querySelectorAll("option[data-cc]").forEach((option) => {
    const cc = option.dataset.cc;
    let name = cc;
    try { name = (names && names.of(cc)) || cc; } catch (e) { /* ignore */ }
    option.textContent = `${flag(cc)} ${name}`;
  });
  select.addEventListener("change", () => select.form.submit());
})();
