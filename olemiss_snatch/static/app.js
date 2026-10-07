(function () {
  "use strict";

  // ---------- toast ----------
  function toast(message, kind) {
    var el = document.getElementById("snatch-toast");
    if (!el || !window.bootstrap) return;
    el.classList.remove("toast-success", "toast-error");
    el.classList.add(kind === "error" ? "toast-error" : "toast-success");
    document.getElementById("snatch-toast-body").textContent = message;
    bootstrap.Toast.getOrCreateInstance(el, { delay: 3500 }).show();
  }

  // ---------- term picker ----------
  var termSelect = document.getElementById("term-select");
  if (termSelect) {
    termSelect.addEventListener("change", function () {
      window.location = "/dashboard?term=" + encodeURIComponent(termSelect.value);
    });
  }

  // ---------- live search ----------
  var input = document.getElementById("search-input");
  var results = document.getElementById("search-results");
  var timer = null;
  var latest = 0;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function render(courses, query) {
    results.replaceChildren();
    if (!courses.length) {
      results.appendChild(el("div", "text-muted small p-2", 'No courses match "' + query + '".'));
      return;
    }
    courses.forEach(function (c) {
      var a = el("a", "list-group-item list-group-item-action");
      a.href = c.url;
      if (window.location.pathname === c.url) a.classList.add("active");
      var top = el("div", "d-flex justify-content-between align-items-baseline gap-2");
      top.appendChild(el("span", "result-code", c.subject + " " + c.number));
      if (c.full > 0) top.appendChild(el("span", "full-count", c.full + " full"));
      a.appendChild(top);
      a.appendChild(el("div", "small text-muted text-truncate", c.title));
      results.appendChild(a);
    });
  }

  function search() {
    var q = input.value.trim();
    try { sessionStorage.setItem("snatch-q", q); } catch (e) {}
    if (!q) return;
    var id = ++latest;
    fetch("/api/search?q=" + encodeURIComponent(q), { headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (courses) { if (id === latest) render(courses, q); })
      .catch(function () {});
  }

  if (input && results) {
    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(search, 200);
    });
    // Keep the last search when moving between pages, like TigerSnatch.
    try {
      var saved = sessionStorage.getItem("snatch-q");
      if (saved) { input.value = saved; search(); }
    } catch (e) {}
  }

  // ---------- subscribe switches ----------
  document.addEventListener("change", function (event) {
    var sw = event.target;
    if (!sw.classList || !sw.classList.contains("snatch-switch")) return;
    var wanted = sw.checked;
    sw.disabled = true;
    fetch("/api/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
      body: JSON.stringify({ term: sw.dataset.term, crn: sw.dataset.crn, subscribe: wanted })
    })
      .then(function (r) { return r.json().then(function (body) { return { ok: r.ok, body: body }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.body.error || "Something went wrong.");
        var w = document.querySelector('[data-watchers-crn="' + sw.dataset.crn + '"]');
        if (w) w.textContent = res.body.watchers;
        if (wanted) {
          toast("Subscribed! You're #" + res.body.position + " in line for CRN " + sw.dataset.crn + ".");
        } else {
          toast("Unsubscribed from CRN " + sw.dataset.crn + ".");
          if (sw.dataset.removeRow) {
            var row = sw.closest("tr");
            if (row) row.remove();
          }
        }
      })
      .catch(function (err) {
        sw.checked = !wanted;
        toast(err.message, "error");
      })
      .finally(function () { sw.disabled = false; });
  });

  // ---------- trades ----------
  function postJSON(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (b) {
        if (!r.ok) throw new Error(b.error || "Something went wrong.");
        return b;
      });
    });
  }

  function afterTradeChange(res, quietMessage) {
    if (res.matches && res.matches.length) {
      toast("Trade match! Check the Trades box for their email.");
      setTimeout(function () { window.location.hash = "trades"; window.location.reload(); }, 900);
    } else if (quietMessage) {
      toast(quietMessage);
    }
  }

  var enrollBtn = document.getElementById("enroll-btn");
  if (enrollBtn) {
    enrollBtn.addEventListener("click", function () {
      var crn = document.getElementById("enroll-select").value;
      enrollBtn.disabled = true;
      postJSON("/api/enrollment", { term: enrollBtn.dataset.term, crn: crn, open_to_trade: true })
        .then(function () { window.location.hash = "trades"; window.location.reload(); })
        .catch(function (err) { toast(err.message, "error"); enrollBtn.disabled = false; });
    });
  }

  var openToTrade = document.getElementById("open-to-trade");
  if (openToTrade) {
    openToTrade.addEventListener("change", function () {
      var on = openToTrade.checked;
      postJSON("/api/enrollment", { term: openToTrade.dataset.term, crn: openToTrade.dataset.crn, open_to_trade: on })
        .then(function (res) { afterTradeChange(res, on ? "You're open to trades." : "Trades paused for this course."); })
        .catch(function (err) { openToTrade.checked = !on; toast(err.message, "error"); });
    });
  }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest && event.target.closest("#leave-trades, .leave-trades-btn");
    if (!btn) return;
    btn.disabled = true;
    postJSON("/api/enrollment", { term: btn.dataset.term, crn: null,
                                  subject: btn.dataset.subject, course_number: btn.dataset.number })
      .then(function () { window.location.reload(); })
      .catch(function (err) { toast(err.message, "error"); btn.disabled = false; });
  });

  document.addEventListener("change", function (event) {
    var box = event.target;
    if (!box.classList || !box.classList.contains("trade-want")) return;
    var want = box.checked;
    box.disabled = true;
    postJSON("/api/trade-want", { term: box.dataset.term, crn: box.dataset.crn, want: want })
      .then(function (res) { afterTradeChange(res, want ? "Added. We'll email you if someone there wants your section." : "Removed."); })
      .catch(function (err) { box.checked = !want; toast(err.message, "error"); })
      .finally(function () { box.disabled = false; });
  });
})();
