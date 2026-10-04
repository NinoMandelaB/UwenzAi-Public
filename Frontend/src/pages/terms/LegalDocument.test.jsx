import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { LanguageProvider } from "../../context/LanguageContext";
import {
  LEGAL_LAST_UPDATED,
  LEGAL_POLICY_VERSION,
  legalDocuments,
} from "../../i18n/legal";
import TermsOfService from "./TermsOfService";
import PrivacyPolicy from "./PrivacyPolicy";
import SubprocessorDisclosure from "./SubprocessorDisclosure";

function renderLegal(ui) {
  return render(
    <MemoryRouter>
      <LanguageProvider>{ui}</LanguageProvider>
    </MemoryRouter>,
  );
}

// Text is stored across separate blocks, so a phrase search has to run against
// the flattened document rather than a single DOM node.
function flatten(doc) {
  const fromBlock = (block) => {
    if (block.type === "ul") return block.items.join(" ");
    if (block.type === "callout")
      return [block.title, ...block.blocks.map(fromBlock)].join(" ");
    if (block.type === "table")
      return [block.head.join(" "), ...block.rows.map((r) => r.join(" "))].join(
        " ",
      );
    return block.text;
  };

  return [
    doc.title,
    doc.subtitle || "",
    ...doc.intro.map(fromBlock),
    ...doc.sections.flatMap((s) => [s.heading, ...s.blocks.map(fromBlock)]),
    doc.footer,
  ].join("\n");
}

const allDocs = Object.entries(legalDocuments).flatMap(([name, byLanguage]) =>
  Object.entries(byLanguage).map(([language, doc]) => ({
    name,
    language,
    doc,
  })),
);

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllEnvs();
});

describe("legal pages render", () => {
  it("renders the Terms of Use with its last-updated date", () => {
    renderLegal(<TermsOfService />);
    expect(
      screen.getByRole("heading", { level: 1, name: "UwenzAi Terms of Use" }),
    ).toBeTruthy();
    expect(screen.getByText(new RegExp(LEGAL_LAST_UPDATED))).toBeTruthy();
  });

  it("renders the Privacy Policy with its last-updated date", () => {
    renderLegal(<PrivacyPolicy />);
    expect(
      screen.getByRole("heading", { level: 1, name: "UwenzAi Privacy Policy" }),
    ).toBeTruthy();
    expect(screen.getByText(new RegExp(LEGAL_LAST_UPDATED))).toBeTruthy();
  });

  it("renders the Service Provider Disclosure as a table of providers", () => {
    renderLegal(<SubprocessorDisclosure />);
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "UwenzAi Service Provider Disclosure",
      }),
    ).toBeTruthy();
    expect(screen.getAllByRole("table").length).toBeGreaterThan(0);
    for (const provider of [
      "Railway",
      "Cortecs",
      "Mistral AI",
      "fal.ai",
      "Stability AI",
      "Paystack",
      "Google",
      "Sentry",
    ]) {
      expect(screen.getAllByText(provider).length).toBeGreaterThan(0);
    }
  });

  it("no longer shows the development/test server warning", () => {
    renderLegal(<PrivacyPolicy />);
    expect(screen.queryByText(/DEVELOPMENT \/ TEST SERVER/i)).toBeNull();
    expect(screen.queryByText(/not the production service/i)).toBeNull();
    expect(
      screen.queryByText(/not protected.*production standards/i),
    ).toBeNull();
    expect(
      screen.queryByText(/Swiss FADP.*do not apply|GDPR.*do not apply/i),
    ).toBeNull();
    expect(screen.queryByText(/wiped, or taken offline/i)).toBeNull();
  });

  it("links the Privacy Policy to the provider disclosure page", () => {
    renderLegal(<PrivacyPolicy />);
    expect(
      screen.getAllByText("Service Provider Disclosure").length,
    ).toBeGreaterThan(0);
  });
});

describe("language switching", () => {
  it("renders the Kiswahili Terms when the stored language is sw", () => {
    localStorage.setItem("eaaic-language", "sw");
    renderLegal(<TermsOfService />);
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Masharti ya Matumizi ya UwenzAi",
      }),
    ).toBeTruthy();
    expect(
      screen.getByText(`Ilisasishwa mwisho: ${LEGAL_LAST_UPDATED}`),
    ).toBeTruthy();
  });

  it("swaps the Privacy Policy language when the switcher is used", async () => {
    renderLegal(<PrivacyPolicy />);
    expect(screen.getByText("UwenzAi Privacy Policy")).toBeTruthy();

    await act(async () => {
      screen.getByRole("button", { name: "SW" }).click();
    });

    expect(screen.getByText("Sera ya Faragha ya UwenzAi")).toBeTruthy();
    expect(screen.queryByText("UwenzAi Privacy Policy")).toBeNull();

    await act(async () => {
      screen.getByRole("button", { name: "EN" }).click();
    });

    expect(screen.getByText("UwenzAi Privacy Policy")).toBeTruthy();
  });

  it("offers the switcher on every legal page", () => {
    for (const Page of [
      TermsOfService,
      PrivacyPolicy,
      SubprocessorDisclosure,
    ]) {
      const { unmount } = renderLegal(<Page />);
      expect(screen.getByRole("button", { name: "EN" })).toBeTruthy();
      expect(screen.getByRole("button", { name: "SW" })).toBeTruthy();
      unmount();
    }
  });
});

describe("English and Kiswahili stay structurally parallel", () => {
  for (const [name, byLanguage] of Object.entries(legalDocuments)) {
    it(`${name} has matching sections in both languages`, () => {
      const { en, sw } = byLanguage;
      expect(sw).toBeTruthy();
      expect(sw.sections.map((s) => s.id)).toEqual(
        en.sections.map((s) => s.id),
      );
      expect(sw.intro.length).toBe(en.intro.length);
      expect(sw.intro.map((b) => b.type)).toEqual(en.intro.map((b) => b.type));
      en.sections.forEach((section, index) => {
        const other = sw.sections[index];
        expect(other.blocks.map((b) => b.type)).toEqual(
          section.blocks.map((b) => b.type),
        );
      });
    });

    it(`${name} Kiswahili is translated, not copied from English`, () => {
      const { en, sw } = byLanguage;
      expect(sw.title).not.toBe(en.title);
      en.sections.forEach((section, index) => {
        expect(sw.sections[index].heading).not.toBe(section.heading);
      });
    });
  }
});

describe("required disclosures are present", () => {
  const requiredInPrivacy = [
    /not end-to-end encryption/i,
    /Railway/,
    /fal\.ai/,
    /reference/i,
    /signed/i,
    /guest/i,
    /Kenyan Data Protection Act/,
    /Office of the Data Protection Commissioner/,
    /seventy-two hours/i,
    /Paystack/,
    /export/i,
    /delete your account/i,
    /Bring Your Own Key/,
    /cross|across borders|international/i,
  ];

  it.each(requiredInPrivacy)("Privacy Policy discloses %s", (pattern) => {
    expect(flatten(legalDocuments.privacy.en)).toMatch(pattern);
  });

  const requiredInTerms = [
    /licence to host, store, copy/i,
    /Your Inputs/,
    /sublicensable/i,
    /minors/i,
    /animals/i,
    /deceased/i,
    /sexually explicit/i,
    /guest/i,
    /except where required by applicable law|mandatory applicable law/i,
  ];

  it.each(requiredInTerms)("Terms of Use states %s", (pattern) => {
    expect(flatten(legalDocuments.terms.en)).toMatch(pattern);
  });

  it("keeps the absolute image prohibitions in both languages", () => {
    expect(flatten(legalDocuments.terms.en)).toMatch(/Absolute prohibitions/i);
    expect(flatten(legalDocuments.terms.sw)).toMatch(/Marufuku ya kabisa/i);
  });

  it("describes account deletion without cascading every character", () => {
    const en = flatten(legalDocuments.privacy.en);
    const sw = flatten(legalDocuments.privacy.sw);
    expect(en).not.toMatch(/cascading deletion/i);
    expect(sw).not.toMatch(/ufutaji wa mfululizo/i);
    expect(en).toMatch(/Public characters you created remain on the Service/i);
    expect(en).toMatch(/This is not a refund of purchases/i);
    expect(sw).toMatch(/Wahusika wa umma uliowaunda wanabaki kwenye Huduma/i);
    expect(sw).toMatch(/Hii si marejesho ya malipo/i);
  });
});

function sectionText(doc, id) {
  const section = doc.sections.find((s) => s.id === id);
  expect(section).toBeTruthy();
  return [section.heading, ...section.blocks.map((b) => b.text)].join("\n");
}

describe("legal publication versions", () => {
  it("versions registration and the displayed policy set together", () => {
    // 2026-09-14: UwenzAi-Dev developer publication (initial consent version).
    // 2026-09-20: subprocessor disclosure updated (AWS Bedrock removed from
    // the UwenzAi-Dev gateway; section 10 uses Fikra as the non-EU example).
    // 2026-09-23: TensorX (Ireland, EU) added to the gateway provider set.
    // 2026-10-05: photo-message disclosures — Terms 7.3-7.6 (upload rules and
    // screening), Privacy sections 1/6/8/9/17 (photo screening before storage,
    // application-level encryption of chat photos, photos to the active chat
    // provider), subprocessor purposes updated. New processing purposes are
    // disclosed, so the consent version moves to 2026-10-05; existing
    // acceptance records keep the version they have.
    // 2026-10-06: cross-session safety ledger — Terms 6.7 and 14.4 (automated
    // safety screening, warnings, account safety lock and admin review),
    // Privacy 4/10/14/15 (safety records with category/severity metadata
    // only, never message text; summary-only admin visibility; retention
    // and the child-safety retention basis). A new category of record is
    // disclosed, so the consent version moves again.
    // 2026-10-07: advisory model-refusal records (Privacy §4) — counted,
    // never punitive, admin-resettable, deleted with the account.
    expect(LEGAL_POLICY_VERSION).toBe("2026-10-07");
    expect(LEGAL_LAST_UPDATED).toBe("07-OCT-2026");
  });

  for (const language of ["en", "sw"]) {
    const privacy = legalDocuments.privacy[language];
    const terms = legalDocuments.terms[language];
    const providers = legalDocuments.subprocessors[language];
    const en = language === "en";
    it(`${language} distinguishes the developer offering without promising isolation`, () => {
      const intro = privacy.intro.map((b) => b.text).join("\n");
      expect(intro).toMatch(en ? /functionally separate/ : /utendaji tofauti/);
      expect(intro).toMatch(
        en
          ? /same UwenzAi account and Sparks balance/
          : /akaunti ileile ya UwenzAi na salio lilelile la Sparks/,
      );
      expect(intro).toMatch(
        en
          ? /does not automatically include companion/
          : /halijumuishi kiotomatiki mazungumzo na wenzi/,
      );
      expect(intro).toMatch(
        en
          ? /name does not mean.*internal development\/test/
          : /Jina hilo halimaanishi.*ndani ya maendeleo/,
      );
      const independence = terms.sections
        .find((s) => s.id === "11")
        .blocks.find((b) => b.text?.startsWith("11.6."));
      expect(independence.text).toMatch(
        en
          ? /does not create a separate account.*guarantee of technical isolation/
          : /haiundi akaunti tofauti.*hakikisho la utengano wa kiufundi/,
      );
      expect(independence.text).toMatch(
        en ? /suspension or deletion/ : /kusimamishwa au kufutwa/,
      );
      expect(independence.text).toMatch(/11\.4/);
    });
    it(`${language} retains the section 4 bullet structure while making every restriction subject to 11.4`, () => {
      const blocks = terms.sections.find((s) => s.id === "4").blocks;
      expect(blocks.map((b) => b.type)).toEqual(["p", "p", "ul", "p"]);
      expect(blocks[2].items).toHaveLength(2);
      for (const block of blocks.filter((b) => b.type === "p")) {
        expect(block.text).toMatch(/11\.4/);
      }
      const ip = sectionText(terms, "11");
      for (const n of ["11.2.", "11.3.", "11.4.", "11.5.", "11.6."])
        expect(ip).toContain(n);
      expect(ip).toMatch(
        en
          ? /use, reproduce, modify and distribute/
          : /kutumia, kunakili, kurekebisha na kusambaza/,
      );
      expect(ip).toMatch(
        en ? /configure, manage and test/ : /kusanidi, kusimamia na kujaribu/,
      );
      expect(ip).toMatch(
        en
          ? /does not override a third party’s restrictions/
          : /haibatilishi vikwazo vya mtu wa tatu/,
      );
    });
    it(`${language} adds optional history without expanding training permissions or waiving statutory rights`, () => {
      expect(sectionText(terms, "8")).toMatch(/8\.10\./);
      expect(sectionText(terms, "8")).toMatch(/8\.11\./);
      expect(sectionText(terms, "8")).toMatch(
        en
          ? /Nothing in this clause limits your statutory data-protection rights/
          : /Hakuna sehemu ya kifungu hiki inayopunguza haki zako za kisheria/,
      );
      expect(sectionText(privacy, "5")).toMatch(
        en
          ? /stored only when you enable/
          : /historia huhifadhiwa tu unapowasha/,
      );
      expect(sectionText(privacy, "5")).toMatch(
        en ? /performance of our contract/ : /kutekeleza mkataba wetu/,
      );
      expect(sectionText(privacy, "5")).toMatch(
        en
          ? /not to use private content to train/
          : /kutotumia maudhui ya faragha kufundisha/,
      );
    });
    it(`${language} distinguishes request data, metadata, selected providers and key storage`, () => {
      expect(sectionText(privacy, "4")).toMatch(
        en ? /verification hash/ : /alama ya hashi/,
      );
      expect(sectionText(privacy, "4")).toMatch(
        en ? /operation identifiers/ : /vitambulisho vya shughuli/,
      );
      expect(sectionText(privacy, "6")).toMatch(
        en
          ? /not sent to every listed provider/
          : /hayatumwi kwa kila mtoa huduma/,
      );
      expect(sectionText(privacy, "6")).toMatch(
        en
          ? /not a promise of zero retention/
          : /si ahadi kwamba mtoa huduma huyo hatabaki/,
      );
      expect(sectionText(privacy, "9")).toMatch(
        en
          ? /not end-to-end encrypted/
          : /si usimbaji fiche wa mwisho hadi mwisho/,
      );
      expect(sectionText(privacy, "9")).toMatch(
        en
          ? /not covered by this content-encryption layer/
          : /havifunikwi na safu hii/,
      );
      expect(sectionText(privacy, "9")).toMatch(
        en ? /rather than the full secret/ : /badala ya siri nzima/,
      );
    });
    it(`${language} states capacity limits, no age expiry and pair-scoped deletion`, () => {
      const retention = sectionText(privacy, "14");
      for (const limit of ["60", "8,192", "32,768", "20"])
        expect(retention).toContain(limit);
      expect(retention).toMatch(
        en
          ? /no automatic expiry after a fixed number of days/
          : /hakuna ufutaji wa kiotomatiki baada ya idadi maalum ya siku/,
      );
      expect(retention).toMatch(
        en
          ? /does not itself delete history you saved earlier/
          : /hakufuti chenyewe historia uliyohifadhi awali/,
      );
      expect(retention).toMatch(
        en
          ? /does not clear every other saved conversation/
          : /hakifuti mazungumzo mengine yote yaliyohifadhiwa/,
      );
      expect(retention).toMatch(
        en
          ? /Ordinary external API requests do not create playground history/
          : /Maombi ya kawaida ya API ya nje hayaundi historia/,
      );
      expect(sectionText(privacy, "15")).toMatch(
        en
          ? /revoking a key alone does not delete saved history/
          : /kubatilisha ufunguo pekee hakufuti historia/,
      );
    });
    it(`${language} excludes companion replies but includes retained playground replies and usage charges in exports`, () => {
      const rights = sectionText(privacy, "16");
      expect(rights).toMatch(
        en
          ? /does not include companion-chat AI replies/
          : /Haijumuishi majibu ya AI katika mazungumzo na wenzi wa AI/,
      );
      expect(rights).toMatch(
        en
          ? /Saved playground replies and gateway usage charges are included/
          : /Majibu yaliyohifadhiwa katika eneo la majaribio na gharama za matumizi ya lango zinajumuishwa/,
      );
      expect(rights).not.toMatch(
        en ? /does not include AI replies/ : /Haijumuishi majibu ya AI, data/,
      );
      expect(rights).toMatch(
        en ? /gateway consent records/ : /Rekodi zako za ridhaa ya lango/,
      );
      expect(rights).toMatch(
        en
          ? /not deleted history, unsaved messages/
          : /si historia iliyofutwa, ujumbe ambao haukuhifadhiwa/,
      );
      expect(rights).toMatch(
        en
          ? /unavailable rather than presenting it as empty/
          : /hayapatikani badala ya kuyaonyesha kana kwamba hayana ujumbe/,
      );
      expect(rights).toMatch(
        en ? /does not protect that copy/ : /haulindi nakala hiyo/,
      );
      expect(rights).toMatch(
        en
          ? /excludes API secrets.*verification hashes.*provider credentials.*server encryption keys/
          : /haijumuishi siri za API.*alama za hashi.*watoa huduma.*funguo za usimbaji fiche za seva/,
      );
    });
    it(`${language} lists all seven configurable gateway providers without a metadata-only storage promise`, () => {
      const gateway = sectionText(providers, "8");
      for (const name of [
        "Cortecs",
        "Mistral AI",
        "Fikra",
        "Inceptron",
        "AKI.IO",
        "OVHcloud",
        "TensorX",
      ])
        expect(gateway).toContain(name);
      expect(gateway).toMatch(
        en
          ? /only when configured and offered/
          : /tu ikiwa amesanidiwa na amewekwa/,
      );
      expect(gateway).toMatch(
        en
          ? /separately stores the retained conversation/
          : /huhifadhi kando mazungumzo yanayobakizwa/,
      );
      expect(gateway).not.toMatch(
        en
          ? /Only metadata|sent to Cortecs/
          : /Metadata tu|hutumwa kwa Cortecs/,
      );
      expect(gateway).toMatch(
        en ? /sections 9, 14 and 15/ : /vifungu vya 9, 14 na 15/,
      );
      expect(gateway).toMatch(en ? /section 20/ : /kifungu cha 20/);
      expect(sectionText(privacy, "20")).toMatch(
        en ? /changes/i : /mabadiliko/i,
      );
    });
    it(`${language} makes processing locations conditional without deleting transfer safeguards`, () => {
      const transfers = sectionText(privacy, "11");
      expect(transfers).toMatch(
        en
          ? /locations depend on the provider and the configured service/
          : /Maeneo ya kuchakata data hutegemea mtoa huduma na huduma iliyosanidiwa/,
      );
      expect(transfers).not.toMatch(
        en
          ? /these providers operate outside Kenya/
          : /watoa huduma hawa wanafanya kazi nje ya Kenya/,
      );
      expect(flatten(providers)).not.toMatch(
        en
          ? /All of these providers operate outside Kenya/
          : /Watoa huduma hawa wote wanafanya kazi nje ya Kenya/,
      );
      expect(transfers).toMatch(
        en
          ? /appropriate contractual safeguards/
          : /ulinzi unaofaa wa kimkataba/,
      );
    });
    it(`${language} adds no SLA while preserving statutory rights and billing remedies`, () => {
      const sla = sectionText(terms, "16");
      expect(sla).toContain("16.4.");
      expect(sla).toContain("16.5.");
      expect(sla).toMatch(
        en ? /separate written SLA/ : /SLA tofauti ya maandishi/,
      );
      expect(sla).toMatch(
        en
          ? /no SLA service credits are promised/
          : /hakuna salio la fidia ya huduma chini ya SLA linaloahidiwa/,
      );
      expect(sla).toMatch(
        en
          ? /does not remove any billing correction, refund or remedy required by applicable law/
          : /hakiondoi marekebisho ya malipo, urejeshaji wa fedha au suluhisho linalotakiwa na sheria/,
      );
    });
  }
});

// Section numbers are maintained by hand and the documents cite them in their own
// prose, so inserting a clause without renumbering would produce two sections
// with the same number and leave the citations pointing at the wrong clause.
describe("section numbering and cross-references", () => {
  for (const { name, language, doc } of allDocs) {
    it(`${name}.${language} numbers every heading to match its section id`, () => {
      expect(doc.sections.map((s) => s.id)).toEqual(
        doc.sections.map((_, index) => String(index + 1)),
      );
      for (const section of doc.sections) {
        expect(section.heading).toMatch(new RegExp(`^${section.id}\\. `));
      }
    });
  }

  // Only the Terms cite their own sections. The provider disclosure's "section 10"
  // and "section 19" point into the Terms, not at its own ten sections.
  for (const language of ["en", "sw"]) {
    const citation =
      language === "en" ? /section (\d+)/gi : /kifungu cha (\d+)/gi;

    it(`terms.${language} cites only sections that exist`, () => {
      const doc = legalDocuments.terms[language];
      const ids = doc.sections.map((s) => s.id);
      const cited = [...flatten(doc).matchAll(citation)].map((m) => m[1]);
      expect(cited.length).toBeGreaterThan(0);
      for (const reference of cited) {
        expect(ids).toContain(reference);
      }
    });
  }

  // 2026-09-20: AWS Bedrock was removed from the gateway entirely; the
  // disclosure must not mention it anywhere, and non-EU AI providers stay
  // scoped to the developer gateway only.
  it("removes AWS Bedrock from the disclosure and keeps non-EU AI out of the companion app", () => {
    for (const language of ["en", "sw"]) {
      // AWS/Amazon/Bedrock are gone from every document...
      for (const doc of ["privacy", "terms", "subprocessors"]) {
        const whole = legalDocuments[doc][language].sections
          .map((s) =>
            [s.heading, ...(s.blocks || []).map((b) => b.text)].join("\n"),
          )
          .join("\n");
        expect(whole).not.toMatch(/\bAWS\b|Amazon|Bedrock/i);
      }
      const scope = sectionText(legalDocuments.subprocessors[language], "10");
      // ...and Fikra (Kenya) is the non-EU example used only by the gateway.
      expect(scope).toMatch(
        language === "en"
          ? /Fikra \(Kenya\) is used exclusively for the UwenzAi-Dev developer gateway/
          : /Fikra \(Kenya\), hutumiwa kwa lango la wasanidi la UwenzAi-Dev pekee/,
      );
      // EU processing of companion AI content stays stated, with the BYOK
      // exception kept so the claim never overreaches the implementation.
      expect(scope).toMatch(
        language === "en"
          ? /Cortecs in Austria and Mistral AI in France/
          : /Cortecs nchini Austria na Mistral AI nchini Ufaransa/,
      );
      expect(scope).toMatch(
        language === "en"
          ? /processed only by EU-based AI providers/
          : /walioko Ulaya pekee/,
      );
    }
  });

  it("keeps the disclosure's cross-document citations pointing at the right Terms sections", () => {
    expect(sectionText(legalDocuments.terms.en, "10")).toMatch(/BYOK/);
    expect(sectionText(legalDocuments.terms.en, "19")).toMatch(
      /Changes to the Service and Terms/i,
    );
  });
  it.each(["en", "sw"])(
    "keeps every %s Terms clause cross-reference resolvable",
    (language) => {
      const doc = legalDocuments.terms[language];
      const clauses = doc.sections.flatMap((s) =>
        s.blocks
          .filter((b) => b.type === "p")
          .map((b) => b.text.match(/^(\d+\.\d+)\./)?.[1])
          .filter(Boolean),
      );
      const refs = [
        ...flatten(doc).matchAll(
          language === "en" ? /section (\d+\.\d+)/g : /kifungu cha (\d+\.\d+)/g,
        ),
      ].map((m) => m[1]);
      expect(refs).toContain("11.4");
      expect(refs).toContain("11.5");
      for (const ref of refs) expect(clauses).toContain(ref);
    },
  );
});

// Owner-approved boilerplate. Two things carry the legal weight and are easy to
// erode in a later edit: the precedence clause must keep its "only to the extent
// permitted by applicable law" qualifier in the *same* provision as the
// English-controls statement, and neither clause may override mandatory rights.
describe("severability and language-precedence clauses", () => {
  const expected = {
    en: {
      severabilityHeading: /^22\. Severability/,
      languageHeading: /^23\. Language of these Terms/,
      severability: [
        /limited or modified only to the minimum extent necessary/i,
        /does not permit that limitation or modification, the provision shall be severed/i,
        /remaining provisions of these Terms, which continue in full force and effect/i,
        /mandatory statutory right that applicable law does not permit to be excluded or varied/i,
      ],
      language: [
        /published in English and in Kiswahili/i,
        /intended to have the same meaning/i,
        /mandatory consumer, data protection, or privacy right/i,
        /to be informed or to receive documents in a particular language/i,
      ],
      controls: /the English version controls/i,
      qualifier: /only to the extent permitted by applicable law/i,
    },
    sw: {
      severabilityHeading: /^22\. Kutenganisha vipengele/,
      languageHeading: /^23\. Lugha ya Masharti haya/,
      severability: [
        /kwa kiwango cha chini kinachohitajika pekee/i,
        /hairuhusu upunguzaji au marekebisho hayo, kipengele hicho kitatenganishwa/i,
        /vipengele vilivyobaki vya Masharti haya, ambavyo vitaendelea kuwa na nguvu kamili/i,
        /haki yoyote ya lazima ya kisheria/i,
      ],
      language: [
        /yanachapishwa kwa Kiingereza na kwa Kiswahili/i,
        /yanakusudiwa kuwa na maana sawa/i,
        /haki yoyote ya lazima ya mteja, ya ulinzi wa data, au ya faragha/i,
        /kupewa taarifa au kupokea nyaraka kwa lugha fulani/i,
      ],
      controls: /toleo la Kiingereza litatawala/i,
      qualifier: /kwa kiwango kinachoruhusiwa na sheria inayotumika pekee/i,
    },
  };

  for (const language of ["en", "sw"]) {
    const phrases = expected[language];
    const terms = () => legalDocuments.terms[language];

    it(`terms.${language} carries a severability clause with all four elements`, () => {
      const text = sectionText(terms(), "22");
      expect(text).toMatch(phrases.severabilityHeading);
      for (const pattern of phrases.severability) {
        expect(text).toMatch(pattern);
      }
    });

    it(`terms.${language} carries a language clause with all four elements`, () => {
      const text = sectionText(terms(), "23");
      expect(text).toMatch(phrases.languageHeading);
      for (const pattern of phrases.language) {
        expect(text).toMatch(pattern);
      }
    });

    // The qualifier is worthless if a later edit moves it into its own sentence.
    it(`terms.${language} qualifies English precedence in the same provision`, () => {
      const provisions = terms()
        .sections.find((s) => s.id === "23")
        .blocks.map((b) => b.text);
      const controlling = provisions.filter((p) => phrases.controls.test(p));
      expect(controlling.length).toBe(1);
      expect(controlling[0]).toMatch(phrases.qualifier);
    });

    it(`terms.${language} keeps section 17.4 as the liability-specific rule`, () => {
      expect(sectionText(terms(), "17")).toMatch(/17\.4\./);
      // The general clause must defer to it rather than silently contradict it.
      expect(sectionText(terms(), "22")).toMatch(/17\.4/);
    });
  }

  // The owner ruled out precedence framed as whichever version suits UwenzAi.
  const subjective = [
    /whichever (?:version|language|text)/i,
    /more favou?rable/i,
    /best protects/i,
    /protects UwenzAi/i,
    /in (?:our|UwenzAi's) favou?r/i,
    /inayolinda UwenzAi/i,
    /inayofaa zaidi/i,
  ];

  for (const language of ["en", "sw"]) {
    it.each(subjective)(
      `terms.${language} states precedence without subjective wording %s`,
      (pattern) => {
        expect(flatten(legalDocuments.terms[language])).not.toMatch(pattern);
      },
    );
  }

  it("does not duplicate the severability clause", () => {
    for (const language of ["en", "sw"]) {
      const headings = legalDocuments.terms[language].sections.map(
        (s) => s.heading,
      );
      const severability = headings.filter((h) =>
        /Severability|Kutenganisha/i.test(h),
      );
      expect(severability.length).toBe(1);
    }
  });
});

// These documents used to name Mistral AI as the provider that creates memory
// embeddings. The code has not worked that way for some time: chat replies go
// through CortecsClient and embeddings through MemoryService._get_embedding, and
// both try Cortecs first and only fall back to Mistral. Mistral is still called
// directly for background text tasks, so it cannot be described as a pure
// fallback either.
describe("AI provider roles match the implementation", () => {
  const byLanguage = {
    en: {
      primary: /primary (?:AI )?provider/i,
      fallback: /fallback (?:provider )?for/i,
      embedding: /embedding/i,
      direct:
        /called directly|directly for background|send text to Mistral AI directly/i,
    },
    sw: {
      primary: /mtoa huduma (?:wetu )?mkuu/i,
      fallback: /mtoa huduma wa akiba/i,
      embedding: /uwakilishi wa kihesabu/i,
      direct: /moja kwa moja/i,
    },
  };

  const providerDocs = ["privacy", "subprocessors"];

  function sentences(doc) {
    return flatten(doc).split(/(?<=[.;])\s+/);
  }

  for (const name of providerDocs) {
    for (const language of ["en", "sw"]) {
      const phrases = byLanguage[language];

      it(`${name}.${language} names Cortecs primary for chat and embeddings`, () => {
        const doc = legalDocuments[name][language];
        const primary = sentences(doc).filter((s) => phrases.primary.test(s));
        expect(primary.length).toBeGreaterThan(0);
        // The primary claim has to cover embeddings, not chat replies alone.
        expect(primary.some((s) => phrases.embedding.test(s))).toBe(true);
        expect(primary.every((s) => /Mistral/.test(s))).toBe(false);
      });

      it(`${name}.${language} names Mistral AI the fallback for both`, () => {
        const doc = legalDocuments[name][language];
        const fallback = sentences(doc).filter((s) => phrases.fallback.test(s));
        expect(fallback.length).toBeGreaterThan(0);
        expect(fallback.some((s) => phrases.embedding.test(s))).toBe(true);
        expect(fallback.some((s) => /Mistral/.test(s))).toBe(true);
      });

      it(`${name}.${language} still discloses direct Mistral AI use`, () => {
        const text = flatten(legalDocuments[name][language]);
        expect(text).toMatch(phrases.direct);
      });
    }
  }

  // The specific regression: embeddings attributed to Mistral rather than Cortecs.
  const embeddingsMisattributed =
    /Mistral[^.]*(?:used to create|hutumika kuunda|anayetumika kuunda)[^.]*(?:embedding|uwakilishi wa kihesabu)/i;

  for (const { name, language, doc } of allDocs) {
    it(`${name}.${language} does not attribute embeddings to Mistral AI`, () => {
      expect(flatten(doc)).not.toMatch(embeddingsMisattributed);
    });
  }

  it("distinguishes the provider company from the model it runs", () => {
    expect(flatten(legalDocuments.subprocessors.en)).toMatch(
      /A provider is not the same thing as a model/i,
    );
    expect(flatten(legalDocuments.subprocessors.sw)).toMatch(
      /Mtoa huduma na muundo si kitu kimoja/i,
    );
  });
});

describe("no unsupported claims", () => {
  const forbidden = [
    {
      pattern: /end-to-end encrypt/i,
      allowIfNegated:
        /not end-to-end encrypt(?:ion|ed)|si usimbaji fiche kutoka/i,
      label: "end-to-end encryption",
    },
    { pattern: /only you and admins/i, label: "closed access list" },
    {
      pattern: /completely secure/i,
      allowIfNegated: /No service can be completely secure/i,
      label: "guaranteed security",
    },
    { pattern: /we guarantee/i, label: "unqualified guarantee" },
    { pattern: /permanently erased everywhere/i, label: "absolute erasure" },
    { pattern: /development \/ test server/i, label: "dev server warning" },
    { pattern: /training purposes/i, label: "training on user content" },
  ];

  for (const { name, language, doc } of allDocs) {
    for (const { pattern, allowIfNegated, label } of forbidden) {
      it(`${name}.${language} does not claim ${label}`, () => {
        const text = flatten(doc);
        const matches = text.match(new RegExp(pattern.source, "gi")) || [];
        if (matches.length === 0) return;
        // The only permitted mentions are the ones that explicitly deny the claim.
        expect(allowIfNegated).toBeTruthy();
        for (const sentence of text.split(/(?<=[.;])\s+/)) {
          if (pattern.test(sentence)) {
            expect(sentence).toMatch(allowIfNegated);
          }
        }
      });
    }
  }
});

// The public build renders the versioned production policy unconditionally.
// There is no build-time switch that can substitute a different document for it.
describe("privacy policy renders unconditionally", () => {
  it("serves the production policy with no gating applied", () => {
    renderLegal(<PrivacyPolicy />);
    expect(screen.getByText("UwenzAi Privacy Policy")).toBeTruthy();
    expect(screen.queryByText(/DEVELOPMENT \/ TEST SERVER/i)).toBeNull();
    expect(screen.queryByText(/SEVA YA MAENDELEO/)).toBeNull();
  });

  it("serves the Kiswahili production policy with no gating applied", () => {
    localStorage.setItem("eaaic-language", "sw");
    renderLegal(<PrivacyPolicy />);
    expect(screen.getByText("Sera ya Faragha ya UwenzAi")).toBeTruthy();
    expect(screen.queryByText(/SEVA YA MAENDELEO/)).toBeNull();
  });

  it("exposes exactly the three production legal documents", () => {
    expect(Object.keys(legalDocuments)).toEqual([
      "terms",
      "privacy",
      "subprocessors",
    ]);
  });
});

describe("photo message disclosures (2026-10-05)", () => {
  it.each(["en", "sw"])(
    "privacy.%s discloses chat-photo screening before storage",
    (language) => {
      const s8 = sectionText(legalDocuments.privacy[language], "8");
      if (language === "en") {
        expect(s8).toMatch(/child-safety screening/i);
        expect(s8).toMatch(/never your message text or caption/i);
        expect(s8).toMatch(/refused and never stored/i);
      } else {
        expect(s8).toMatch(/usalama wa watoto/i);
        expect(s8).toMatch(/si maandishi ya ujumbe wako/i);
        expect(s8).toMatch(/hazihifadhiwi kabisa/i);
      }
    },
  );

  it.each(["en", "sw"])(
    "privacy.%s states chat photos get application-level encryption while generated images do not",
    (language) => {
      const doc = flatten(legalDocuments.privacy[language]);
      if (language === "en") {
        expect(doc).toMatch(/chat photos are individually encrypted by us/i);
        expect(doc).toMatch(/rely on the storage provider's encryption only/i);
      } else {
        expect(doc).toMatch(
          /picha za mazungumzo zinasimbwa mmoja mmoja na sisi/i,
        );
        expect(doc).toMatch(
          /hutegemea usimbaji fiche wa mtoa huduma wa hifadhi pekee/i,
        );
      }
    },
  );

  it.each(["en", "sw"])(
    "privacy.%s discloses that the active chat provider receives the photo",
    (language) => {
      const s6 = sectionText(legalDocuments.privacy[language], "6");
      if (language === "en") {
        expect(s6).toMatch(
          /the photo is sent to that provider as part of the same request/i,
        );
      } else {
        expect(s6).toMatch(
          /picha hutumwa kwa mtoa huduma huyo kama sehemu ya ombi/i,
        );
      }
    },
  );

  it.each(["en", "sw"])(
    "subprocessors.%s discloses the Mistral photo safety screening",
    (language) => {
      const doc = flatten(legalDocuments.subprocessors[language]);
      if (language === "en") {
        expect(doc).toMatch(/child-safety screening of photos sent in chat/i);
        expect(doc).toMatch(/never your message text/i);
      } else {
        expect(doc).toMatch(
          /usalama wa watoto kwa picha zinazotumwa kwenye mazungumzo/i,
        );
        expect(doc).toMatch(/si maandishi ya ujumbe wako/i);
      }
    },
  );

  it.each(["en", "sw"])(
    "subprocessors.%s discloses photos can reach Cortecs and BYOK endpoints",
    (language) => {
      const doc = flatten(legalDocuments.subprocessors[language]);
      if (language === "en") {
        expect(doc).toMatch(
          /the photo is sent to Cortecs as part of the reply request/i,
        );
        expect(doc).toMatch(
          /photos you send are included in the requests to your endpoint/i,
        );
        expect(doc).toMatch(
          /photos you send are still screened by us before storage/i,
        );
      } else {
        expect(doc).toMatch(
          /picha hutumwa kwa Cortecs kama sehemu ya ombi la jibu/i,
        );
        expect(doc).toMatch(/picha unazotuma zinajumuishwa kwa maombi/i);
        expect(doc).toMatch(/bado huchambuliwa na sisi kabla ya kuhifadhiwa/i);
      }
    },
  );

  it.each(["en", "sw"])(
    "terms.%s keeps uploaded-photo prohibitions separate from generation prohibitions",
    (language) => {
      const s7 = sectionText(legalDocuments.terms[language], "7");
      if (language === "en") {
        expect(s7).toMatch(
          /The prohibitions in section 7\.2 govern what you may ask us to generate/i,
        );
        expect(s7).toMatch(
          /A separate rule governs photos you upload and send/i,
        );
        // Ordinary clothed photos of children must stay allowed (only child
        // nudity / sexual content blocks, unlike the generation rule).
        expect(s7).toMatch(
          /fully-clothed photos of children[^]*are not prohibited by this rule/i,
        );
        expect(s7).toMatch(/no sparks are charged for them/i);
      } else {
        expect(s7).toMatch(
          /Marufuku ya kifungu cha 7\.2 zinasimamia unachoruhusiwa kuomba tuzalishe/i,
        );
        expect(s7).toMatch(
          /Kanuni tofauti inasimamia picha unazopakia na kutuma/i,
        );
        expect(s7).toMatch(/hazipigwa marufuku na kanuni hii/i);
        expect(s7).toMatch(/hakuna cheche zinazotozwa kwa zile/i);
      }
    },
  );
});
