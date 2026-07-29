// src/pages/terms/LegalDocument.jsx

import { ArrowLeft } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useLanguage } from "../../context/LanguageContext";
import { getLegalDocument, LEGAL_LAST_UPDATED } from "../../i18n/legal";
import { parseLinks } from "../../utils/messageLinks";

// Legal pages are reachable while signed out, so they carry their own language
// switcher rather than relying on the one in the authenticated shell.
function LanguageSwitch() {
  const { language, setLanguage, t } = useLanguage();

  const option = (code, label) => (
    <button
      key={code}
      type="button"
      onClick={() => setLanguage(code)}
      aria-pressed={language === code}
      className="rounded-full px-3 py-1 text-xs font-semibold transition-colors"
      style={{
        backgroundColor:
          language === code ? "var(--color-accent)" : "transparent",
        color: language === code ? "#082f49" : "var(--color-text-muted)",
      }}
    >
      {label}
    </button>
  );

  return (
    <div
      className="flex items-center gap-1 rounded-full border p-1"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-soft)",
      }}
      role="group"
      aria-label={t("legal.languageLabel")}
    >
      {option("en", "EN")}
      {option("sw", "SW")}
    </div>
  );
}

function Block({ block, navigate }) {
  if (block.type === "callout") {
    return (
      <div
        className="rounded-2xl border-2 p-5 space-y-2"
        style={{
          borderColor: "rgb(245, 158, 11)",
          backgroundColor: "rgba(245, 158, 11, 0.08)",
          color: "rgb(180, 120, 0)",
        }}
      >
        <p className="text-base font-bold">{block.title}</p>
        {block.blocks.map((inner, index) => (
          <Block key={index} block={inner} navigate={navigate} />
        ))}
      </div>
    );
  }

  if (block.type === "ul") {
    return (
      <ul className="list-disc pl-5 space-y-1">
        {block.items.map((item, index) => (
          <li key={index}>{parseLinks(item, navigate)}</li>
        ))}
      </ul>
    );
  }

  if (block.type === "table") {
    return (
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs border-collapse">
          <thead>
            <tr>
              {block.head.map((cell, index) => (
                <th
                  key={index}
                  className="border p-2 font-semibold align-top"
                  style={{ borderColor: "var(--color-border)" }}
                >
                  {cell}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <td
                    key={cellIndex}
                    className="border p-2 align-top"
                    style={{ borderColor: "var(--color-border)" }}
                  >
                    {parseLinks(cell, navigate)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return <p>{parseLinks(block.text, navigate)}</p>;
}

export default function LegalDocument({ name }) {
  const navigate = useNavigate();
  const { language, t } = useLanguage();
  const doc = getLegalDocument(name, language);

  if (!doc) return null;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <button
          onClick={() => navigate(-1)}
          className="flex items-center gap-2 text-sm font-medium theme-text-muted hover:underline"
        >
          <ArrowLeft size={16} />
          {t("legal.back")}
        </button>
        <LanguageSwitch />
      </div>

      <div className="max-w-prose mx-auto space-y-6 text-sm theme-text leading-relaxed">
        <h1 className="text-2xl font-semibold theme-text">{doc.title}</h1>
        <p className="text-xs text-theme-muted italic">
          {`${t("legal.lastUpdated")}: ${LEGAL_LAST_UPDATED}`}
        </p>

        {doc.intro.map((block, index) => (
          <Block key={`intro-${index}`} block={block} navigate={navigate} />
        ))}

        {doc.sections.map((section) => (
          <section key={section.id}>
            <h2 className="text-lg font-semibold theme-text mt-6 mb-2">
              {section.heading}
            </h2>
            <div className="space-y-2">
              {section.blocks.map((block, index) => (
                <Block
                  key={`${section.id}-${index}`}
                  block={block}
                  navigate={navigate}
                />
              ))}
            </div>
          </section>
        ))}

        <p className="text-xs text-theme-muted pt-4">
          {parseLinks(doc.footer, navigate)}
        </p>
      </div>
    </div>
  );
}
