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
  document.querySelectorAll(".prose pre").forEach((pre) => {
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
})();
