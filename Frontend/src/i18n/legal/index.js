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

// Display date on the legal pages. 2026-10-09: Spark purchases in the Android
// app through Google Play Billing (Terms §§21.12–21.14: price, sign-in, Google
// Play's 48-hour refund route, automatic refund of uncredited payments and
// removal of Sparks for refunded/charged-back payments; Privacy §§2 and 12:
// the purchase record received from Google and the buyer name/email Google may
// share with us as seller; Provider Disclosure §5), sign-up promo codes (Terms
// §21.15; Privacy §§4, 5, 14 and 15) and first-party visit records with the
// country taken from Cloudflare (Privacy §§4, 5, 11, 13, 14 and 15; Provider
// Disclosure §1, which now lists Cloudflare).
// 2026-10-07: Privacy Policy §4 extended for
// advisory model-refusal records — when the companion model itself declines a
// request, the refusal is counted as an advisory safety record (category, time
// and an irreversible fingerprint only), never counts toward enforcement or
// automatic locks, survives user-side deletions, and can only be reviewed or
// reset by an administrator, with every reset audit-logged; refusal records
// are deleted together with the account when the account is deleted. Terms
// §§6.7 and 14.4 and Privacy §4 also state the default recording-only mode of
// chat-message screening (safety records are kept for administrator review;
// nothing is blocked or locked automatically, and an administrator decides),
// and Privacy §10 notes that a message the user reports is seen by support.
// 2026-10-06: all
// three documents updated for the cross-session safety ledger — Terms §§6.7
// and 14.4 (automated safety screening, warnings, account safety lock and
// admin review), Privacy Policy §§4, 10, 14 and 15 (safety records:
// category/severity metadata only, never message text; admin visibility
// limited to summaries; retention and the child-safety retention basis).
// Users who registered earlier keep the consent version they accepted at the
// time.
// 2026-10-13: image rules — explicit nudity banned for both generation and
// uploads, section 6.2's prohibitions applied to images, the 7.2/7.3
// child-photo carve-out clarified, the VLM screening categories named, and
// the Mistral subprocessor row extended to generated images.
export const LEGAL_LAST_UPDATED = "13-OCT-2026";

// Sent with registration consent so we can prove which version a user accepted.
// 2026-10-13 applies the section 6.2 prohibitions to image generation and
// uploads, bans explicit nudity, extends the Mistral screening to generated
// images, and names the five screening categories. Material change to the
// content rules (Terms §19.2): advance notice required for existing users.
// 2026-10-09 adds the Google Play, promo code and visit-record disclosures
// described above on top of 2026-10-07 (new purchase channel, new data
// categories and a new provider, so the version moves; Terms §19.2 and
// Privacy §20 require advance notice of the change to existing users).
// 2026-10-07 adds the advisory model-refusal disclosures described above on top
// of 2026-10-06 (the safety-ledger disclosures). 2026-10-06 adds the
// safety-ledger disclosures on top of 2026-10-05 (photo messages: upload
// prohibitions, screening enforcement and provider disclosure). Existing
// acceptance records retain the version accepted at the time.
export const LEGAL_POLICY_VERSION = "2026-10-13";

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
