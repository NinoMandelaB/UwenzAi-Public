import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { LanguageProvider } from "../../context/LanguageContext";
import { LEGAL_LAST_UPDATED, legalDocuments } from "../../i18n/legal";
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
  // and "section 19" point into the Terms, not at its own seven sections.
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

  it("keeps the disclosure's cross-document citations pointing at the right Terms sections", () => {
    expect(sectionText(legalDocuments.terms.en, "10")).toMatch(/BYOK/);
    expect(sectionText(legalDocuments.terms.en, "19")).toMatch(
      /Changes to the Service and Terms/i,
    );
  });
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
      allowIfNegated: /not end-to-end encryption|si usimbaji fiche kutoka/i,
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
