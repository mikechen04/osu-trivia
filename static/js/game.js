/* The game screen. All rules (timer, scoring, locking) live on the server; this file only
   draws the state the server sends and posts answers back. It never sees accepted answers or
   tiers before you've answered. */
(() => {
  "use strict";
  const root = document.getElementById("game");
  if (!root) return;

  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const urls = {
    state: root.dataset.stateUrl,
    next: root.dataset.nextUrl,
    answer: root.dataset.answerUrl,
    board: root.dataset.leaderboardUrl,
  };

  const me = { avatar: root.dataset.avatar || "", name: root.dataset.name || "" };
  const calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let state = null;
  let last = null; // the result of the question you just answered (shown before moving on)
  let timer = null;
  let reveal = false; // true only right after you lock an answer: plays the climb before the result
  let stopReveal = null;

  // A polite live region for screen readers (kept outside the re-rendered area).
  const live = document.createElement("div");
  live.className = "vh";
  live.setAttribute("aria-live", "polite");
  live.setAttribute("aria-atomic", "true");
  document.body.append(live);
  const say = (text) => { live.textContent = ""; setTimeout(() => { live.textContent = text; }, 50); };

  // ---------- tiny DOM helper (text is always set as text, never as HTML) ----------
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === false || value == null) continue;
      if (key === "class") el.className = value;
      else if (key === "text") el.textContent = value;
      else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, value === true ? "" : value);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(kid));
    }
    return el;
  }

  class ApiError extends Error {
    constructor(message, status, code) { super(message); this.status = status; this.code = code; }
  }

  async function api(url, body) {
    const init = body === undefined
      ? { credentials: "same-origin", headers: { Accept: "application/json" } }
      : {
          method: "POST",
          credentials: "same-origin",
          headers: { Accept: "application/json", "Content-Type": "application/json", "X-CSRFToken": csrf },
          body: JSON.stringify(body),
        };
    let res;
    try { res = await fetch(url, init); } catch (e) { throw new ApiError("Network problem. Check your connection and try again.", 0); }
    let data = null;
    try { data = await res.json(); } catch (e) { /* not JSON */ }
    if (!res.ok) throw new ApiError((data && data.error && data.error.message) || "Something went wrong.", res.status, data && data.error && data.error.code);
    return data;
  }

  // ---------- rendering plumbing ----------
  function stopTimer() { if (timer) { clearInterval(timer); timer = null; } }

  function show(node, focusEl) {
    if (stopReveal) { stopReveal(); stopReveal = null; }
    stopTimer();
    root.replaceChildren(node);
    if (window.initCountdowns) window.initCountdowns(root);
    const target = focusEl || node.querySelector("[data-focus]");
    if (target) { target.setAttribute("tabindex", target.getAttribute("tabindex") || "-1"); target.focus({ preventScroll: false }); }
  }

  function errorBox(message) {
    return h("div", { class: "alert", role: "alert", "data-error": "" }, message);
  }

  function showError(container, message) {
    const old = container.querySelector("[data-error]");
    if (old) old.remove();
    container.append(errorBox(message));
  }

  async function reload(showQuestionId) {
    reveal = false;
    state = await api(urls.state);
    last = showQuestionId ? (state.answered.find((a) => a.question_id === showQuestionId) || null) : null;
    render();
  }

  // Run a server action from a button, with a disabled state and an inline error.
  async function act(button, container, fn) {
    button.disabled = true;
    try {
      await fn();
    } catch (err) {
      button.disabled = false;
      showError(container, err.message);
    }
  }

  function render() {
    if (!state.ready && state.status === "not_started") return show(screenNotReady());
    if (last) return show(reveal ? screenReveal() : screenResult());
    switch (state.status) {
      case "active": return screenQuestion();
      case "finished": return show(screenFinal());
      default: return show(screenIntro());
    }
  }

  // ---------- shared bits ----------
  function dots(currentPos) {
    const wrap = h("div", { class: "dots", role: "img", "aria-label": `Question ${currentPos || state.answered.length} of ${state.total_questions}` });
    for (let p = 1; p <= state.total_questions; p++) {
      const done = state.answered.find((a) => a.position === p);
      wrap.append(h("span", { class: `dot ${done ? `tier-${done.tier}` : ""} ${p === currentPos ? "is-now" : ""}` }));
    }
    return wrap;
  }

  // ---------- screens ----------
  function screenNotReady() {
    return h("div", { class: "card center" },
      h("h2", { "data-focus": "" }, "Today’s set isn’t ready yet"),
      h("p", { class: "muted" }, "Today’s questions aren’t up yet. Check back soon!"),
      h("p", { class: "muted small" }, "Next reset in ",
        h("strong", { "data-countdown": "", "data-target": state.next_reset_ms, "data-now": state.server_now_ms }, "--:--:--")));
  }

  function screenIntro() {
    const n = state.total_questions;
    const resuming = state.answered.length > 0;
    const card = h("div", { class: "card center" });
    const button = h("button", { class: "btn btn--pink btn--lg", type: "button", "data-focus": "" }, resuming ? "Start next question" : "Start");
    button.addEventListener("click", () => act(button, card, async () => { state = await api(urls.next, {}); last = null; render(); }));
    card.append(
      h("h2", null, resuming ? `${state.answered.length} of ${n} done. Ready for the next one?` : "Today’s set"),
      h("p", { class: "muted" }, `${n} questions, ${state.question_seconds} seconds each, one attempt a day.`),
      h("p", { class: "muted" }, "The clock starts the moment a question shows up. Answers lock when you send them, so no take-backs."));
    if (resuming) card.append(dots(0));
    card.append(h("div", { class: "cta row row--center" }, button));
    return card;
  }

  function screenQuestion() {
    const cur = state.current;
    const total = state.question_seconds;
    const deadline = performance.now() + cur.seconds_left * 1000; // monotonic: immune to clock changes
    let sending = false;

    const input = h("input", {
      type: "text", id: "answer", name: "answer", maxlength: "100", autocomplete: "off", autocapitalize: "off",
      autocorrect: "off", spellcheck: "false", enterkeyhint: "send", "aria-describedby": "q-hint", placeholder: "Type your answer",
    });
    const submit = h("button", { class: "btn btn--pink btn--lg", type: "submit" }, "Lock it in");
    const num = h("span", { class: "timer__num", "aria-hidden": "true" }, String(Math.ceil(cur.seconds_left)));
    const fill = h("div", { class: "bar__fill" });
    const timerBox = h("div", { class: "timer" },
      h("div", { class: "timer__top" }, h("span", null, "Time left"), num),
      h("div", { class: "bar", "aria-hidden": "true" }, fill));
    const form = h("form", { class: "answer-form", autocomplete: "off" }, h("label", { class: "vh", for: "answer" }, "Your answer"), input, submit);
    const card = h("div", { class: "card" },
      h("div", { class: "game__head" }, h("span", null, `Question ${cur.position} of ${state.total_questions}`), dots(cur.position)),
      timerBox,
      h("h2", { class: "prompt", id: "q-prompt", tabindex: "-1" }, cur.prompt),
      h("p", { class: "muted", id: "q-hint" }, cur.hint || ""),
      form);

    async function send() {
      if (sending) return;
      sending = true;
      input.readOnly = true;
      submit.disabled = true;
      try {
        const data = await api(urls.answer, { question_id: cur.question_id, answer: input.value });
        last = data.result;
        state = data;
        reveal = !calm;
        render();
      } catch (err) {
        if (err.status === 409) return reload(cur.question_id); // already answered/not started: trust the server
        if ((deadline - performance.now()) <= 0) return reload(cur.question_id).catch(() => {});
        sending = false;
        input.readOnly = false;
        submit.disabled = false;
        showError(card, err.message);
      }
    }
    form.addEventListener("submit", (e) => { e.preventDefault(); send(); });

    show(card, input);
    say(`Question ${cur.position} of ${state.total_questions}. ${cur.prompt}. ${total} seconds.`);

    let announced = new Set();
    timer = setInterval(() => {
      const left = Math.max(0, (deadline - performance.now()) / 1000);
      num.textContent = String(Math.ceil(left));
      fill.style.width = `${Math.min(100, (left / total) * 100)}%`;
      timerBox.classList.toggle("is-low", left <= 10 && left > 5);
      timerBox.classList.toggle("is-crit", left <= 5);
      for (const mark of [10, 5]) {
        if (left <= mark && !announced.has(mark)) { announced.add(mark); say(`${mark} seconds left`); }
      }
      if (left <= 0 && !sending) { stopTimer(); send(); } // the server decides whether it still counts
    }, 100);
  }

  // The climb: your avatar rises a y-axis of pp (100 points = 1000pp) slowly, then the result shows.
  // Only transform changes while it plays. Reduced-motion users never see it.
  function screenReveal() {
    const r = last;
    const target = Math.min(1, Math.max(0, Number(r.points) || 0) / 100);
    const skip = h("button", { class: "btn btn--ghost btn--sm", type: "button", "data-focus": "" }, "Skip");
    const readout = h("div", { class: "reveal__pp", "aria-hidden": "true" }, "0pp");
    const grid = h("div", { class: "chart__grid", "aria-hidden": "true" });
    const rows = [];
    for (let i = 1; i <= 10; i++) {
      const row = h("div", { class: "chart__row" }, h("span", { class: "chart__tick" }, `${i * 100}pp`));
      row.style.bottom = `${i * 10}%`;
      grid.append(row);
      rows.push(row);
    }
    const bar = h("div", { class: "chart__bar", "aria-hidden": "true" });
    const face = me.avatar
      ? h("img", { class: "avatar avatar--md chart__you", src: me.avatar, alt: "", referrerpolicy: "no-referrer" })
      : h("span", { class: "avatar avatar--md avatar--fallback chart__you", "aria-hidden": "true" }, (me.name || "?").slice(0, 1).toUpperCase());
    const chart = h("div", { class: "chart" }, grid, bar, face);
    const card = h("div", { class: "card reveal" },
      h("div", { class: "game__head" }, h("span", null, `Question ${r.position} of ${state.total_questions}`), dots(r.position)),
      h("p", { class: "muted" }, "Let’s see how rare that was"),
      readout, chart,
      h("div", { class: "cta row row--center" }, skip));

    const D = 5200;
    const ease = (x) => (x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2);
    // Fast start, a long stall near the top, then a slow crawl to the final level.
    const prog = (u) => {
      if (u < 0.3) return 0.45 * (1 - Math.pow(1 - u / 0.3, 2));
      if (u < 0.7) return 0.45 + 0.35 * ease((u - 0.3) / 0.4);
      return 0.8 + 0.2 * ease((u - 0.7) / 0.3);
    };
    let raf = 0, tm = 0, t0 = 0, H = 0, finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      reveal = false;
      show(screenResult());
    };
    const frame = (now) => {
      if (!t0) t0 = now;
      const u = Math.min(1, (now - t0) / D);
      // A zero-point answer rises a little and falls back.
      const lvl = target > 0 ? target * prog(u) : 0.12 * Math.sin(Math.PI * Math.pow(u, 0.7));
      face.style.transform = `translateY(${-lvl * H}px)`;
      bar.style.transform = `scaleY(${lvl})`;
      readout.textContent = `${Math.round((target > 0 ? target * prog(u) : 0) * 1000)}pp`;
      rows.forEach((row, i) => row.classList.toggle("is-lit", lvl * 10 >= i + 1));
      if (u < 1) raf = requestAnimationFrame(frame);
      else tm = setTimeout(finish, 1000);
    };
    skip.addEventListener("click", finish);
    // Armed on the next frame: show() runs right after this function returns and must not cancel it.
    requestAnimationFrame(() => {
      stopReveal = () => { finished = true; cancelAnimationFrame(raf); clearTimeout(tm); };
      H = chart.clientHeight;
      raf = requestAnimationFrame(frame);
    });
    say("Revealing your result.");
    return card;
  }

  function screenResult() {
    const r = last;
    const lastOne = state.finished;
    const card = h("div", { class: "card result" });
    const button = h("button", { class: "btn btn--pink btn--lg", type: "button", "data-focus": "" }, lastOne ? "See my results" : "Next question");
    button.addEventListener("click", () => act(button, card, async () => {
      last = null;
      if (state.finished) return render();
      state = await api(urls.next, {});
      render();
    }));

    let line;
    if (r.timed_out) line = "Time’s up! No answer counted.";
    else if (r.matched) line = ["Counted as ", h("strong", null, r.matched), "."];
    else if (r.answer) line = ["“", r.answer, "” isn’t on today’s list."];
    else line = "You didn’t answer.";

    card.append(
      h("div", { class: "game__head" }, h("span", null, `Question ${r.position} of ${state.total_questions}`), dots(r.position)),
      h("div", { class: `tier tier-${r.tier} result__tier`, "data-focus": "", tabindex: "-1" }, r.tier_name),
      h("div", { class: `result__points tier-${r.tier}` }, `+${r.points}`),
      h("p", { class: "muted" }, line),
      h("p", { class: "muted small" }, `Total so far: ${state.score} / ${state.max_score}`),
      h("div", { class: "cta row row--center" }, button));
    say(`${r.tier_name}. ${r.points} points.`);
    return card;
  }

  function screenFinal() {
    const n = state.total_questions;
    const wrap = h("div", { class: "grid" });

    // --- score card ---
    const row = state.answered.map((a) => a.emoji).join("");
    const recap = h("ol", { class: "recap" }, state.answered.map((a) =>
      h("li", null,
        h("span", { class: `tier tier-${a.tier}` }, a.tier_name),
        h("span", { class: "q" }, a.prompt,
          h("span", { class: "mine" }, a.timed_out ? "Time ran out" : a.answer ? `You said: ${a.answer}` : "No answer")),
        h("strong", null, `+${a.points}`))));

    const status = h("span", { class: "vh", role: "status" });
    const copyBtn = h("button", { class: "btn btn--pink", type: "button" }, "Copy result");
    copyBtn.addEventListener("click", async () => {
      const ok = await copyText(state.share.text);
      copyBtn.textContent = ok ? "Copied!" : "Copy failed";
      status.textContent = ok ? "Result copied to clipboard" : "Could not copy";
      setTimeout(() => { copyBtn.textContent = "Copy result"; }, 2000);
    });

    wrap.append(h("div", { class: "card center" },
      h("div", { class: "muted", "data-focus": "", tabindex: "-1" }, `Your score for ${state.date}`),
      h("div", { class: "final__score" }, String(state.score), h("small", null, ` / ${state.max_score}`)),
      h("div", { class: "emoji-row", role: "img", "aria-label": `Tier results: ${state.answered.map((a) => a.tier_name).join(", ")}` }, row),
      recap,
      h("pre", { class: "sharebox", "aria-label": "Share text preview" }, state.share.text),
      h("div", { class: "cta row row--center" },
        copyBtn,
        h("a", { class: "btn", href: state.share.url }, "Open share page"),
        h("a", { class: "btn btn--ghost", href: urls.board }, "Leaderboard")),
      status,
      h("p", { class: "muted small mt-l" }, "New questions in ",
        h("strong", { "data-countdown": "", "data-target": state.next_reset_ms, "data-now": state.server_now_ms }, "--:--:--"))));

    // --- most popular answers ---
    const breakdown = h("div", { class: "card" }, h("h2", null, "Most popular answers"),
      h("p", { class: "muted small" }, "Share is how much of the valid-answer pie each one got. The tier is how rare it looks right now."));
    (state.breakdown || []).forEach((q) => {
      const list = h("ul", { class: "pop" });
      if (!q.answers.length) list.append(h("li", null, h("span", { class: "muted" }, "No valid answers yet.")));
      q.answers.forEach((a) => {
        const fill = h("div", { class: "bar__fill" });
        fill.style.width = `${Math.max(2, a.share)}%`;
        list.append(h("li", { class: a.mine ? "is-mine" : "" },
          h("span", { class: "name" }, a.text, a.mine ? " (you)" : "", h("span", { class: "tier" }, a.tier_name)),
          h("span", { class: "muted" }, `${a.share}% (${a.plays} plays)`),
          h("div", { class: "bar", "aria-hidden": "true" }, fill)));
      });
      breakdown.append(h("details", { class: "break", open: q.position === 1 },
        h("summary", null, `${q.position}. ${q.prompt}`), h("div", { class: "body" }, list)));
    });
    wrap.append(breakdown);
    return wrap;
  }

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* fall through */ }
    try {
      const ta = h("textarea", { readonly: "", "aria-hidden": "true" });
      ta.value = text;
      ta.className = "vh";
      document.body.append(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch (e) { return false; }
  }

  // ---------- go ----------
  reload().catch((err) => {
    const card = h("div", { class: "card center" });
    if (err.status === 401) {
      card.append(h("h2", null, "Please sign in"), h("a", { class: "btn btn--pink", href: "/login/?next=/play/" }, "Sign in with osu!"));
    } else {
      card.append(h("h2", null, "The game didn’t load"), h("p", { class: "muted" }, err.message),
        h("button", { class: "btn", type: "button", onclick: () => location.reload() }, "Try again"));
    }
    show(card);
  });
})();
