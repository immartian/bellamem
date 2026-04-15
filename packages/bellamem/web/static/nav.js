// Shared helper: rewrite topbar nav links to include the current
// /p/<id>/ prefix. Imported (or duplicated) by every per-project view.
export function rewriteNavLinks() {
  const prefix = location.pathname.match(/^\/p\/[^/]+/)?.[0] ?? "";
  const nav = document.querySelector("header.topbar nav");
  if (!nav) return;
  // Prepend a "projects" breadcrumb if not already present.
  if (!nav.querySelector(".nav-projects")) {
    const crumb = document.createElement("a");
    crumb.href = "/";
    crumb.textContent = "← projects";
    crumb.className = "nav-projects";
    crumb.style.marginRight = "14px";
    crumb.style.color = "var(--fg-dim)";
    nav.prepend(crumb);
  }
  // Rewrite the static nav hrefs to scope them under the current project.
  for (const a of nav.querySelectorAll("a")) {
    const href = a.getAttribute("href");
    if (!href) continue;
    if (href === "/overview" || href === "/graph" || href === "/trace") {
      a.setAttribute("href", prefix + href);
    }
  }
}
