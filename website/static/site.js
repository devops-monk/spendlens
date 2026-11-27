// spendlens site: theme toggle, mobile menu, copy buttons. Progressive enhancement only.
(function () {
  const root = document.documentElement;
  try { const t = localStorage.getItem("spendlens-site-theme"); if (t) root.dataset.theme = t; } catch (e) {}
  const tb = document.querySelector(".theme-btn");
  if (tb) tb.addEventListener("click", () => {
    const cur = root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem("spendlens-site-theme", next); } catch (e) {}
  });
  const mb = document.querySelector(".menu-btn"), links = document.querySelector(".nav-links");
  if (mb && links) mb.addEventListener("click", () => {
    const open = links.classList.toggle("open"); mb.setAttribute("aria-expanded", String(open));
  });
  document.querySelectorAll(".prose pre, .steps pre, pre.copyable").forEach((pre) => {
    const b = document.createElement("button");
    b.className = "copy"; b.type = "button"; b.textContent = "Copy";
    b.addEventListener("click", async () => {
      const text = (pre.querySelector("code") || pre).innerText;
      try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; } catch (e) { b.textContent = "Select + copy"; }
      setTimeout(() => (b.textContent = "Copy"), 1500);
    });
    pre.appendChild(b);
  });
  // screenshot gallery: tabs with arrow-key navigation (all screenshots stay visible without JS)
  document.querySelectorAll(".gallery").forEach((g) => {
    const tabs = [...g.querySelectorAll('[role="tab"]')];
    const select = (t, focus) => {
      tabs.forEach((x) => {
        const on = x === t;
        x.setAttribute("aria-selected", String(on));
        x.tabIndex = on ? 0 : -1;
        const panel = document.getElementById(x.getAttribute("aria-controls"));
        if (panel) panel.toggleAttribute("data-hidden", !on);
      });
      if (focus) t.focus();
    };
    tabs.forEach((t, i) => {
      t.addEventListener("click", () => select(t, false));
      t.addEventListener("keydown", (e) => {
        const k = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
        if (k) { e.preventDefault(); select(tabs[(i + k + tabs.length) % tabs.length], true); }
        if (e.key === "Home") { e.preventDefault(); select(tabs[0], true); }
        if (e.key === "End") { e.preventDefault(); select(tabs[tabs.length - 1], true); }
      });
    });
  });
  // docs sidebar: filter as you type
  const filter = document.querySelector(".doc-filter input");
  if (filter) filter.addEventListener("input", () => {
    const q = filter.value.trim().toLowerCase();
    let shown = 0;
    document.querySelectorAll(".docgroup").forEach((g) => {
      let any = false;
      g.querySelectorAll("li").forEach((li) => {
        const hit = !q || li.textContent.toLowerCase().includes(q) || g.querySelector("h4").textContent.toLowerCase().includes(q);
        li.hidden = !hit; if (hit) { any = true; shown++; }
      });
      g.hidden = !any;
    });
    const empty = document.querySelector(".doc-empty");
    if (empty) empty.hidden = shown > 0;
  });
  // docs sidebar on phones: collapsed behind "Browse docs"
  const dt = document.querySelector(".docnav-toggle");
  if (dt) dt.addEventListener("click", () => {
    const open = dt.closest(".sidebar").classList.toggle("open");
    dt.setAttribute("aria-expanded", String(open));
  });
  // "On this page": highlight the section being read
  const tocLinks = [...document.querySelectorAll('.toc a[href^="#"]:not(.toc-top)')];
  const heads = tocLinks.map((a) => document.getElementById(decodeURIComponent(a.hash.slice(1)))).filter(Boolean);
  if (heads.length && "IntersectionObserver" in window) {
    const mark = (id) => tocLinks.forEach((a) => a.classList.toggle("active", a.hash.slice(1) === id));
    const visible = new Set();
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => (e.isIntersecting ? visible.add(e.target.id) : visible.delete(e.target.id)));
      const first = heads.find((h) => visible.has(h.id));
      if (first) mark(first.id);
    }, { rootMargin: "-60px 0px -65% 0px" });
    heads.forEach((h) => io.observe(h));
  }
  // architecture local nav: highlight the section in view
  const lnLinks = [...document.querySelectorAll('.localnav-links a[href^="#"]')];
  const lnSecs = lnLinks.map((a) => document.getElementById(a.hash.slice(1))).filter(Boolean);
  if (lnSecs.length && "IntersectionObserver" in window) {
    const seen = new Set();
    const io2 = new IntersectionObserver((entries) => {
      entries.forEach((e) => (e.isIntersecting ? seen.add(e.target.id) : seen.delete(e.target.id)));
      const cur = lnSecs.find((s) => seen.has(s.id));
      lnLinks.forEach((a) => a.classList.toggle("active", !!cur && a.hash.slice(1) === cur.id));
    }, { rootMargin: "-110px 0px -55% 0px" });
    lnSecs.forEach((s) => io2.observe(s));
  }
  // command catalog: filter commands (h3 blocks) by any text in their description, recipes or options
  const cf = document.querySelector(".prose .cmd-filter");
  if (cf) {
    const prose = cf.closest(".prose");
    const blocks = [];
    let cur = null, h2 = null;
    [...prose.children].forEach((el) => {
      if (el.tagName === "H2") { h2 = { el, items: [], rest: [] }; blocks.push(h2); cur = null; }
      else if (el.tagName === "H3" && h2) { cur = { el, parts: [el] }; h2.items.push(cur); }
      else if (cur) cur.parts.push(el);
      else if (h2) h2.rest.push(el);
    });
    const run = () => {
      const q = cf.value.trim().toLowerCase();
      blocks.forEach((b) => {
        let any = false;
        b.items.forEach((it) => {
          const hit = !q || it.parts.some((p) => p.textContent.toLowerCase().includes(q));
          it.parts.forEach((p) => (p.hidden = !hit)); if (hit) any = true;
        });
        const show = !q || any;
        b.el.hidden = !show; b.rest.forEach((r) => (r.hidden = !!q));
      });
    };
    let t; cf.addEventListener("input", () => { clearTimeout(t); t = setTimeout(run, 120); });
  }
  // cheat sheet: copy buttons and search
  document.querySelectorAll(".cs-copy").forEach((b) => b.addEventListener("click", async () => {
    const text = b.parentElement.querySelector("code").innerText;
    try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; b.classList.add("done"); }
    catch (e) { b.textContent = "Select + copy"; }
    setTimeout(() => { b.textContent = "Copy"; b.classList.remove("done"); }, 1400);
  }));
  const csq = document.querySelector(".cs-q");
  if (csq) {
    const secs = [...document.querySelectorAll(".cs-section")], none = document.querySelector(".cs-none");
    let tq;
    csq.addEventListener("input", () => { clearTimeout(tq); tq = setTimeout(() => {
      const q = csq.value.trim().toLowerCase(); let shown = 0;
      secs.forEach((sec) => {
        const head = sec.querySelector(".cs-head").textContent.toLowerCase();
        let n = 0;
        sec.querySelectorAll(".cs-card").forEach((c) => {
          const hit = !q || head.includes(q) || c.textContent.toLowerCase().includes(q);
          c.hidden = !hit; if (hit) n++;
        });
        sec.hidden = n === 0; shown += n;
      });
      if (none) none.hidden = shown > 0;
    }, 120); });
  }
})();
