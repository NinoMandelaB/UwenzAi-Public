// src/i18n/legal/subprocessors.en.js
//
// Service-provider (subprocessor) disclosure, English. Only providers that are
// actually configured in the production codebase are listed here.

export const subprocessorsEn = {
  title: "UwenzAi Service Provider Disclosure",
  intro: [
    {
      type: "p",
      text: "UwenzAi Limited uses the third-party providers below to operate the Service. They process personal data on our instructions and under contract, only to deliver the function described. This page supports section 11 of our [Privacy Policy](/privacy).",
    },
    {
      type: "p",
      text: "Some providers are used only when a relevant feature is enabled or configured. Data-processing locations depend on the provider and the configured service, and processing may involve international transfers. See section 11 of the Privacy Policy for our international-transfer disclosure.",
    },
  ],
  sections: [
    {
      id: "1",
      heading: "1. Hosting, network, database and file storage",
      blocks: [
        {
          type: "table",
          head: ["Provider", "Purpose", "Data involved"],
          rows: [
            [
              "Railway",
              "Application hosting, PostgreSQL database, Redis cache and background queues, and the S3-compatible bucket that stores uploaded and generated images and the photos you send in chat.",
              "All account and service data, including your content. Stored data is encrypted at rest at the storage layer; image objects are private and reachable only through short-lived signed links. Photo files you send in chat are additionally encrypted with our own keys before they reach the bucket, so the storage provider holds them as ciphertext.",
            ],
            [
              "Cloudflare",
              "Network delivery and protection for our website and API: DNS, encrypted connections and protection against attacks. It also tells our servers which country a request comes from.",
              "Your IP address and the requests you send to our website and API, which pass through Cloudflare's network. For visit records we store only the country code Cloudflare provides, not your IP address.",
            ],
          ],
        },
      ],
    },
    {
      id: "2",
      heading: "2. AI chat replies and embeddings",
      blocks: [
        {
          type: "table",
          head: ["Provider", "Purpose", "Data involved"],
          rows: [
            [
              "Cortecs",
              "Primary provider for both kinds of AI text processing we do: generating chat replies, and creating the numeric embeddings that power character memory. When a chat message includes a photo and the active model can view images, the photo is sent to Cortecs as part of the reply request.",
              "For replies, the conversation context: your messages, the photos you send when the active model can view them, the character definition, and relevant summaries and memory notes. For embeddings, the text of the memory note being stored or the text being matched against it. No payment data.",
            ],
            [
              "Mistral AI",
              "Fallback provider for chat replies and for embeddings, used when Cortecs fails or is not configured. Also called directly for background text tasks: extracting memory notes, writing conversation summaries, drafting character persona details, and judging conversation tone. Also our current provider for the automated child-safety and prohibited-content screening of photos sent in chat and of the images our image-generation providers return: a downscaled, metadata-free copy of the image is sent for classification before it is stored or shown. Only the image is sent for that screening — never your message text or caption.",
              "The same conversation context and memory note text as above, depending on which task is running, and — for photo screening — the downscaled image copy described here.",
            ],
            [
              "Your own AI provider (Bring Your Own Key)",
              "Used for chat replies instead of the providers above only if you configure your own endpoint and API key. When your configured model can view images, photos you send are included in the requests to your endpoint. Embeddings and the background tasks continue to use the providers above, and photos you send are still screened by us before storage (see the Mistral AI row).",
              "Your conversation context — including photos you send, when your model can view them — is sent to the endpoint you chose. That provider’s own terms apply and we have no control over its retention.",
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
      heading: "4. Web search",
      blocks: [
        {
          type: "table",
          head: ["Provider", "Purpose", "Data involved"],
          rows: [
            [
              'European Perspective SAS ("Staan")',
              "Web search retrieval (only when the user enables web search in a conversation). Location: France / EU (OVHcloud).",
              "Search queries derived from user messages; no account data, no conversation history. May use a fallback search provider that does not retain queries. Privacy policy: https://staan.ai/privacy-policy",
            ],
          ],
        },
      ],
    },
    {
      id: "5",
      heading: "5. Payments",
      blocks: [
        {
          type: "table",
          head: ["Provider", "Purpose", "Data involved"],
          rows: [
            [
              "Paystack",
              "Processing spark and subscription purchases on our website, including mobile-money and card payment methods.",
              "Transaction amount, currency, our reference and what was purchased, plus the payment details you enter directly with Paystack. We do not receive or store your full card number, card security code or PIN.",
            ],
            [
              "Google Play (Google)",
              "Processing spark purchases in our Android app through Google Play's billing system, and reporting refunds and chargebacks of those purchases to us.",
              "The purchase token, Google Play order number, product, purchase status and time, and the refund details Google reports. Google handles the payment itself under its own terms and privacy policy, and we do not receive your card or other payment-method details. Google may make your name and email address available to us as the seller for handling orders.",
            ],
          ],
        },
      ],
    },
    {
      id: "6",
      heading: "6. Sign-in, email and abuse prevention",
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
      id: "7",
      heading: "7. Error monitoring (optional)",
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
      id: "8",
      heading: "8. UwenzAi-Dev developer workspace and inference API",
      blocks: [
        {
          type: "p",
          text: "UwenzAi-Dev provides an experimental developer workspace, a text playground and an inference API for eligible signed-in account holders. It is separate from the Bring Your Own Key feature and is not available to guests.",
        },
        {
          type: "p",
          text: "The initial gateway provider set is Cortecs, Mistral AI, Fikra, Inceptron, AKI.IO, OVHcloud and TensorX. A provider is available only when configured and offered in the gateway catalog. Depending on the selected provider/model, the provider processing your request receives your submitted messages, any history included in the request, and the settings needed to produce the reply. Listing a provider here does not mean every request is sent to that provider.",
        },
        {
          type: "p",
          text: "The gateway’s ordinary usage records contain operational and billing metadata rather than message or reply content. If you choose to save playground history, UwenzAi separately stores the retained conversation in encrypted form for your account and selected model/provider. The default-off setting, storage limits, retention period and deletion controls are described in sections 9, 14 and 15 of the Privacy Policy. Hosting infrastructure processes the data needed to operate this feature, as described in section 1 of this disclosure.",
        },
        {
          type: "p",
          text: "Provider availability may change. We will update this disclosure when providers are added or removed and give notice of material processing changes as described in section 20 of the Privacy Policy. Neither the word “experimental” nor a change to this list removes your statutory data-protection rights.",
        },
      ],
    },
    {
      id: "9",
      heading: "9. Changes to this list",
      blocks: [
        {
          type: "p",
          text: "We may add or replace a provider as the Service develops. When we do, we will update this page and change the “Last updated” date. If a change materially affects how your personal data is processed, we will give notice as described in section 20 of the [Privacy Policy](/privacy).",
        },
        {
          type: "p",
          text: "Questions about any provider on this list: [info@uwenzai.com](mailto:info@uwenzai.com).",
        },
      ],
    },
    {
      id: "10",
      heading: "10. Scope of this list",
      blocks: [
        {
          type: "p",
          text: "Sections 1 to 7 describe the providers that operate the Service as a whole, including the companion app and the account, search and payment features around it. Section 8 describes the providers behind the separate UwenzAi-Dev developer offering. A provider added to the UwenzAi-Dev gateway is not thereby used for the companion app.",
        },
        {
          type: "p",
          text: "For the companion app, the AI providers that receive conversation content — your messages, character definitions, memory notes and the numeric embeddings that power character memory — are based in the European Union: Cortecs in Austria and Mistral AI in France. Companion conversation content that we send on your behalf is therefore processed only by EU-based AI providers; the sole exception is a Bring Your Own Key endpoint, if you configure one yourself. Providers headquartered outside the EU never receive companion conversation content: the non-EU AI provider Fikra (Kenya) is used exclusively for the UwenzAi-Dev developer gateway described in section 8, and non-EU providers that support other functions (for example image generation, payments, sign-in or email delivery) operate under the transfer safeguards described in section 11 of the [Privacy Policy](/privacy).",
        },
      ],
    },
  ],
  footer:
    "UwenzAi Limited – Nairobi, Kenya. Questions? [info@uwenzai.com](mailto:info@uwenzai.com)",
};
