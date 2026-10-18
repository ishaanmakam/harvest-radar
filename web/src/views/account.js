import { h } from "../dom.js";
import { CHANNEL_LABEL, chip, titleCase } from "../format.js";
import { MAX_MATCH_KM, geocode, matchField, parseLatLon } from "../geo.js";
import { NOT_CONFIGURED, configured, deleteMyAccount, demoText, myDeliveries, signIn, signUp, updateMyProfile } from "../backend.js";
import { card, errorNote, field, fmtDateTime, fmtDay, loading, notice, select, statusLine } from "../ui.js";

// Mirrors LANGUAGE_NAMES in intelligence/providers.py.
const LANGUAGES = [
  ["en", "English"],
  ["hi", "Hindi"],
  ["pa", "Punjabi (Gurmukhi script)"],
];

const COUNTRY_CODES = [
  ["+1", "United States and Canada (+1)"],
  ["+52", "Mexico (+52)"],
  ["+55", "Brazil (+55)"],
  ["+54", "Argentina (+54)"],
  ["+57", "Colombia (+57)"],
  ["+51", "Peru (+51)"],
  ["+44", "United Kingdom (+44)"],
  ["+353", "Ireland (+353)"],
  ["+33", "France (+33)"],
  ["+49", "Germany (+49)"],
  ["+34", "Spain (+34)"],
  ["+39", "Italy (+39)"],
  ["+31", "Netherlands (+31)"],
  ["+48", "Poland (+48)"],
  ["+380", "Ukraine (+380)"],
  ["+90", "Turkey (+90)"],
  ["+20", "Egypt (+20)"],
  ["+234", "Nigeria (+234)"],
  ["+233", "Ghana (+233)"],
  ["+254", "Kenya (+254)"],
  ["+255", "Tanzania (+255)"],
  ["+256", "Uganda (+256)"],
  ["+251", "Ethiopia (+251)"],
  ["+27", "South Africa (+27)"],
  ["+91", "India (+91)"],
  ["+92", "Pakistan (+92)"],
  ["+880", "Bangladesh (+880)"],
  ["+94", "Sri Lanka (+94)"],
  ["+977", "Nepal (+977)"],
  ["+62", "Indonesia (+62)"],
  ["+63", "Philippines (+63)"],
  ["+66", "Thailand (+66)"],
  ["+84", "Vietnam (+84)"],
  ["+86", "China (+86)"],
  ["+81", "Japan (+81)"],
  ["+82", "South Korea (+82)"],
  ["+61", "Australia (+61)"],
  ["+64", "New Zealand (+64)"],
];

const TIMEZONES = [
  "America/Chicago",
  "America/New_York",
  "America/Denver",
  "America/Phoenix",
  "America/Los_Angeles",
  "America/Anchorage",
  "Pacific/Honolulu",
  "America/Toronto",
  "America/Winnipeg",
  "America/Vancouver",
  "America/Mexico_City",
  "America/Bogota",
  "America/Lima",
  "America/Sao_Paulo",
  "America/Argentina/Buenos_Aires",
  "Europe/London",
  "Europe/Dublin",
  "Europe/Paris",
  "Europe/Berlin",
  "Europe/Madrid",
  "Europe/Rome",
  "Europe/Warsaw",
  "Europe/Kyiv",
  "Europe/Istanbul",
  "Africa/Cairo",
  "Africa/Lagos",
  "Africa/Accra",
  "Africa/Nairobi",
  "Africa/Addis_Ababa",
  "Africa/Johannesburg",
  "Asia/Kolkata",
  "Asia/Karachi",
  "Asia/Dhaka",
  "Asia/Colombo",
  "Asia/Kathmandu",
  "Asia/Bangkok",
  "Asia/Jakarta",
  "Asia/Manila",
  "Asia/Ho_Chi_Minh",
  "Asia/Shanghai",
  "Asia/Tokyo",
  "Asia/Seoul",
  "Australia/Sydney",
  "Australia/Perth",
  "Pacific/Auckland",
  "UTC",
];

const CHANNELS = [
  ["sms", "Text (SMS)"],
  ["voice", "Voice call"],
  ["email", "Email"],
];

const CONSENT_LABEL = "Send my morning plan to this number by text and voice call. Reply STOP to end.";

function browserZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

function zoneOptions(current) {
  const zones = [...TIMEZONES];
  for (const extra of [browserZone(), current]) if (extra && !zones.includes(extra)) zones.unshift(extra);
  return zones.map((z) => [z, z.replace(/_/g, " ")]);
}

/** "+15155550101" -> { code: "+1", national: "5155550101" }; the longest code that matches wins. */
function splitPhone(phone) {
  const digits = String(phone || "").replace(/\D/g, "");
  if (!digits) return { code: "+1", national: "" };
  const codes = COUNTRY_CODES.map(([c]) => c).sort((a, b) => b.length - a.length);
  for (const code of codes) {
    const bare = code.slice(1);
    if (digits.startsWith(bare) && digits.length > bare.length) return { code, national: digits.slice(bare.length) };
  }
  return { code: "+1", national: digits };
}

function signInForm(el, ctx) {
  const status = statusLine();
  let mode = "in";
  const name = h("input", { id: "acct-new-name", class: "input", type: "text", autocomplete: "name", placeholder: "Your name" });
  const email = h("input", { id: "acct-email", class: "input", type: "email", required: true, autocomplete: "email", placeholder: "you@example.com" });
  const password = h("input", { id: "acct-password", class: "input", type: "password", required: true, minlength: "8", autocomplete: "current-password", placeholder: "At least 8 characters" });
  const button = h("button", { class: "btn btn-primary", type: "submit" }, "Sign in");
  const nameField = field("Name", name);
  const toggle = h("button", { class: "btn btn-link", type: "button" }, "Create an account");
  const title = h("h2", { class: "panel-title" }, "Existing account");
  const blurb = h("p", { class: "muted small", style: { margin: "4px 0 12px" } }, "Your email and password; the session stays on this device until you sign out.");

  function setMode(next) {
    mode = next;
    nameField.hidden = mode === "in";
    title.textContent = mode === "in" ? "Existing account" : "New account";
    button.textContent = mode === "in" ? "Sign in" : "Create account";
    toggle.textContent = mode === "in" ? "Create an account" : "Sign in to an existing account";
    password.autocomplete = mode === "in" ? "current-password" : "new-password";
    blurb.textContent =
      mode === "in"
        ? "Your email and password; the session stays on this device until you sign out."
        : "Pick a password of at least 8 characters. Overfield keeps only a salted hash of it, on its own server.";
    status.set("");
  }
  toggle.addEventListener("click", () => setMode(mode === "in" ? "up" : "in"));
  setMode("in");

  const form = h(
    "form",
    {
      class: "card",
      onsubmit: async (event) => {
        event.preventDefault();
        const value = email.value.trim();
        if (!value || !password.value) return;
        button.disabled = true;
        status.set(mode === "in" ? "Signing you in." : "Creating your account.");
        try {
          if (mode === "in") await signIn(value, password.value);
          else await signUp(value, password.value, name.value);
          status.set("Signed in.", "ok");
        } catch (err) {
          status.set(err.message, "warn");
          button.disabled = false;
        }
      },
    },
    title,
    blurb,
    nameField,
    field("Email", email),
    field("Password", password),
    h("div", { class: "row row-wrap", style: { marginTop: "12px" } }, button, toggle),
    status.el,
  );
  el.replaceChildren(
    h("div", { class: "section" }, form),
    h(
      "div",
      { class: "section" },
      h("div", { class: "section-title" }, "What an account gives you"),
      h(
        "ul",
        { class: "small explainer-list" },
        h("li", {}, "Your farm's plan every morning by text, voice call or email, at the time and in the language you choose."),
        h("li", {}, "A record of what was sent to you, and a pause switch for when you do not want it."),
        h("li", {}, "A developer key for the API, after you accept the developer terms."),
        h("li", {}, "A delete button that removes everything about you."),
      ),
    ),
  );
}

/** The demo of the morning text: today's plan for a farm, sent to a phone now through Photon. */
function demoSection(ctx, profile, current) {
  const phone = h("input", { class: "input", type: "tel", value: profile?.phone || "", placeholder: "Phone, or the one typed above", "aria-label": "Phone to text", style: { maxWidth: "220px" } });
  const farm = h("input", { class: "input mono", type: "text", value: profile?.farm_id || "", placeholder: "Farm, or the matched field", "aria-label": "Farm id", style: { maxWidth: "200px" } });
  const status = statusLine();
  const button = h("button", { class: "btn btn-primary", type: "button" }, "Text me today's plan");
  button.addEventListener("click", async () => {
    button.disabled = true;
    status.set("Sending the plan.");
    try {
      const now = current ? current() : {};
      const r = await demoText(phone.value.trim() || now.phone || "", farm.value.trim() || now.farm || "");
      status.set(`Sent to ${r.to} for farm ${r.farm}. It reads: ${r.preview}`, "ok");
      document.dispatchEvent(new CustomEvent("overfield:delivered"));
    } catch (err) {
      status.set(`Not sent: ${err.message || err}`, "warn");
    } finally {
      button.disabled = false;
    }
  });
  return h("div", { class: "section" },
    h("div", { class: "section-title" }, "Demo: the morning text"),
    h("p", { class: "small" }, "Every morning the plan goes out by text at the time above. Send today's plan now to see it: the phone that should receive it, and the farm whose plan to send (your matched field by default)."),
    h("div", { class: "row row-wrap", style: { gap: "8px", margin: "8px 0" } }, phone, farm, button),
    status.el);
}

function deliveriesSection(profile) {
  const body = h("div", {}, loading(2));
  const section = h("div", { class: "section" }, h("div", { class: "section-title" }, "Your last deliveries"), body);
  myDeliveries()
    .catch((error) => {
      body.replaceChildren(errorNote(error, "Could not read your deliveries"));
      return null;
    })
    .then((data) => {
      if (data === null) return;
      if (!data?.length) {
        body.replaceChildren(h("div", { class: "empty" }, "Nothing has been sent to you yet. The first plan goes out the morning after your profile has a farm and a number or email."));
        return;
      }
      body.replaceChildren(
        h(
          "div",
          { class: "table-wrap" },
          h(
            "table",
            { class: "table" },
            h("thead", {}, h("tr", {}, h("th", {}, "Morning"), h("th", {}, "Farm"), h("th", {}, "Channel"), h("th", {}, "Status"), h("th", {}, "Detail"), h("th", {}, "Sent"))),
            h(
              "tbody",
              {},
              ...data.map((row) =>
                h(
                  "tr",
                  {},
                  h("td", {}, fmtDay(row.run_date), row.dry_run ? h("span", { class: "muted small" }, " (dry run)") : null),
                  h("td", { class: "mono" }, row.farm_id || ""),
                  h("td", {}, CHANNEL_LABEL[row.channel] || titleCase(row.channel)),
                  h("td", {}, chip(row.status)),
                  h("td", { class: "muted" }, row.detail || ""),
                  h("td", { class: "muted" }, fmtDateTime(row.sent_at)),
                ),
              ),
            ),
          ),
        ),
      );
    });
  return section;
}

function deleteSection(ctx) {
  const status = statusLine();
  const box = h("div", { class: "card card-danger" });
  const idle = () =>
    box.replaceChildren(
      h("p", { class: "small" }, "Delete my data removes your account, profile, developer keys, terms acceptances and delivery rows. It cannot be undone."),
      h("div", { class: "row row-wrap", style: { marginTop: "10px" } }, h("button", { class: "btn", type: "button", onclick: confirm }, "Delete my data")),
      status.el,
    );
  const confirm = () =>
    box.replaceChildren(
      h("p", { class: "small" }, h("strong", {}, "This cannot be undone. "), "Everything about you goes, including the number the morning plan is sent to."),
      h(
        "div",
        { class: "row row-wrap", style: { marginTop: "10px" } },
        h(
          "button",
          {
            class: "btn btn-danger",
            type: "button",
            onclick: async (event) => {
              event.currentTarget.disabled = true;
              status.set("Deleting your data.");
              try {
                await deleteMyAccount();
                await ctx.signOut();
                box.replaceChildren(
                  h("p", { class: "small" }, "Your data has been deleted. Thank you for trying Overfield."),
                  h("div", { class: "row", style: { marginTop: "10px" } }, h("button", { class: "btn", type: "button", onclick: ctx.close }, "Return to the map")),
                );
              } catch (err) {
                status.set(err.message, "warn");
                event.currentTarget.disabled = false;
              }
            },
          },
          "Yes, delete everything",
        ),
        h("button", { class: "btn btn-ghost", type: "button", onclick: idle }, "Keep my account"),
      ),
      status.el,
    );
  idle();
  return h("div", { class: "section" }, h("div", { class: "section-title" }, "Delete my data"), box);
}

function profileForm(el, ctx) {
  const { user, profile } = ctx.auth;
  const phone = splitPhone(profile.phone);
  const form = {
    name: profile.name || "",
    address: profile.address || "",
    farm_id: profile.farm_id || null,
    lon: profile.lon ?? null,
    lat: profile.lat ?? null,
    code: phone.code,
    national: phone.national,
    language: profile.language || "en",
    send_time: String(profile.send_time || "06:30").slice(0, 5),
    timezone: profile.timezone || "America/Chicago",
    channels: new Set(Array.isArray(profile.channels) ? profile.channels : ["sms", "voice"]),
    consent: Boolean(profile.sms_consent_at),
    paused: Boolean(profile.paused),
  };
  const status = statusLine();
  const findStatus = statusLine();

  const name = h("input", { id: "acct-name", class: "input", type: "text", autocomplete: "name", value: form.name, oninput: (e) => (form.name = e.target.value) });
  const address = h("input", {
    id: "acct-address",
    class: "input",
    type: "text",
    autocomplete: "street-address",
    placeholder: "Road, town, state, or lat, lon",
    value: form.address,
    oninput: (e) => (form.address = e.target.value),
  });
  const matched = h("div", { class: "matched small" });
  function showMatch(extra) {
    matched.replaceChildren(
      form.farm_id
        ? h("span", {}, "Your field: ", h("strong", { class: "mono" }, form.farm_id), extra ? h("span", { class: "muted" }, ` ${extra}`) : null)
        : h("span", { class: "muted" }, extra || "No field matched yet. Type your farm address and press Find my field."),
    );
  }
  showMatch();

  const findButton = h("button", { class: "btn", type: "button" }, "Find my field");
  findButton.addEventListener("click", async () => {
    const text = address.value.trim();
    if (!text) {
      findStatus.set("Type an address or lat, lon first.", "warn");
      return;
    }
    findButton.disabled = true;
    findStatus.set("Looking up the address.");
    try {
      let point = parseLatLon(text);
      let where = null;
      if (!point) {
        const hit = await geocode(text);
        if (!hit) {
          findStatus.set("Could not find that address. Add the town and state, or type lat, lon.", "warn");
          return;
        }
        point = [hit.lon, hit.lat];
        where = hit.display_name;
      }
      const data = await ctx.dataPromise.catch(() => null);
      if (!data?.fields) {
        findStatus.set("The field map has not loaded, so the address cannot be matched yet. Try again in a moment.", "warn");
        return;
      }
      form.lon = Math.round(point[0] * 1e5) / 1e5;
      form.lat = Math.round(point[1] * 1e5) / 1e5;
      const match = matchField(point[0], point[1], data.fields);
      if (!match) {
        form.farm_id = null;
        showMatch();
        findStatus.set(`Overfield does not cover that location yet (${form.lat}, ${form.lon}). No field is within ${MAX_MATCH_KM} km.`, "warn");
        return;
      }
      form.farm_id = match.field_id;
      showMatch(match.inside ? "(the address is inside this field)" : `(nearest field, ${match.distance_km} km away)`);
      findStatus.set(where ? `Found: ${where}. Save the profile to keep this field.` : "Matched. Save the profile to keep this field.", "ok");
    } catch (err) {
      findStatus.set(err.message, "warn");
    } finally {
      findButton.disabled = false;
    }
  });

  const code = select("acct-code", COUNTRY_CODES, form.code, { class: "select select-code", "aria-label": "Country code", onchange: (e) => (form.code = e.target.value) });
  const national = h("input", {
    id: "acct-phone",
    class: "input",
    type: "tel",
    inputmode: "tel",
    autocomplete: "tel-national",
    placeholder: "Number without the country code",
    value: form.national,
    oninput: (e) => (form.national = e.target.value),
  });
  const language = select("acct-language", LANGUAGES, form.language, { onchange: (e) => (form.language = e.target.value) });
  const time = h("input", { id: "acct-time", class: "input", type: "time", value: form.send_time, onchange: (e) => (form.send_time = e.target.value) });
  const zone = select("acct-zone", zoneOptions(form.timezone), form.timezone, { onchange: (e) => (form.timezone = e.target.value) });

  const channels = h(
    "div",
    { class: "check-list" },
    ...CHANNELS.map(([key, label]) =>
      h(
        "label",
        { class: "check-row" },
        h("input", {
          type: "checkbox",
          checked: form.channels.has(key) ? true : null,
          onchange: (e) => (e.target.checked ? form.channels.add(key) : form.channels.delete(key)),
        }),
        h("span", {}, label),
      ),
    ),
  );
  const consent = h(
    "label",
    { class: "check-row consent" },
    h("input", { type: "checkbox", checked: form.consent ? true : null, onchange: (e) => (form.consent = e.target.checked) }),
    h("span", {}, CONSENT_LABEL),
  );
  const paused = h(
    "label",
    { class: "switch" },
    h("input", { type: "checkbox", role: "switch", checked: form.paused ? true : null, onchange: (e) => (form.paused = e.target.checked) }),
    h("span", { class: "switch-track", "aria-hidden": "true" }),
    h("span", {}, "Paused"),
  );

  const saveButton = h("button", { class: "btn btn-primary", type: "submit" }, "Save profile");
  const formEl = h(
    "form",
    {
      class: "card",
      onsubmit: async (event) => {
        event.preventDefault();
        const digits = form.national.replace(/\D/g, "");
        const channelList = CHANNELS.map(([k]) => k).filter((k) => form.channels.has(k));
        const patch = {
          name: form.name.trim(),
          address: form.address.trim() || null,
          farm_id: form.farm_id || null,
          lon: form.lon,
          lat: form.lat,
          phone: digits ? `${form.code}${digits}` : null,
          language: form.language,
          send_time: form.send_time || "06:30",
          timezone: form.timezone || "America/Chicago",
          channels: channelList,
          sms_consent_at: form.consent ? profile.sms_consent_at || new Date().toISOString() : null,
          paused: form.paused,
        };
        saveButton.disabled = true;
        status.set("Saving your profile.");
        try {
          const row = await updateMyProfile(user.id, patch);
          ctx.setProfile(row);
          const ready = row.farm_id && (row.phone || row.email) && !row.paused;
          status.set(ready ? "Saved. You are in the morning roster." : "Saved. The morning plan starts once you have a field, a number or email, and are not paused.", "ok");
        } catch (err) {
          status.set(err.message, "warn");
        } finally {
          saveButton.disabled = false;
        }
      },
    },
    h("div", { class: "form-grid" }, field("Name", name), field("Roster id", h("input", { class: "input mono", type: "text", value: profile.roster_id || "", readonly: true, "aria-readonly": "true" }))),
    h(
      "div",
      { class: "field", style: { marginTop: "12px" } },
      h("label", { for: "acct-address" }, "Farm address"),
      h("div", { class: "row find-row" }, address, findButton),
      matched,
      findStatus.el,
    ),
    h(
      "div",
      { class: "field", style: { marginTop: "12px" } },
      h("label", { for: "acct-phone" }, "Phone"),
      h("div", { class: "row phone-row" }, code, national),
      h("span", { class: "hint" }, "Stored with the country code, digits only."),
    ),
    h("div", { class: "form-grid", style: { marginTop: "12px" } }, field("Language", language), field("Morning time", time, "Local time; the default is 06:30."), field("Timezone", zone)),
    h("div", { class: "field", style: { marginTop: "12px" } }, h("label", {}, "Channels"), channels),
    h("div", { class: "field", style: { marginTop: "12px" } }, h("label", {}, "Consent"), consent, h("span", { class: "hint" }, "Nothing is sent to a number without this box ticked.")),
    h("div", { class: "field", style: { marginTop: "12px" } }, h("label", {}, "Pause"), paused, h("span", { class: "hint" }, "While paused nothing is sent; your profile stays.")),
    h("div", { class: "row row-wrap", style: { marginTop: "16px" } }, saveButton, h("span", { class: "muted small" }, `Signed in as ${user.email || user.phone || ""}`)),
    status.el,
  );

  el.replaceChildren(h("div", { class: "section" }, formEl), demoSection(ctx, profile, () => ({ phone: form.national.replace(/\D/g, "") ? `${form.code}${form.national.replace(/\D/g, "")}` : "", farm: form.farm_id || "" })), deliveriesSection(profile), deleteSection(ctx));
}

export function renderAccount(el, ctx) {
  if (!configured) {
    el.replaceChildren(notice(NOT_CONFIGURED));
    return {};
  }
  const { auth } = ctx;
  if (!auth.ready) {
    el.replaceChildren(loading(3));
    return {};
  }
  if (!auth.user) {
    signInForm(el, ctx);
    return {};
  }
  if (auth.profileError) {
    el.replaceChildren(
      errorNote(auth.profileError, "Could not load your profile"),
      h("div", { class: "row row-wrap", style: { marginTop: "12px" } }, h("button", { class: "btn", type: "button", onclick: () => ctx.refreshProfile() }, "Reload the profile"), h("button", { class: "btn btn-ghost", type: "button", onclick: ctx.signOut }, "Sign out")),
    );
    return {};
  }
  if (!auth.profile) {
    el.replaceChildren(loading(4));
    return {};
  }
  profileForm(el, ctx);
  return {};
}

export { card };
