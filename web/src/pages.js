// Pages outside the two aspects: #account, #developers, #admin and #data open as a full-height page over
// the map; the map, the aspect switch and the view chrome stay underneath (inert) until the page closes.
// This module also owns the sign-in state shown in the header and in the page bar.
import { h } from "./dom.js";
import { configured, myProfile, onAuth, signOut as backendSignOut } from "./backend.js";
import { isStaff } from "./ui.js";
import { renderDeveloper } from "./views/developer.js";
import { renderAdmin } from "./views/admin.js";
import { renderData } from "./views/data.js";
import { renderAccount } from "./views/account.js";
import { renderRegions } from "./views/regions.js";

const PAGES = {
  regions: { title: "Regions", render: renderRegions },
  account: { title: "Account", render: renderAccount },
  developers: { title: "Developers", render: renderDeveloper },
  admin: { title: "Admin", render: renderAdmin },
  data: { title: "Data sources", render: renderData },
};
const AFTER_SIGN_IN = "overfield.after-sign-in";

/** The page a hash names, or null: "#data" names "data"; "#access_token=" and "#dock-body" name nothing. */
export function pageFromHash(hash = window.location.hash) {
  const name = String(hash || "")
    .replace(/^#\/?/, "")
    .split(/[?&/=]/)[0]
    .toLowerCase();
  return Object.prototype.hasOwnProperty.call(PAGES, name) ? name : null;
}

function remember(key, value) {
  try {
    if (value == null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* private mode or storage off */
  }
}

function recall(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function initPages({ dataPromise }) {
  const $ = (id) => document.getElementById(id);
  const page = $("page");
  const content = $("page-content");
  const title = $("page-title");
  const scroll = page.querySelector(".page-scroll");
  const navLinks = [...page.querySelectorAll(".page-nav a")];
  const adminLink = $("page-admin-link");
  const slots = [$("auth"), $("page-auth")].filter(Boolean);
  const covered = ["app", "intro", "place-card"].map($).filter(Boolean);
  const baseTitle = document.title;

  const auth = { user: null, profile: null, profileError: null, ready: !configured };
  const listeners = new Set();
  let current = null;
  let profileToken = 0;

  const ctx = {
    auth,
    dataPromise,
    go,
    close: closePage,
    onAuth(cb) {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    refreshProfile: loadProfile,
    setProfile(row) {
      auth.profile = row;
      notify();
    },
    signOut: doSignOut,
    rememberAfterSignIn: (name) => remember(AFTER_SIGN_IN, name),
  };

  function notify() {
    renderAuthSlots();
    adminLink.hidden = !isStaff(auth.profile);
    for (const cb of listeners) cb(auth);
  }

  /** Something the open page depends on changed (sign-in, sign-out, the profile arriving): draw it again. */
  function transition() {
    notify();
    if (current) openPage(current.name, { keepFocus: true });
  }

  function closeMenus(except) {
    for (const menu of document.querySelectorAll("details.menu[open]")) if (menu !== except) menu.removeAttribute("open");
  }

  function renderAuthSlots() {
    for (const slot of slots) {
      if (!auth.user) {
        slot.replaceChildren(
          h("button", { class: "btn btn-ghost auth-btn", type: "button", onclick: () => go("account") }, "Sign in"),
        );
        continue;
      }
      const name = (auth.profile?.name || "").trim();
      const label = name || auth.user.email || auth.user.phone || "Account";
      const initial = (label[0] || "?").toUpperCase();
      const item = (text, onclick) => h("button", { class: "menu-item", type: "button", role: "menuitem", onclick }, text);
      const menu = h(
        "details",
        { class: "menu" },
        h(
          "summary",
          { class: "btn btn-ghost auth-btn", "aria-label": `Signed in as ${label}` },
          h("span", { class: "avatar", "aria-hidden": "true" }, initial),
          h("span", { class: "auth-name" }, label),
        ),
        h(
          "div",
          { class: "menu-list", role: "menu" },
          h("div", { class: "menu-head small muted" }, auth.user.email || auth.user.phone || ""),
          item("Account", () => {
            menu.removeAttribute("open");
            go("account");
          }),
          isStaff(auth.profile)
            ? item("Admin", () => {
                menu.removeAttribute("open");
                go("admin");
              })
            : null,
          item("Sign out", () => {
            menu.removeAttribute("open");
            doSignOut();
          }),
        ),
      );
      menu.addEventListener("toggle", () => {
        if (menu.open) closeMenus(menu);
      });
      slot.replaceChildren(menu);
    }
  }

  async function loadProfile() {
    const token = ++profileToken;
    const user = auth.user;
    if (!user) return null;
    try {
      const row = await myProfile(user);
      if (token !== profileToken) return row;
      auth.profile = row;
      auth.profileError = null;
    } catch (err) {
      if (token !== profileToken) return null;
      auth.profile = null;
      auth.profileError = err;
    }
    transition();
    return auth.profile;
  }

  async function doSignOut() {
    try {
      await backendSignOut();
    } catch (err) {
      console.error(err);
    }
  }

  function setAuthUser(user) {
    const changed = (user?.id || null) !== (auth.user?.id || null);
    auth.user = user || null;
    if (!user) {
      auth.profile = null;
      auth.profileError = null;
      profileToken += 1;
    }
    return changed;
  }

  if (configured) {
    onAuth((session, event) => {
      const user = session?.user || null;
      const changed = setAuthUser(user);
      const firstLook = !auth.ready;
      auth.ready = true;
      if (changed && user) {
        // Fetching inside the auth callback would wait on the client's own lock, so it goes to the next tick.
        window.setTimeout(loadProfile, 0);
      }
      if (changed || firstLook || event === "SIGNED_OUT") transition();
      if (event === "SIGNED_IN" && changed) {
        const next = recall(AFTER_SIGN_IN);
        if (next) {
          remember(AFTER_SIGN_IN, null);
          if (pageFromHash(`#${next}`)) go(next);
        }
      }
    });
  } else {
    notify();
  }

  function setCovered(on) {
    for (const el of covered) {
      if (on) {
        el.setAttribute("inert", "");
        el.setAttribute("aria-hidden", "true");
      } else {
        el.removeAttribute("inert");
        el.removeAttribute("aria-hidden");
      }
    }
  }

  function openPage(name, { keepFocus = false } = {}) {
    const def = PAGES[name];
    if (!def) return;
    const wasOpen = Boolean(current);
    current?.api?.destroy?.();
    page.hidden = false;
    document.body.classList.add("has-page");
    setCovered(true);
    document.title = `${def.title} / ${baseTitle}`;
    title.textContent = def.title;
    for (const a of navLinks) {
      if (a.getAttribute("href") === `#${name}`) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    content.replaceChildren();
    const api = def.render(content, ctx) || {};
    current = { name, api };
    if (!keepFocus) {
      scroll.scrollTop = 0;
      if (!wasOpen) title.focus({ preventScroll: true });
    }
  }

  function closePage() {
    if (!current) return;
    current.api?.destroy?.();
    current = null;
    closeMenus();
    page.hidden = true;
    document.body.classList.remove("has-page");
    setCovered(false);
    document.title = baseTitle;
    if (pageFromHash()) window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }

  function route() {
    const name = pageFromHash();
    if (name) openPage(name);
    else closePage();
  }

  function go(name) {
    if (window.location.hash === `#${name}`) route();
    else window.location.hash = name;
  }

  $("page-back").addEventListener("click", closePage);
  window.addEventListener("hashchange", route);
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element) || !event.target.closest("details.menu")) closeMenus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenus();
  });

  notify();
  route();

  return {
    go,
    close: closePage,
    isOpen: () => Boolean(current),
    rememberAfterSignIn: (name) => remember(AFTER_SIGN_IN, name),
  };
}

export { AFTER_SIGN_IN, remember as rememberLocal };
