// src/i18n/legal/index.js
//
// Single entry point for legal document content. Each document exists in both
// languages so LegalDocument can switch on the active LanguageContext value.

import { termsEn } from "./terms.en";
import { termsSw } from "./terms.sw";
import { privacyEn } from "./privacy.en";
import { privacySw } from "./privacy.sw";
import { subprocessorsEn } from "./subprocessors.en";
import { subprocessorsSw } from "./subprocessors.sw";

export const LEGAL_LAST_UPDATED = "08-AUG-2026";

// Sent with registration consent so we can prove which version a user accepted.
// The suffix distinguishes same-day revisions: .1 corrected the AI-provider
// disclosure, which had named the wrong embeddings provider, and .2 added the
// severability and language-precedence clauses to the Terms.
export const LEGAL_POLICY_VERSION = "2026-08-08";

export const legalDocuments = {
  terms: { en: termsEn, sw: termsSw },
  privacy: { en: privacyEn, sw: privacySw },
  subprocessors: { en: subprocessorsEn, sw: subprocessorsSw },
};

export function getLegalDocument(name, language) {
  const doc = legalDocuments[name];
  if (!doc) return null;
  return doc[language] || doc.en;
}
