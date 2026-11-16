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
})();
