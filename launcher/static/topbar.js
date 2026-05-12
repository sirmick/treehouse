// Treehouse top bar — injected by host nginx (sub_filter) into every
// non-launcher content page (kiwix vhosts today, kolibri/peertube/etc.
// when those land). One file, no build step, no framework.
//
// Reads window.__TREEHOUSE_TOPBAR__ which nginx writes inline before
// this script loads. Shape:
//   {
//     hostnames: { <id>: { internal, public }, ... },
//     mode: "lan" | "isolated"
//   }
//
// The bar lives in a shadow root so the host page's CSS can't leak in
// and ours can't leak out. We push the page content down by adjusting
// document.body.style.paddingTop after mount.

(function () {
  const cfg = window.__TREEHOUSE_TOPBAR__;
  if (!cfg || !cfg.hostnames) return;

  const activeHost = (id) => {
    const h = cfg.hostnames[id];
    if (!h) return null;
    return cfg.mode === "lan" ? h.public : h.internal;
  };

  // Aesthetic choices live here; the host map comes from cfg. Each
  // entry must reference an id present in treehouse.yml hostnames.
  // Entries whose id isn't in cfg.hostnames are skipped at render
  // time (so a fresh-clone deploy without `books` still renders the
  // rest cleanly).
  const QUICKLINKS = [
    { id: "wikipedia", emoji: "📚", label: "Wikipedia" },
    { id: "dictionary", emoji: "📖", label: "Dictionary" },
    { id: "vikidia", emoji: "🌱", label: "Vikidia" },
    { id: "books", emoji: "📕", label: "Books" },
    { id: "maps", emoji: "🗺️", label: "Maps" },
  ];

  const homeHost = activeHost("home");
  const searchHost = activeHost("search");
  if (!homeHost || !searchHost) return;

  // Outer wrapper sits in light DOM only as an anchor for the shadow.
  // Shadow root already isolates inner styles from the host page; the
  // host itself just needs to be a visible block so the fixed bar's
  // siblings (any focused outline rings, dropdowns) compose normally.
  const wrap = document.createElement("div");
  wrap.id = "__treehouse_topbar";
  wrap.style.cssText = "display: block; position: static;";
  const root = wrap.attachShadow({ mode: "open" });

  root.innerHTML = `
    <style>
      :host {
        all: initial;
      }
      .bar {
        position: fixed;
        top: 0; left: 0; right: 0;
        height: 56px;
        background: #ffffff;
        border-bottom: 1px solid #e2e8f0;
        box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 0 16px;
        z-index: 2147483647;
        font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
        font-size: 14px;
        color: #1e293b;
        box-sizing: border-box;
      }
      a.logo {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        text-decoration: none;
        color: #0f172a;
        font-weight: 600;
        white-space: nowrap;
        padding: 6px 10px;
        border-radius: 12px;
      }
      a.logo:hover { background: #f1f5f9; }
      a.logo .mark { font-size: 22px; line-height: 1; }
      form.search {
        flex: 1;
        max-width: 540px;
      }
      input.q {
        width: 100%;
        height: 38px;
        padding: 0 16px;
        border: 1px solid #e2e8f0;
        border-radius: 9999px;
        background: #f8fafc;
        font: inherit;
        color: inherit;
        outline: none;
        box-sizing: border-box;
        transition: border-color 0.15s, background 0.15s;
      }
      input.q:focus {
        border-color: #7dd3fc;
        background: #ffffff;
        box-shadow: 0 0 0 4px rgba(125, 211, 252, 0.25);
      }
      nav.links {
        display: flex;
        gap: 4px;
        flex-wrap: nowrap;
      }
      nav.links a {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 8px 10px;
        border-radius: 12px;
        text-decoration: none;
        color: #475569;
        white-space: nowrap;
      }
      nav.links a:hover { background: #f1f5f9; color: #0f172a; }
      nav.links a .emoji { font-size: 18px; line-height: 1; }
      nav.links a .label { display: none; }
      @media (min-width: 720px) {
        nav.links a .label { display: inline; }
      }
      @media (max-width: 480px) {
        a.logo .text { display: none; }
        .bar { gap: 8px; padding: 0 10px; }
      }
    </style>
    <header class="bar" role="banner">
      <a class="logo" href="https://${homeHost}/">
        <span class="mark" aria-hidden="true">🏠</span>
        <span class="text">Treehouse</span>
      </a>
      <form class="search" method="get" action="https://${searchHost}/search">
        <input class="q" type="search" name="q" placeholder="Search…" autocomplete="off" aria-label="Search Treehouse" />
      </form>
      <nav class="links" aria-label="Quick links">
        ${QUICKLINKS
          .map((l) => {
            const host = activeHost(l.id);
            if (!host) return "";
            return `<a href="https://${host}/" title="${l.label}"><span class="emoji" aria-hidden="true">${l.emoji}</span><span class="label">${l.label}</span></a>`;
          })
          .join("")}
      </nav>
    </header>
  `;

  const mount = () => {
    document.body.prepend(wrap);
    // Push page content down. Most hosts (Kiwix) have no top padding;
    // for the rare page that does we still want at least 56px clear.
    const existing = parseFloat(getComputedStyle(document.body).paddingTop) || 0;
    document.body.style.paddingTop = `${Math.max(existing, 56)}px`;
  };

  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount, { once: true });
})();
