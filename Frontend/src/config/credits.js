// src/config/credits.js
//
// Single source of truth for the "Contributors & Roadmap" modal
// (Settings page → small link at the very bottom).
//
// ── CONTRIBUTORS ──────────────────────────────────────────────────────
//   name  — display name
//   role  — one string per language ("en" and "sw")
//   urls  — optional list of links; plain strings are enough — labels
//           (GitHub, LinkedIn, Fiverr, Email, …) are generated from the
//           URL. For a custom label use an object:
//           { label: "Portfolio", url: "https://…" }
//
// ── SHIPPED (what's new since the beta release) ──────────────────────
//   date  — week of shipping (any day inside that week works; the modal
//           renders the label "Week of <Monday>" automatically)
//   en/sw — one short, user-facing line per language — benefits, not
//           technical details
//
//   Maintenance: add new items at the TOP (newest first). When the list
//   grows past ~6 entries, drop the oldest from the bottom — the modal
//   is a highlight reel, not a changelog.
//
// ── ROADMAP (what's next) ────────────────────────────────────────────
//   en/sw — one short line per language, no dates, no promises.
//   When an item ships, move it to the top of `shipped` with a date
//   and reword it as a past-tense benefit.
//
// Example shipped entry:
//   {
//     date: "2026-08-28",
//     en: "Installable app with push notifications",
//     sw: "Programu inayosakinishwa na arifa za push",
//   },

export const CREDITS = {
  // Link to the public repository shown at the bottom of the modal.
  // Set to null to hide the open-source note entirely.
  openSourceUrl: "https://github.com/NinoMandelaB/UwenzAi-Public",

  contributors: [
    {
      name: "Nino Bachmann",
      role: {
        en: "Co-Founder & Lead Architect",
        sw: "Mwanzilishi Mwenza na Msanifu Mkuu",
      },
      // urls: ["https://github.com/NinoMandelaB"], // add links here if wanted
    },
    {
      name: "Bhoke Werema",
      role: {
        en: "Co-Founder & Localization Lead",
        sw: "Mwanzilishi Mwenza na Mkuu wa Lugha",
      },
    },
    {
      name: "Erick Kamanda",
      role: {
        en: "Project Manager · Alpha & Beta",
        sw: "Meneja wa Mradi · Alfa na Beta",
      },
      urls: [
        "https://www.linkedin.com/in/erick-kamanda/",
        "https://pro.fiverr.com/engineer_moses",
      ],
    },
    {
      name: "Isaac Misiani",
      role: {
        en: "Software Developer · Alpha & Beta",
        sw: "Msanidi Programu · Alfa na Beta",
      },
      urls: ["https://github.com/atuta", "mailto:isaacatuta@icloud.com"],
    },
    {
      name: "Maureen Nzioki",
      role: {
        en: "Character Writer · Alpha & Beta",
        sw: "Mwandishi wa Wahusika · Alfa na Beta",
      },
      urls: [
        "https://www.linkedin.com/in/maureen-nzioki-a7524986",
        "https://www.fiverr.com/reenmafab",
      ],
    },
    {
      name: "Ashfaq",
      role: {
        en: "AI Character Artist · Alpha & Beta",
        sw: "Msanii wa Picha za Wahusika · Alfa na Beta",
      },
      urls: ["https://pro.fiverr.com/ashfaq_de"],
    },
    {
      name: "Roby Werema",
      role: {
        en: "Marketing",
        sw: "Masoko",
      },
    },
    // Add more contributors below this line.
  ],

  // What's new since the beta release — newest first, ~6 entries max.
  shipped: [
    {
      date: "2026-09-15",
      en: "UwenzAi-Dev — your personal UwenzAi API key to access AI models for your personal use",
      sw: "UwenzAi-Dev — ufunguo wako wa API wa UwenzAi wa kufikia mifano ya AI kwa matumizi yako binafsi",
    },
    {
      date: "2026-09-11",
      en: "Reminders — ask your companion to remind you, and they will",
      sw: "Vikumbusho — mwombe mwandani wako akukumbushe, naye atafanya",
    },
    {
      date: "2026-09-11",
      en: "Memory editor — see and correct what your companion remembers",
      sw: "Kihariri cha kumbukumbu — angalia na sahihisha anavyokumbuka mwandani wako",
    },
    {
      date: "2026-09-11",
      en: "Stronger security, plus new settings to show or hide reminders and web search",
      sw: "Usalama imara zaidi, na mipangilio mpya ya kuonyesha au kuficha vikumbusho na utafutaji wavuti",
    },
    {
      date: "2026-08-28",
      en: "Installable app with push notifications",
      sw: "Programu inayosakinishwa na arifa za push",
    },
    {
      date: "2026-08-26",
      en: "Sharper, more consistent photos",
      sw: "Picha bora na zinazofanana zaidi",
    },
    {
      date: "2026-08-07",
      en: "Web search, right inside the chat",
      sw: "Utafutaji wavuti ndani ya mazungumzo",
    },
    {
      date: "2026-08-07",
      en: "Deep Thinking mode — slower, more thoughtful replies",
      sw: "Hali ya Ufikiri Mpana — majibu yenye mafikiri zaidi",
    },
    {
      date: "2026-08-06",
      en: "A smarter memory of your relationship",
      sw: "Kumbukumbu bora zaidi ya mahusiano yako",
    },
    {
      date: "2026-08-03",
      en: "Your companion can now offer you photos",
      sw: "Mwandani wako sasa anaweza kukupa picha",
    },
    // Add newly shipped items above this line (newest first).
  ],

  // What's coming next — short, benefit-first, no dates.
  roadmap: [
    {
      en: "Voice chat — talk to your AI companion and hear them reply",
      sw: "Mazungumzo ya sauti — sema na mwandani wako wa AI na usikie anavyojibu",
    },
    {
      en: "Connect UwenzAi to your own AI agent",
      sw: "Unganisha UwenzAi na kipasha chako cha AI",
    },
    {
      en: "…and a few surprises",
      sw: "…na mambo mengine ya kufurahisha",
    },
  ],
};
