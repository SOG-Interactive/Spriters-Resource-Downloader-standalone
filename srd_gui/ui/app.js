/* Spriters Resource Downloader - UI logic (vanilla JS, talks to Python via window.pywebview.api) */
window.App = (function () {
  "use strict";

  const state = {
    ready: false,
    screen: "start",
    settings: {},
    info: {},
    catalog: null,          // {game_title, console_title, sections:[{id,name,items:[{id,title,thumb,section,order}]}]}
    items: [],              // flat, page order
    byId: {},
    selected: new Set(),
    collapsed: new Set(),
    thumbs: {},             // id -> data URL
    thumbsDone: 0,
    filter: "",
    sort: "page",
    onlySelected: false,
    session: "idle",
    mode: null,             // "inventory" | "all" | "selected"
    progress: null,         // {index,total}
    counts: { ok: 0, skipped: 0, failed: 0 },
    summary: null,
    logs: [],
    warnings: [],
    lastError: null,
  };

  const $ = (sel) => document.querySelector(sel);
  const api = () => window.pywebview.api;

  // ---------- helpers ----------
  function show(screen) {
    state.screen = screen;
    for (const s of ["start", "catalog", "progress"]) {
      $("#screen-" + s).classList.toggle("hidden", s !== screen);
    }
  }

  function options() {
    return {
      dest: $("#dest").value.trim(),
      extract_zips: $("#opt-extract").checked,
      skip_existing: $("#opt-skip").checked,
      nsfw: $("#opt-nsfw").checked,
      thumbs: $("#opt-thumbs").checked,
    };
  }

  let saveTimer = null;
  function persist() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      const o = options();
      api().save_settings({
        last_url: $("#url").value.trim(), dest: o.dest, extract_zips: o.extract_zips,
        skip_existing: o.skip_existing, nsfw: o.nsfw, thumbs: o.thumbs,
      }).catch(() => {});
    }, 300);
  }

  function setError(where, text) {
    const el = $(where);
    if (!text) { el.classList.add("hidden"); el.textContent = ""; return; }
    el.textContent = text; el.classList.remove("hidden");
  }

  function log(level, text) {
    state.logs.push({ level, text });
    if (state.logs.length > 2000) state.logs.shift();
    const el = $("#log");
    const line = document.createElement("div");
    line.className = level; line.textContent = text;
    el.appendChild(line);
    while (el.childElementCount > 600) el.removeChild(el.firstChild);
    el.scrollTop = el.scrollHeight;
  }

  // ---------- session banner ----------
  function setSession(stateName, url) {
    state.session = stateName;
    const banner = $("#session-banner");
    const text = $("#session-text");
    banner.classList.remove("ready", "loading");
    if (stateName === "challenge") {
      text.textContent = "The site is asking for a human check. Click \"Verify you are human\" in the browser window, then wait a moment.";
      banner.classList.remove("hidden");
    } else if (stateName === "loading") {
      text.textContent = "Browser window is loading the site...";
      banner.classList.add("loading"); banner.classList.remove("hidden");
    } else if (stateName === "ready") {
      text.textContent = "Site session ready.";
      banner.classList.add("ready"); banner.classList.remove("hidden");
      setTimeout(() => { if (state.session === "ready") banner.classList.add("hidden"); }, 2500);
    } else {
      banner.classList.add("hidden");
    }
  }

  // ---------- catalog ----------
  function loadCatalog(cat, warnings) {
    state.catalog = cat;
    state.items = [];
    state.byId = {};
    for (const sec of cat.sections) for (const it of sec.items) { state.items.push(it); state.byId[it.id] = it; }
    state.selected = new Set(state.items.map((i) => i.id));
    state.collapsed = new Set();
    state.thumbs = {}; state.thumbsDone = 0;
    state.warnings = warnings || [];
    $("#cat-title").textContent = cat.game_title;
    $("#cat-sub").textContent = `${cat.console_title} • ${state.items.length} sheets in ${cat.sections.length} sections`;
    const w = $("#cat-warnings");
    w.textContent = state.warnings.join(" "); w.classList.toggle("hidden", !state.warnings.length);
    $("#foot-dest").textContent = $("#dest").value.trim();
    renderCatalog();
  }

  function visibleItems(sec) {
    let items = sec.items.slice();
    const f = state.filter.trim().toLowerCase();
    if (f) {
      const secMatch = sec.name.toLowerCase().includes(f);
      items = items.filter((i) => secMatch || i.title.toLowerCase().includes(f));
    }
    if (state.onlySelected) items = items.filter((i) => state.selected.has(i.id));
    if (state.sort === "name") items.sort((a, b) => a.title.localeCompare(b.title, undefined, { numeric: true }));
    else if (state.sort === "name-desc") items.sort((a, b) => b.title.localeCompare(a.title, undefined, { numeric: true }));
    else items.sort((a, b) => a.order - b.order);
    return items;
  }

  function renderCatalog() {
    const root = $("#sections");
    root.innerHTML = "";
    if (!state.catalog) return;
    const tplS = $("#tpl-section").content;
    const tplI = $("#tpl-item").content;
    for (const sec of state.catalog.sections) {
      const items = visibleItems(sec);
      if (!items.length && (state.filter.trim() || state.onlySelected)) continue;
      const node = tplS.cloneNode(true);
      const el = node.querySelector(".section");
      el.dataset.id = sec.id;
      el.classList.toggle("collapsed", state.collapsed.has(sec.id));
      el.querySelector(".sec-name").textContent = sec.name;
      const grid = el.querySelector(".grid");
      for (const it of items) {
        const n = tplI.cloneNode(true);
        const card = n.querySelector(".item");
        card.dataset.id = it.id;
        card.title = it.title;
        card.querySelector(".item-title").textContent = it.title;
        const img = card.querySelector("img");
        img.dataset.id = it.id;
        if (state.thumbs[it.id]) img.src = state.thumbs[it.id];
        grid.appendChild(n);
      }
      root.appendChild(node);
    }
    refreshSelection();
  }

  function refreshSelection() {
    for (const card of document.querySelectorAll(".item")) {
      const on = state.selected.has(card.dataset.id);
      card.classList.toggle("selected", on);
      card.querySelector(".item-check").checked = on;
    }
    if (state.catalog) {
      for (const el of document.querySelectorAll(".section")) {
        const sec = state.catalog.sections.find((s) => s.id === el.dataset.id);
        const n = sec.items.filter((i) => state.selected.has(i.id)).length;
        const cb = el.querySelector(".sec-check");
        cb.checked = n === sec.items.length && n > 0;
        cb.indeterminate = n > 0 && n < sec.items.length;
        el.querySelector(".sec-count").textContent = `${n} / ${sec.items.length} selected`;
      }
    }
    const count = state.selected.size;
    $("#cat-selected").textContent = `${count} of ${state.items.length} selected`;
    $("#btn-download-selected").disabled = count === 0;
    $("#btn-download-selected").textContent = count ? `Download ${count} selected` : "Nothing selected";
  }

  function setSelected(ids, on) {
    for (const id of ids) { if (on) state.selected.add(id); else state.selected.delete(id); }
    refreshSelection();
  }

  function shownIds() {
    return Array.from(document.querySelectorAll(".section:not(.collapsed) .item, .section.collapsed .item")).map((c) => c.dataset.id);
  }

  function applyThumbs(map) {
    for (const [id, url] of Object.entries(map)) {
      if (!url) continue;
      state.thumbs[id] = url;
      const img = document.querySelector(`img.thumb[data-id="${id}"]`);
      if (img) img.src = url;
    }
  }

  // ---------- jobs ----------
  async function checkUrl() {
    const r = await api().validate_url($("#url").value);
    $("#url-status").textContent = r.ok ? `OK: ${r.console} / ${r.game}` : r.error;
    $("#url-status").style.color = r.ok ? "var(--ok)" : "var(--fail)";
    return r;
  }

  async function startJob(mode) {
    setError("#start-error", null);
    const r = await checkUrl();
    if (!r.ok) return false;
    if (!$("#dest").value.trim()) { setError("#start-error", "Choose a download folder first."); return false; }
    persist();
    state.mode = mode;
    state.lastError = null;
    let res;
    if (mode === "inventory") {
      $("#start-status").textContent = "Inventorying...";
      res = await api().start_inventory(r.url, options());
    } else if (mode === "all") {
      resetProgress("Downloading everything...");
      show("progress");
      res = await api().start_download(r.url, options(), null);
    } else {
      resetProgress(`Downloading ${state.selected.size} selected sheets...`);
      show("progress");
      res = await api().start_download(r.url, options(), Array.from(state.selected));
    }
    if (!res.ok) { setError(state.screen === "progress" ? "#prog-error" : "#start-error", res.error); }
    return res.ok;
  }

  function resetProgress(title) {
    state.progress = null; state.summary = null; state.counts = { ok: 0, skipped: 0, failed: 0 }; state.logs = [];
    $("#prog-title").textContent = title;
    $("#prog-bar").style.width = "0%";
    $("#prog-text").textContent = "";
    $("#prog-item").textContent = "";
    $("#prog-thumb").removeAttribute("src");
    $("#log").innerHTML = "";
    setError("#prog-error", null);
    for (const k of ["ok", "skip", "fail"]) $("#cnt-" + k).textContent = "0";
    $("#btn-cancel").classList.remove("hidden");
    $("#btn-cancel").disabled = false;
    for (const id of ["#btn-open-folder", "#btn-to-catalog", "#btn-start-over"]) $(id).classList.add("hidden");
  }

  function finishProgress() {
    $("#btn-cancel").classList.add("hidden");
    $("#btn-open-folder").classList.remove("hidden");
    $("#btn-to-catalog").classList.toggle("hidden", !state.catalog);
    $("#btn-start-over").classList.remove("hidden");
  }

  // ---------- events from Python ----------
  function handleEvent(ev) {
    switch (ev.type) {
      case "session": setSession(ev.state, ev.url); break;
      case "status":
        $("#start-status").textContent = ev.text;
        if (state.screen === "progress") $("#prog-text").textContent = ev.text;
        break;
      case "catalog":
        loadCatalog(ev.catalog, ev.warnings);
        if (state.mode === "inventory") { show("catalog"); $("#start-status").textContent = ""; }
        break;
      case "thumbs":
        applyThumbs(ev.thumbs);
        state.thumbsDone = ev.done;
        $("#thumb-progress").textContent = ev.done < ev.total ? `Loading thumbnails ${ev.done} / ${ev.total}...` : "";
        $("#thumb-progress").classList.toggle("hidden", ev.done >= ev.total);
        break;
      case "inventory_done":
        $("#thumb-progress").classList.add("hidden");
        break;
      case "download_start":
        state.progress = { index: 0, total: ev.total };
        $("#prog-text").textContent = `0 / ${ev.total} • saving to ${ev.dest}`;
        state.destDir = ev.dest;
        log("ok", `Saving to ${ev.dest}`);
        break;
      case "progress": {
        state.progress = { index: ev.index, total: ev.total };
        $("#prog-bar").style.width = `${(ev.index / ev.total) * 100}%`;
        $("#prog-text").textContent = `${ev.index} / ${ev.total}`;
        $("#prog-item").textContent = `${ev.item.section} › ${ev.item.title}`;
        const t = state.thumbs[ev.item.id];
        if (t) $("#prog-thumb").src = t; else $("#prog-thumb").removeAttribute("src");
        break;
      }
      case "item_done": {
        const r = ev.result;
        const n = ev.index + 1;
        $("#prog-bar").style.width = `${(n / ev.total) * 100}%`;
        $("#prog-text").textContent = `${n} / ${ev.total}`;
        if (r.status === "ok") { state.counts.ok++; log("ok", `✓ ${ev.item.title}${r.extracted ? " (extracted)" : ""}`); }
        else if (r.status === "skipped") { state.counts.skipped++; log("skip", `– ${ev.item.title} (already exists)`); }
        else if (r.status === "cancelled") { log("warn", `cancelled at ${ev.item.title}`); }
        else { state.counts.failed++; log("fail", `✗ ${ev.item.title}: ${r.error || "failed"}`); }
        $("#cnt-ok").textContent = state.counts.ok;
        $("#cnt-skip").textContent = state.counts.skipped;
        $("#cnt-fail").textContent = state.counts.failed;
        break;
      }
      case "log": log(ev.level || "warn", ev.text); break;
      case "download_done":
        state.summary = ev.summary;
        $("#prog-title").textContent = ev.summary.cancelled ? "Cancelled" : (ev.summary.failed ? "Finished with some failures" : "All done");
        $("#prog-text").textContent = `${ev.summary.ok} downloaded, ${ev.summary.skipped} skipped, ${ev.summary.failed} failed in ${ev.summary.seconds}s`;
        $("#prog-bar").style.width = "100%";
        finishProgress();
        break;
      case "cancelled":
        state.lastError = "cancelled";
        if (state.screen === "progress") { $("#prog-title").textContent = "Cancelled"; finishProgress(); }
        else { $("#start-status").textContent = "Cancelled."; }
        break;
      case "error":
        state.lastError = ev.text;
        if (state.screen === "progress") { setError("#prog-error", ev.text); finishProgress(); }
        else { setError("#start-error", ev.text); $("#start-status").textContent = ""; }
        if (ev.detail) console.error(ev.detail);
        break;
      default: break;
    }
  }

  // ---------- wiring ----------
  async function init() {
    state.info = await api().app_info();
    state.settings = await api().get_settings();
    $("#version").textContent = "v" + state.info.version;
    $("#url").value = state.settings.last_url || "";
    $("#dest").value = state.settings.dest || "";
    $("#opt-extract").checked = !!state.settings.extract_zips;
    $("#opt-skip").checked = !!state.settings.skip_existing;
    $("#opt-nsfw").checked = !!state.settings.nsfw;
    $("#opt-thumbs").checked = state.settings.thumbs !== false;

    $("#url").addEventListener("input", () => { $("#url-status").textContent = ""; persist(); });
    $("#url").addEventListener("change", checkUrl);
    $("#url").addEventListener("keydown", (e) => { if (e.key === "Enter") startJob("inventory"); });
    $("#dest").addEventListener("input", persist);
    for (const id of ["#opt-extract", "#opt-skip", "#opt-nsfw", "#opt-thumbs"]) $(id).addEventListener("change", persist);

    const browse = async () => {
      const p = await api().pick_folder($("#dest").value.trim());
      if (p) { $("#dest").value = p; $("#foot-dest").textContent = p; persist(); }
    };
    $("#btn-browse").addEventListener("click", browse);
    $("#btn-foot-browse").addEventListener("click", browse);
    $("#btn-show-browser").addEventListener("click", () => api().show_browser());
    $("#btn-credits").addEventListener("click", () => $("#credits").classList.remove("hidden"));
    $("#btn-credits-close").addEventListener("click", () => $("#credits").classList.add("hidden"));
    $("#credits").addEventListener("click", (e) => { if (e.target === e.currentTarget) $("#credits").classList.add("hidden"); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#credits").classList.add("hidden"); });
    document.addEventListener("click", (e) => {
      const b = e.target.closest(".open-url");
      if (b) api().open_url(b.dataset.url);
    });

    $("#btn-all").addEventListener("click", () => startJob("all"));
    $("#btn-inventory").addEventListener("click", () => startJob("inventory"));

    $("#btn-back").addEventListener("click", () => show("start"));
    $("#btn-select-all").addEventListener("click", () => setSelected(state.items.map((i) => i.id), true));
    $("#btn-deselect-all").addEventListener("click", () => setSelected(state.items.map((i) => i.id), false));
    $("#btn-invert").addEventListener("click", () => {
      const next = new Set(state.items.map((i) => i.id).filter((id) => !state.selected.has(id)));
      state.selected = next; refreshSelection();
    });
    $("#btn-select-shown").addEventListener("click", () => setSelected(shownIds(), true));
    $("#btn-deselect-shown").addEventListener("click", () => setSelected(shownIds(), false));
    $("#filter").addEventListener("input", (e) => { state.filter = e.target.value; renderCatalog(); });
    $("#sort").addEventListener("change", (e) => { state.sort = e.target.value; renderCatalog(); });
    $("#thumb-size").addEventListener("change", (e) => document.documentElement.style.setProperty("--thumb", e.target.value + "px"));
    $("#only-selected").addEventListener("change", (e) => { state.onlySelected = e.target.checked; renderCatalog(); });
    $("#btn-expand").addEventListener("click", () => { state.collapsed.clear(); renderCatalog(); });
    $("#btn-collapse").addEventListener("click", () => { state.catalog.sections.forEach((s) => state.collapsed.add(s.id)); renderCatalog(); });

    $("#sections").addEventListener("click", (e) => {
      const secEl = e.target.closest(".section");
      if (!secEl) return;
      const sec = state.catalog.sections.find((s) => s.id === secEl.dataset.id);
      if (e.target.closest(".caret")) {
        if (state.collapsed.has(sec.id)) state.collapsed.delete(sec.id); else state.collapsed.add(sec.id);
        secEl.classList.toggle("collapsed");
        return;
      }
      if (e.target.classList.contains("sec-check")) { setSelected(sec.items.map((i) => i.id), e.target.checked); return; }
      if (e.target.closest(".sec-only")) { state.selected = new Set(sec.items.map((i) => i.id)); refreshSelection(); return; }
      const card = e.target.closest(".item");
      if (card) {
        const id = card.dataset.id;
        const on = e.target.classList.contains("item-check") ? e.target.checked : !state.selected.has(id);
        setSelected([id], on);
      }
    });
    $("#sections").addEventListener("keydown", (e) => {
      const card = e.target.closest(".item");
      if (card && (e.key === " " || e.key === "Enter")) { e.preventDefault(); setSelected([card.dataset.id], !state.selected.has(card.dataset.id)); }
    });

    $("#btn-download-selected").addEventListener("click", () => startJob("selected"));
    $("#btn-cancel").addEventListener("click", () => { $("#btn-cancel").disabled = true; api().cancel(); });
    $("#btn-open-folder").addEventListener("click", () => api().open_folder((state.summary && state.summary.dest) || state.destDir || $("#dest").value));
    $("#btn-to-catalog").addEventListener("click", () => { show("catalog"); refreshSelection(); });
    $("#btn-start-over").addEventListener("click", () => { show("start"); $("#start-status").textContent = ""; });

    if ($("#url").value) checkUrl();
    state.ready = true;
  }

  window.addEventListener("pywebviewready", init);

  // ---------- public (also used by the automated offline test) ----------
  return {
    get ready() { return state.ready; },
    handleEvent,
    setUrl: (u) => { $("#url").value = u; },
    setDest: (d) => { $("#dest").value = d; $("#foot-dest").textContent = d; },
    setOption: (name, on) => { $("#opt-" + name).checked = !!on; },
    inventory: () => startJob("inventory"),
    downloadAll: () => startJob("all"),
    downloadSelected: () => startJob("selected"),
    selectAll: () => setSelected(state.items.map((i) => i.id), true),
    deselectAll: () => setSelected(state.items.map((i) => i.id), false),
    toggleItem: (id) => setSelected([String(id)], !state.selected.has(String(id))),
    setFilter: (f) => { state.filter = f; $("#filter").value = f; renderCatalog(); },
    startOver: () => show("start"),
    getState: () => ({
      ready: state.ready, screen: state.screen, session: state.session, mode: state.mode,
      itemCount: state.items.length, selectedCount: state.selected.size, thumbCount: Object.keys(state.thumbs).length,
      sectionCount: state.catalog ? state.catalog.sections.length : 0,
      progress: state.progress, counts: state.counts, summary: state.summary, lastError: state.lastError,
      warnings: state.warnings, shownCount: document.querySelectorAll(".item").length,
      lastLog: state.logs.length ? state.logs[state.logs.length - 1].text : null,
    }),
  };
})();
