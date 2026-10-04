/* UwenzAi-Dev docs — renderer + interactions (no framework, no build). */
/* global CONTENT -- defined in content.js, loaded before this script. */

(function () {
  "use strict";

  const FACT_ICONS = {
    bolt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M13 2 4.1 12.6a1 1 0 0 0 .8 1.6H11l-1.8 7.4 9.4-11a1 1 0 0 0-.8-1.6H12l1-6z"/></svg>',
    globe:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18 15 15 0 0 1 0-18z"/></svg>',
    gauge:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"/><path d="m14 10 3-3"/><path d="M3.5 15a9 9 0 1 1 17 0"/></svg>',
    shield:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3z"/><path d="m9 12 2 2 4-4"/></svg>',
  };

  const COPY_ICON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>';

  /* Cookie-based prefs (works on real origins; no-ops where storage is blocked) */
  function savePref(key, value) {
    try {
      document.cookie =
        "uwenzai-docs-" +
        key +
        "=" +
        encodeURIComponent(value) +
        ";max-age=31536000;path=/;SameSite=Lax";
    } catch (e) {
      /* storage unavailable — preference simply won't persist */
    }
  }
  function loadPref(key) {
    try {
      const m = document.cookie.match(
        new RegExp("(?:^|; )uwenzai-docs-" + key + "=([^;]*)"),
      );
      return m ? decodeURIComponent(m[1]) : null;
    } catch (e) {
      return null;
    }
  }

  /* ── Inline markup: `code`, **bold**, [text](url) ───── */
  function inline(text) {
    const escaped = String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    return escaped
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>")
      .replace(
        /\[([^\]]+)\]\(([^)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
      );
  }

  function esc(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  /* ── Block renderers ───────────────────────────────── */
  function renderCode(block) {
    return (
      '<div class="code-block">' +
      '<div class="code-head"><span class="code-title">' +
      esc(block.title || "text") +
      "</span>" +
      '<button type="button" class="copy-button" data-copy="' +
      esc(block.x) +
      '">' +
      COPY_ICON +
      '<span class="copy-label"></span></button></div>' +
      "<pre><code>" +
      esc(block.x) +
      "</code></pre></div>"
    );
  }

  function renderTable(block, chrome) {
    const head = block.head.map((h) => "<th>" + inline(h) + "</th>").join("");
    const rows = block.rows
      .map(
        (r) =>
          "<tr>" +
          r
            .map(
              (c, i) =>
                '<td data-label="' +
                esc(block.head[i]) +
                '">' +
                inline(c) +
                "</td>",
            )
            .join("") +
          "</tr>",
      )
      .join("");
    return (
      '<div class="table-wrap"><table><thead><tr>' +
      head +
      "</tr></thead><tbody>" +
      rows +
      "</tbody></table></div>" +
      '<p class="table-hint">' +
      esc(chrome.scrollHint) +
      "</p>"
    );
  }

  function renderEndpoint(block, chrome) {
    let html =
      '<div class="endpoint"><div class="endpoint-head">' +
      '<span class="method" data-method="' +
      block.method +
      '">' +
      block.method +
      "</span>" +
      '<span class="endpoint-path">' +
      esc(block.path) +
      "</span>" +
      '<span class="status-pill">' +
      esc(block.auth === "bearer" ? chrome.authBearer : chrome.authSession) +
      "</span></div>";
    html += "<p>" + inline(block.desc) + "</p>";
    if (block.params) {
      html += renderTable(
        {
          head: ["Field", "Type", "Required", "Description"],
          rows: block.params,
          hint: true,
        },
        chrome,
      );
    }
    if (block.request)
      html += renderCode({ title: "request", x: block.request });
    if (block.response)
      html += renderCode({ title: "response", x: block.response });
    html += "</div>";
    return html;
  }

  function renderBlocks(blocks, chrome) {
    return blocks
      .map((b) => {
        switch (b.t) {
          case "p":
            return "<p>" + inline(b.x) + "</p>";
          case "h3":
            return "<h3>" + inline(b.x) + "</h3>";
          case "h4":
            return "<h4>" + inline(b.x) + "</h4>";
          case "ul":
            return (
              "<ul>" +
              b.items.map((i) => "<li>" + inline(i) + "</li>").join("") +
              "</ul>"
            );
          case "ol":
            return (
              "<ol>" +
              b.items.map((i) => "<li>" + inline(i) + "</li>").join("") +
              "</ol>"
            );
          case "callout": {
            const icon =
              b.kind === "warn"
                ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3 2.5 20h19L12 3z"/><path d="M12 10v4m0 3v.5"/></svg>'
                : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8v.5"/></svg>';
            return (
              '<aside class="callout' +
              (b.kind === "warn" ? " is-warn" : "") +
              '">' +
              icon +
              "<div>" +
              inline(b.x) +
              "</div></aside>"
            );
          }
          case "code":
            return renderCode(b);
          case "table":
            return renderTable(b, chrome);
          case "endpoint":
            return renderEndpoint(b, chrome);
          case "steps":
            return (
              '<div class="steps">' +
              b.steps
                .map(
                  (s, i) =>
                    '<div class="step"><span class="step-num" aria-hidden="true">' +
                    (i + 1) +
                    "</span><div><h3>" +
                    inline(s.title) +
                    "</h3>" +
                    renderBlocks(s.body, chrome) +
                    "</div></div>",
                )
                .join("") +
              "</div>"
            );
          default:
            return "";
        }
      })
      .join("");
  }

  /* ── Section + nav build ───────────────────────────── */
  const nav = document.getElementById("sidebar-nav");
  const sectionsRoot = document.getElementById("sections");
  const chrome = CONTENT.chrome;
  let currentLang = "en";

  function buildAll() {
    const labels = CONTENT.groupLabels[currentLang];
    let navHtml = "";
    let lastGroup = null;
    CONTENT.nav.forEach((item) => {
      if (item.group !== lastGroup) {
        navHtml +=
          '<p class="sidebar-group-label">' + esc(labels[item.group]) + "</p>";
        lastGroup = item.group;
      }
      navHtml +=
        '<a href="#' +
        item.id +
        '" data-target="' +
        item.id +
        '">' +
        esc(item[currentLang]) +
        "</a>";
    });
    nav.innerHTML = navHtml;

    const foot = document.querySelector(".sidebar-foot");
    foot.textContent = chrome[currentLang].sidebarFoot;
    document.querySelectorAll("[data-chrome]").forEach((el) => {
      const key = el.getAttribute("data-chrome");
      if (chrome[currentLang][key]) el.textContent = chrome[currentLang][key];
    });

    const hero = CONTENT.hero[currentLang];
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const [group, key] = el.getAttribute("data-i18n").split(".");
      if (group === "hero") el.textContent = hero[key];
    });
    document.getElementById("hero-facts").innerHTML = hero.facts
      .map(
        (f) =>
          '<span class="fact-chip">' +
          FACT_ICONS[f.icon] +
          esc(f.text) +
          "</span>",
      )
      .join("");

    const secFoot = CONTENT.foot[currentLang];
    sectionsRoot.innerHTML = CONTENT.nav
      .map((item) => {
        const blocks = CONTENT.sections[item.id][currentLang];
        return (
          '<section class="section section-anchor" id="' +
          item.id +
          '" aria-labelledby="' +
          item.id +
          '-title">' +
          '<h2 id="' +
          item.id +
          '-title">' +
          esc(item[currentLang]) +
          "</h2>" +
          renderBlocks(blocks, chrome[currentLang]) +
          "</section>"
        );
      })
      .join("");
    document.querySelector(".foot p").textContent = secFoot.text;

    document.documentElement.lang = currentLang === "sw" ? "sw" : "en";
    document.title =
      currentLang === "sw"
        ? "UwenzAi-Dev — Nyaraka za API"
        : "UwenzAi-Dev — API Documentation";
    wireCopyButtons();
    observeSections();
  }

  /* ── Copy buttons ──────────────────────────────────── */
  function wireCopyButtons() {
    document.querySelectorAll(".copy-button").forEach((btn) => {
      const label = btn.querySelector(".copy-label");
      label.textContent = chrome[currentLang].copy;
      btn.addEventListener("click", async () => {
        const text = btn.getAttribute("data-copy");
        try {
          await navigator.clipboard.writeText(text);
        } catch {
          const ta = document.createElement("textarea");
          ta.value = text;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          ta.remove();
        }
        btn.classList.add("is-copied");
        label.textContent = chrome[currentLang].copied;
        setTimeout(() => {
          btn.classList.remove("is-copied");
          label.textContent = chrome[currentLang].copy;
        }, 1600);
      });
    });
  }

  /* ── Scrollspy ─────────────────────────────────────── */
  let spyTicking = false;
  function observeSections() {
    const links = nav.querySelectorAll("a[data-target]");
    const setActive = (id) => {
      links.forEach((l) =>
        l.classList.toggle("is-active", l.getAttribute("data-target") === id),
      );
    };
    const sections = Array.from(document.querySelectorAll(".section-anchor"));

    function update() {
      spyTicking = false;
      // The active section is the last one whose top has crossed 120px
      // from the viewport top — deterministic at section boundaries.
      const marker = window.scrollY + 100; // just past the 84px scroll-margin landing line
      let current = null;
      sections.forEach((s) => {
        if (s.getBoundingClientRect().top + window.scrollY <= marker)
          current = s.id;
      });
      if (!current && sections.length) current = sections[0].id;
      // At max scroll the last section may never cross the marker — pin it.
      if (
        sections.length &&
        window.innerHeight + window.scrollY >=
          document.documentElement.scrollHeight - 4
      ) {
        current = sections[sections.length - 1].id;
      }
      setActive(current);
    }

    if (window.__uwenzaiDocsSpy) {
      window.removeEventListener("scroll", window.__uwenzaiDocsSpy);
    }
    let lastSpyRun = 0;
    window.__uwenzaiDocsSpy = () => {
      const now = performance.now();
      if (now - lastSpyRun >= 80) {
        lastSpyRun = now;
        update();
      }
      clearTimeout(window.__uwenzaiDocsSpyTail);
      window.__uwenzaiDocsSpyTail = setTimeout(update, 120);
    };
    window.addEventListener("scroll", window.__uwenzaiDocsSpy, {
      passive: true,
    });
    update();
  }

  /* ── Language toggle ───────────────────────────────── */
  document.querySelectorAll(".lang-toggle button").forEach((btn) => {
    btn.addEventListener("click", () => {
      currentLang = btn.getAttribute("data-lang");
      document.querySelectorAll(".lang-toggle button").forEach((b) => {
        b.setAttribute("aria-pressed", String(b === btn));
      });
      savePref("lang", currentLang);
      buildAll();
      closeSidebar();
    });
  });

  const savedLang = loadPref("lang");
  if (savedLang === "sw" || savedLang === "en") {
    currentLang = savedLang;
    document.querySelectorAll(".lang-toggle button").forEach((b) => {
      b.setAttribute(
        "aria-pressed",
        String(b.getAttribute("data-lang") === currentLang),
      );
    });
  }

  /* ── Theme toggle ──────────────────────────────────── */
  const themeToggle = document.getElementById("theme-toggle");
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    savePref("theme", theme);
  }
  themeToggle.addEventListener("click", () => {
    const next =
      document.documentElement.getAttribute("data-theme") === "dark"
        ? "light"
        : "dark";
    applyTheme(next);
  });
  (function initTheme() {
    const saved = loadPref("theme");
    if (saved === "dark" || saved === "light") {
      document.documentElement.setAttribute("data-theme", saved);
    } else if (window.matchMedia("(prefers-color-scheme: dark)").matches) {
      document.documentElement.setAttribute("data-theme", "dark");
    }
  })();

  /* ── Mobile sidebar ────────────────────────────────── */
  const sidebar = document.getElementById("sidebar");
  const scrim = document.getElementById("scrim");
  const menuButton = document.getElementById("menu-button");

  function openSidebar() {
    sidebar.classList.add("is-open");
    scrim.hidden = false;
    menuButton.setAttribute("aria-expanded", "true");
  }
  function closeSidebar() {
    sidebar.classList.remove("is-open");
    scrim.hidden = true;
    menuButton.setAttribute("aria-expanded", "false");
  }
  menuButton.addEventListener("click", () => {
    if (sidebar.classList.contains("is-open")) closeSidebar();
    else openSidebar();
  });
  scrim.addEventListener("click", closeSidebar);
  nav.addEventListener("click", (event) => {
    if (event.target.closest("a")) closeSidebar();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSidebar();
  });

  /* ── Init ──────────────────────────────────────────── */
  buildAll();
})();
