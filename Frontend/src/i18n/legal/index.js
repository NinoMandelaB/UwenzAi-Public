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

export const LEGAL_LAST_UPDATED = "14-SEP-2026";

// Sent with registration consent so we can prove which version a user accepted.
// 2026-09-14 covers UwenzAi-Dev processing, saved history and account exports,
// the commercial API exception, shared-account distinction and no-SLA clause.
// Existing acceptance records retain the version accepted at the time.
export const LEGAL_POLICY_VERSION = "2026-09-14";

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
