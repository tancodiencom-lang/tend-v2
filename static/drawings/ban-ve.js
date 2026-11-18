/* Bản vẽ chỉ xem: kéo, phóng (hai ngón / con lăn / nút), chạm chi tiết để xem số liệu. Không ghi gì. */
(function () {
  "use strict";
  var plan = document.querySelector("[data-plan]");
  if (!plan) return;
  var svg = plan.querySelector("[data-svg]");
  var panel = document.querySelector("[data-panel]");
  var info = JSON.parse(document.getElementById("plan-info").textContent);
  var home = svg.getAttribute("viewBox").split(/\s+/).map(Number);
  var view = home.slice();
  var pointers = {}, start = null, moved = false;

  function apply() { svg.setAttribute("viewBox", view.join(" ")); }

  function toPlan(clientX, clientY) {
    var r = svg.getBoundingClientRect();
    var scale = Math.max(view[2] / r.width, view[3] / r.height);
    var offX = (r.width * scale - view[2]) / 2, offY = (r.height * scale - view[3]) / 2;
    return { x: view[0] - offX + (clientX - r.left) * scale, y: view[1] - offY + (clientY - r.top) * scale, scale: scale };
  }

  function zoom(factor, cx, cy) {
    var r = svg.getBoundingClientRect();
    var p = toPlan(cx === undefined ? r.left + r.width / 2 : cx, cy === undefined ? r.top + r.height / 2 : cy);
    var w = Math.min(home[2] * 4, Math.max(home[2] / 40, view[2] / factor));
    var k = w / view[2];
    view = [p.x - (p.x - view[0]) * k, p.y - (p.y - view[1]) * k, view[2] * k, view[3] * k];
    apply();
  }

  plan.querySelectorAll("[data-zoom]").forEach(function (b) {
    b.addEventListener("click", function () {
      var f = Number(b.getAttribute("data-zoom"));
      if (!f) { view = home.slice(); apply(); } else zoom(f);
    });
  });
  svg.addEventListener("wheel", function (e) { e.preventDefault(); zoom(e.deltaY < 0 ? 1.2 : 1 / 1.2, e.clientX, e.clientY); }, { passive: false });

  svg.addEventListener("pointerdown", function (e) {
    pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
    svg.setPointerCapture(e.pointerId);
    start = { view: view.slice(), points: JSON.parse(JSON.stringify(pointers)), target: e.target };
    moved = false;
  });
  svg.addEventListener("pointermove", function (e) {
    if (!pointers[e.pointerId] || !start) return;
    pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
    var ids = Object.keys(pointers);
    var r = svg.getBoundingClientRect();
    var scale = Math.max(start.view[2] / r.width, start.view[3] / r.height);
    if (ids.length === 1 && start.points[ids[0]]) {
      var dx = e.clientX - start.points[ids[0]].x, dy = e.clientY - start.points[ids[0]].y;
      if (Math.abs(dx) + Math.abs(dy) > 6) moved = true;
      view = [start.view[0] - dx * scale, start.view[1] - dy * scale, start.view[2], start.view[3]];
      apply();
    } else if (ids.length === 2 && start.points[ids[0]] && start.points[ids[1]]) {
      var a0 = start.points[ids[0]], b0 = start.points[ids[1]], a1 = pointers[ids[0]], b1 = pointers[ids[1]];
      var d0 = Math.hypot(a0.x - b0.x, a0.y - b0.y) || 1, d1 = Math.hypot(a1.x - b1.x, a1.y - b1.y) || 1;
      moved = true;
      view = start.view.slice(); apply();
      zoom(d1 / d0, (a1.x + b1.x) / 2, (a1.y + b1.y) / 2);
    }
  });
  function release(e) {
    var tap = start && !moved && Object.keys(pointers).length === 1;
    var target = start && start.target;
    delete pointers[e.pointerId];
    if (Object.keys(pointers).length) { start = { view: view.slice(), points: JSON.parse(JSON.stringify(pointers)) }; return; }
    start = null;
    if (tap && target && target.hasAttribute && target.hasAttribute("data-i")) pick(Number(target.getAttribute("data-i")));
  }
  svg.addEventListener("pointerup", release);
  svg.addEventListener("pointercancel", function (e) { delete pointers[e.pointerId]; start = null; });

  document.querySelectorAll("[data-layer]").forEach(function (b) {
    b.addEventListener("click", function () {
      var on = b.getAttribute("aria-pressed") !== "true";
      b.setAttribute("aria-pressed", on ? "true" : "false");
      svg.classList.toggle("hide-" + b.getAttribute("data-layer"), !on);
    });
  });

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function pick(i) {
    var x = info[i];
    if (!x) return;
    svg.querySelectorAll(".it.sel").forEach(function (n) { n.classList.remove("sel"); });
    var shape = svg.querySelector('[data-i="' + i + '"]');
    if (shape) shape.classList.add("sel");
    var rows = x.history.length ? x.history.slice().reverse().map(function (h) {
      var photos = h.photos.length ? '<div class="photos">' + h.photos.map(function (u) {
        return '<a href="' + esc(u) + '" target="_blank" rel="noopener"><img src="' + esc(u) + '" alt="Ảnh ' + esc(x.code) + " " + esc(h.date) + '" loading="lazy"></a>';
      }).join("") + "</div>" : "";
      return "<li><span>" + esc(h.date) + " · " + esc(h.author) + (h.reason ? '<br><small class="muted">Lý do giảm: ' + esc(h.reason) + "</small>" : "") +
        (h.note ? '<br><small class="muted">' + esc(h.note) + "</small>" : "") + photos + "</span><span>" + esc(h.percent) + "</span></li>";
    }).join("") : '<li><span class="missing">Chưa có báo cáo</span></li>';
    var asks = x.asks.length ? "<h3>Đang hỏi</h3><ul class=\"lines\">" + x.asks.map(function (a) { return "<li><span>" + esc(a) + "</span></li>"; }).join("") + "</ul>" : "";
    panel.innerHTML = "<h2>" + esc(x.code) + " · " + esc(x.work) + "</h2>" +
      (x.location ? "<p>" + esc(x.location) + "</p>" : "") +
      "<p><b>" + esc(x.status) + " " + esc(x.percent) + "%</b> · thiết kế " + esc(x.quantity) + " " + esc(x.unit) + "</p>" +
      (x.note ? '<p class="muted">' + esc(x.note) + "</p>" : "") + asks +
      "<h3>Các lần báo</h3><ul class=\"lines\">" + rows + "</ul>" +
      '<p class="muted">Báo cáo, sửa số: nhắn bot Tend trên Telegram, ghi mã ' + esc(x.code) + ".</p>";
    var form = document.getElementById("ask-form");
    if (form) {
      var box = document.createElement("div");
      box.innerHTML = "<h3>Yêu cầu báo cáo " + esc(x.code) + "</h3>";
      box.appendChild(form.content.cloneNode(true));
      box.querySelector("[name=item_id]").value = x.id;
      panel.appendChild(box);
    }
  }
  document.querySelectorAll("[data-pick]").forEach(function (b) {
    b.addEventListener("click", function () { pick(Number(b.getAttribute("data-pick"))); panel.scrollIntoView({ block: "nearest" }); });
  });
})();
