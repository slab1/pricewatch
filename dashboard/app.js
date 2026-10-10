/* PriceWatch free dashboard — consumes /public/* endpoints only, no auth. */
(function () {
  "use strict";

  const REFRESH_MS = 60000;
  const state = { items: [], latest: [], activeItem: null, rangeDays: 90, chart: null };

  const $ = (id) => document.getElementById(id);

  async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(url + " -> " + r.status);
    return r.json();
  }

  // ---- formatting ----------------------------------------------------------
  const fmt = {
    value(v) {
      if (v >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
      if (v >= 10) return v.toLocaleString("en-US", { maximumFractionDigits: 1 });
      return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
    },
    date(d) { return d || "—"; },
    pct(x) { return (x >= 0 ? "+" : "") + x.toFixed(1) + "%"; },
    prettyItem(slug) {
      return slug.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    },
  };

  // ---- overview cards ------------------------------------------------------
  function itemHistory(itemSlug) {
    // newest-first rows for this item from the single /public/latest pass is
    // not enough (that's one row per source); cards use per-item fetch lazily.
    return state.histories[itemSlug] || [];
  }

  function changeOver(rows, days) {
    // rows: newest-first [{value, collected_date}]
    if (rows.length < 2) return null;
    const latest = rows[0];
    const target = new Date(latest.collected_date);
    target.setDate(target.getDate() - days);
    const targetStr = target.toISOString().slice(0, 10);
    let closest = null;
    for (const r of rows) {
      if (r.collected_date <= targetStr) { closest = r; break; }
    }
    if (!closest || closest.value === 0) return null;
    return ((latest.value - closest.value) / closest.value) * 100;
  }

  function sparklineSVG(rows, color) {
    const pts = rows.slice(0, 30).map((r) => r.value).reverse();
    if (pts.length < 2) return "";
    const w = 200, h = 28, min = Math.min(...pts), max = Math.max(...pts);
    const span = max - min || 1;
    const xy = pts.map((v, i) => {
      const x = (i / (pts.length - 1)) * w;
      const y = h - ((v - min) / span) * (h - 4) - 2;
      return x.toFixed(1) + "," + y.toFixed(1);
    });
    return '<svg class="spark" viewBox="0 0 ' + w + " " + h + '" width="100%" height="28" preserveAspectRatio="none" aria-hidden="true">' +
      '<polyline fill="none" stroke="' + color + '" stroke-width="1.5" points="' + xy.join(" ") + '"/></svg>';
  }

  function renderCards() {
    const box = $("cards");
    box.innerHTML = "";
    const byItem = new Map();
    for (const row of state.latest) {
      if (row.source === "demo") continue; // exclude demo from latest value
      const cur = byItem.get(row.item);
      if (!cur ||
          row.collected_date > cur.collected_date ||
          (row.collected_date === cur.collected_date && row.source < cur.source)) {
        byItem.set(row.item, row);
      }
    }
    if (!byItem.size) { $("cards-empty").hidden = false; return; }
    $("cards-empty").hidden = true;

    for (const [slug, latest] of byItem) {
      const hist = itemHistory(slug);
      const d7 = changeOver(hist, 7);
      const d30 = changeOver(hist, 30);
      const el = document.createElement("div");
      el.className = "card";
      el.setAttribute("role", "listitem");
      el.tabIndex = 0;
      el.dataset.item = slug;
      const deltaHTML = (label, d) =>
        d === null ? '<span class="delta flat">' + label + " —</span>"
          : '<span class="delta ' + (d > 0 ? "up" : d < 0 ? "down" : "flat") + '">' +
            label + " " + fmt.pct(d) + "</span>";
      el.innerHTML =
        '<div class="item-name">' + fmt.prettyItem(slug) + "</div>" +
        '<div class="value">' + fmt.value(latest.value) + "</div>" +
        '<div class="unit">' + latest.unit + "</div>" +
        '<div class="row">' + deltaHTML("7d", d7) + deltaHTML("30d", d30) + "</div>" +
        sparklineSVG(hist, d7 !== null && d7 < 0 ? "var(--red)" : "var(--green)") +
        '<div class="date">as of ' + fmt.date(latest.collected_date) + "</div>";
      el.addEventListener("click", () => selectItem(slug));
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") selectItem(slug); });
      box.appendChild(el);
    }
  }

  // ---- detail chart --------------------------------------------------------
  async function selectItem(slug) {
    state.activeItem = slug;
    $("detail").hidden = false;
    $("detail-title").textContent = fmt.prettyItem(slug);
    const meta = state.items.find((i) => i.item === slug);
    $("detail-unit").textContent = meta ? meta.unit : "";
    await loadDetail();
    document.getElementById("detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function loadDetail() {
    const slug = state.activeItem;
    if (!slug) return;
    const limit = state.rangeDays === 0 ? 1000 : Math.min(state.rangeDays + 10, 1000);
    let rows;
    try {
      rows = await getJSON("/public/prices/" + encodeURIComponent(slug) + "?limit=" + limit);
    } catch (e) {
      $("chart-fallback").hidden = false;
      $("chart-fallback").innerHTML = "<p class='muted'>Could not load history: " + e.message + "</p>";
      return;
    }
    if (state.rangeDays > 0) {
      const cutoff = new Date();
      cutoff.setDate(cutoff.getDate() - state.rangeDays);
      const cutStr = cutoff.toISOString().slice(0, 10);
      rows = rows.filter((r) => r.collected_date >= cutStr);
    }
    // group by source -> series
    const bySource = new Map();
    for (const r of rows) {
      if (!bySource.has(r.source)) bySource.set(r.source, []);
      bySource.get(r.source).push(r);
    }
    for (const arr of bySource.values()) arr.sort((a, b) => a.collected_date.localeCompare(b.collected_date));
    $("detail-legend").textContent =
      "Sources: " + [...bySource.keys()].join(", ") +
      (bySource.has("demo") ? "  (demo series is synthetic seed data)" : "");

    renderChart(bySource);
  }

  function renderChart(bySource) {
    const canvas = $("detail-chart");
    const fallback = $("chart-fallback");
    if (typeof Chart === "undefined") {
      canvas.hidden = true;
      fallback.hidden = false;
      let html = "<table><thead><tr><th>Date</th><th>Source</th><th class='num'>Value</th></tr></thead><tbody>";
      for (const [src, arr] of bySource) {
        for (const r of arr.slice(-50)) {
          html += "<tr><td>" + r.collected_date + "</td><td>" + src +
            "</td><td class='num'>" + fmt.value(r.value) + "</td></tr>";
        }
      }
      fallback.innerHTML = html + "</tbody></table>";
      return;
    }
    canvas.hidden = false;
    fallback.hidden = true;
    const palette = ["#58a6ff", "#d29922", "#3fb950", "#f85149", "#bc8cff", "#76e3ea"];
    const datasets = [];
    let i = 0;
    for (const [src, arr] of bySource) {
      datasets.push({
        label: src + " (" + (arr[0] ? arr[0].unit : "") + ")",
        data: arr.map((r) => ({ x: r.collected_date, y: r.value })),
        borderColor: palette[i % palette.length],
        backgroundColor: palette[i % palette.length],
        tension: 0.15,
        pointRadius: arr.length > 120 ? 0 : 2,
      });
      i++;
    }
    if (state.chart) state.chart.destroy();
    state.chart = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { type: "category", ticks: { color: "#8b949e", maxTicksLimit: 12 }, grid: { color: "#2d333b" } },
          y: { ticks: { color: "#8b949e" }, grid: { color: "#2d333b" } },
        },
        plugins: {
          legend: { labels: { color: "#e6edf3", boxWidth: 12 } },
          tooltip: { callbacks: { label: (c) => c.dataset.label + ": " + fmt.value(c.parsed.y) } },
        },
      },
    });
  }

  $("ranges").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-days]");
    if (!btn) return;
    for (const b of $("ranges").querySelectorAll("button")) b.classList.remove("active");
    btn.classList.add("active");
    state.rangeDays = parseInt(btn.dataset.days, 10);
    loadDetail();
  });

  // ---- latest table + demo badge -------------------------------------------
  function renderTable() {
    const tbody = $("latest-table").querySelector("tbody");
    tbody.innerHTML = "";
    for (const r of state.latest) {
      const tr = document.createElement("tr");
      tr.innerHTML = "<td>" + fmt.prettyItem(r.item) + "</td><td>" + r.source + "</td>" +
        '<td class="num">' + fmt.value(r.value) + "</td><td>" + r.unit + "</td><td>" +
        r.collected_date + "</td>";
      tbody.appendChild(tr);
    }
  }

  function updateDemoBadge() {
    $("demo-badge").hidden = !state.latest.some((r) => r.source === "demo");
  }

  // ---- boot / polling ------------------------------------------------------
  async function refreshAll() {
    const ind = $("refresh-indicator");
    try {
      const [items, latest] = await Promise.all([
        getJSON("/public/items"),
        getJSON("/public/latest"),
      ]);
      state.items = items;
      state.latest = latest;
      // histories for cards: fetch per item (bounded, public, no key)
      state.histories = {};
      await Promise.all(items.map(async (it) => {
        try {
          state.histories[it.item] = await getJSON(
            "/public/prices/" + encodeURIComponent(it.item) + "?limit=35");
        } catch (_) { state.histories[it.item] = []; }
      }));
      renderCards();
      renderTable();
      updateDemoBadge();
      if (state.activeItem) loadDetail();
      ind.textContent = "live";
      ind.classList.remove("stale");
    } catch (e) {
      ind.textContent = "offline (" + e.message + ")";
      ind.classList.add("stale");
    }
    $("clock").textContent = new Date().toISOString().replace("T", " ").slice(0, 19) + " UTC";
  }

  refreshAll();
  setInterval(refreshAll, REFRESH_MS);
})();
