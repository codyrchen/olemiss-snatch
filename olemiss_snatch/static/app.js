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
})();
