// src/i18n/legal/subprocessors.en.js
//
// Service-provider (subprocessor) disclosure, English. Only providers that are
// actually configured in the production codebase are listed here.

export const subprocessorsEn = {
  title: "UwenzAi Service Provider Disclosure",
  intro: [
    {
      type: "p",
      text: "UwenzAi Limited uses the third-party providers below to operate the Service. They process personal data on our instructions and under contract, only to deliver the function described. This page supports section 10 of our [Privacy Policy](/privacy).",
    },
    {
      type: "p",
      text: "All of these providers operate outside Kenya, so using the Service involves transferring data across borders. Some providers are used only when the relevant feature is enabled or configured.",
    },
  ],
  sections: [
    {
      id: "1",
      heading: "1. Hosting, database and file storage",
      blocks: [
        {
          type: "table",
          head: ["Provider", "Purpose", "Data involved"],
          rows: [
            [
              "Railway",
              "Application hosting, PostgreSQL database, Redis cache and background queues, and the S3-compatible bucket that stores uploaded and generated images.",
              "All account and service data, including your content. Stored data is encrypted at rest at the storage layer; image objects are private and reachable only through short-lived signed links.",
            ],
          ],
        },
      ],
    },
    {
      id: "2",
      heading: "2. AI text generation and embeddings",
      blocks: [
        {
          type: "table",
          head: ["Provider", "Purpose", "Data involved"],
          rows: [
            [
              "Cortecs",
              "Primary provider for both kinds of AI text processing we do: generating chat replies, and creating the numeric embeddings that power character memory.",
              "For replies, the conversation context: your messages, the character definition, and relevant summaries and memory notes. For embeddings, the text of the memory note being stored or the text being matched against it. No payment data.",
            ],
            [
              "Mistral AI",
              "Fallback provider for chat replies and for embeddings, used when Cortecs fails or is not configured. Also called directly for background text tasks: extracting memory notes, writing conversation summaries, drafting character persona details, and judging conversation tone.",
              "The same conversation context and memory note text as above, depending on which task is running.",
            ],
            [
              "Your own AI provider (Bring Your Own Key)",
              "Used for chat replies instead of the providers above only if you configure your own endpoint and API key. Embeddings and the background tasks continue to use the providers above.",
              "Your conversation context is sent to the endpoint you chose. That provider’s own terms apply and we have no control over its retention.",
            ],
          ],
        },
        {
          type: "p",
          text: "A provider is not the same thing as a model. Cortecs is an inference platform that runs third-party open models on our instructions; Mistral AI develops and hosts its own. The company named in the table is the one that receives your data. Which model each of them runs is set in our configuration and can change without changing who processes the data.",
        },
      ],
    },
    {
      id: "3",
      heading: "3. Image generation",
      blocks: [
        {
          type: "table",
          head: ["Provider", "Purpose", "Data involved"],
          rows: [
            [
              "fal.ai",
              "Primary image-generation provider, running the image model we use for avatars, gallery images and in-chat images.",
              "The generated prompt text, and — for features that keep a character’s appearance consistent — an existing character image uploaded as a reference. No account identifiers are sent. We ask the provider not to store request inputs and outputs, and we make a best-effort deletion request after collecting the result.",
            ],
            [
              "Stability AI",
              "Fallback image-generation provider, used when the primary provider is unavailable or not configured.",
              "The generated prompt text and, for reference-guided requests, the reference image.",
            ],
          ],
        },
      ],
    },
    {
      id: "4",
      heading: "4. Payments",
      blocks: [
        {
          type: "table",
          head: ["Provider", "Purpose", "Data involved"],
          rows: [
            [
              "Paystack",
              "Processing spark and subscription purchases, including mobile-money and card payment methods.",
              "Transaction amount, currency, our reference and what was purchased, plus the payment details you enter directly with Paystack. We do not receive or store your full card number, card security code or PIN.",
            ],
          ],
        },
      ],
    },
    {
      id: "5",
      heading: "5. Sign-in, email and abuse prevention",
      blocks: [
        {
          type: "table",
          head: ["Provider", "Purpose", "Data involved"],
          rows: [
            [
              "Google",
              "Google Sign-In, and reCAPTCHA bot protection on sensitive forms.",
              "For sign-in, the account identifier and basic profile details Google returns. For reCAPTCHA, technical signals Google uses to judge whether a request is automated.",
            ],
            [
              "Our email delivery provider",
              "Sending transactional email over an authenticated, encrypted SMTP connection — for example address verification and password recovery.",
              "Your email address and the contents of that message. We do not send marketing email through this channel.",
            ],
          ],
        },
      ],
    },
    {
      id: "6",
      heading: "6. Error monitoring (optional)",
      blocks: [
        {
          type: "table",
          head: ["Provider", "Purpose", "Data involved"],
          rows: [
            [
              "Sentry",
              "Capturing technical diagnostics when the application fails. Used only when enabled in our configuration.",
              "Error and performance diagnostics. It is configured not to send personal data by default.",
            ],
          ],
        },
      ],
    },
    {
      id: "7",
      heading: "7. Changes to this list",
      blocks: [
        {
          type: "p",
          text: "We may add or replace a provider as the Service develops. When we do, we will update this page and change the “Last updated” date. If a change materially affects how your personal data is processed, we will give notice as described in section 19 of the [Privacy Policy](/privacy).",
        },
        {
          type: "p",
          text: "Questions about any provider on this list: [info@uwenzai.com](mailto:info@uwenzai.com).",
        },
      ],
    },
  ],
  footer:
    "UwenzAi Limited – Nairobi, Kenya. Questions? [info@uwenzai.com](mailto:info@uwenzai.com)",
};
