// src/i18n/legal/privacy.en.js
//
// Production Privacy Policy (English). Content is data-only so it can be
// rendered by LegalDocument and asserted on directly in tests.

export const privacyEn = {
  title: "UwenzAi Privacy Policy",
  intro: [
    {
      type: "p",
      text: "This Privacy Policy explains how UwenzAi Limited (“UwenzAi”, “we”, “us”, “our”), a company registered in Kenya with its base in Nairobi, collects and uses information when you use the UwenzAi AI-companion service, including our websites and applications (the “Service”).",
    },
    {
      type: "p",
      text: "UwenzAi Limited is the data controller for the personal data described here. You can reach us at [info@uwenzai.com](mailto:info@uwenzai.com).",
    },
    {
      type: "p",
      text: "This policy applies to the production Service. It is written to describe what our systems actually do, including where our protections have limits.",
    },
  ],
  sections: [
    {
      id: "1",
      heading: "1. Summary of the key points",
      blocks: [
        {
          type: "ul",
          items: [
            "You can use UwenzAi as a guest without creating an account. We identify guests with a secure cookie so your conversations stay available on your device.",
            "Your conversations stay private to your account. They are not published, not shown to other users, and not sold.",
            "Message text, conversation summaries and memory notes are encrypted in our database using keys held by our servers. This is not end-to-end encryption — our systems can decrypt this content, and a small number of authorised personnel can access it where necessary.",
            "To generate replies and images, we must send your prompts — and, for some image features, a reference image — to the AI providers that power those features.",
            "Images are stored in a private, access-controlled bucket that our hosting provider encrypts at rest. We do not apply a separate per-image encryption layer of our own.",
            "You can export your data and delete your account from inside the app.",
            "We do not use your private conversations to train AI models, and we do not run advertising or third-party analytics trackers.",
          ],
        },
      ],
    },
    {
      id: "2",
      heading: "2. Using UwenzAi without an account",
      blocks: [
        {
          type: "p",
          text: "You may start chatting as a guest. When you do, our server creates a random, high-entropy identifier and places it in a cookie on your device. We store only a hashed form of that identifier, so the cookie value itself is not kept in our database in readable form.",
        },
        {
          type: "ul",
          items: [
            "The guest cookie is set as HttpOnly, so page scripts cannot read it, and it is transmitted securely in production.",
            "It is a persistent cookie so your chats survive a browser restart. It currently lasts up to 180 days, and a guest session also expires after a period of inactivity.",
            "This cookie is strictly necessary to provide guest chat. It is not an advertising or tracking cookie.",
            "As a guest you can create conversations and messages, create characters, generate images, and buy sparks. Guest use is subject to daily and lifetime free-usage limits.",
          ],
        },
        {
          type: "p",
          text: "Guest activity is linked to a device credential rather than to your name. It is pseudonymous, not fully anonymous: anyone with access to that device and cookie can reach those conversations.",
        },
      ],
    },
    {
      id: "3",
      heading: "3. What happens to guest data when you create an account",
      blocks: [
        {
          type: "p",
          text: "The first time you sign in to a verified account from a device that has guest history, we transfer that history to your account so you do not lose it. This transfer covers:",
        },
        {
          type: "ul",
          items: [
            "your guest conversations and their messages;",
            "your remaining spark balance, recorded in your account ledger;",
            "your usage and billing records; and",
            "any purchases you made as a guest.",
          ],
        },
        {
          type: "p",
          text: "Purchase and billing records are retained after the transfer. For accounting, fraud-prevention and dispute-handling purposes, each purchase keeps a permanent internal note of the guest session it originated from, and the underlying guest usage record is retained in a zeroed state rather than deleted. This means a purchase can still be traced to its original guest session after you convert to an account.",
        },
      ],
    },
    {
      id: "4",
      heading: "4. Information we collect",
      blocks: [
        {
          type: "p",
          text: "Account and profile information. Your username and display name, your email address, an optional phone number, your language preference, whether you signed up directly or with Google, and your account status. Email addresses and phone numbers are stored encrypted, alongside a separate hashed copy that lets us look up an account without reading the value.",
        },
        {
          type: "p",
          text: "Consent records. When you register we record that you confirmed you are an adult and accepted these policies, together with the policy version, the time, your IP address and your browser user-agent string. We keep these as proof of consent.",
        },
        {
          type: "p",
          text: "Your content. The characters you create and their attributes, your conversations and messages, conversation summaries, the memory notes the assistant keeps about your preferences (including numeric embeddings used to retrieve them), images you upload, and images generated for you.",
        },
        {
          type: "p",
          text: "Usage, billing and support data. Your spark balance and transactions, purchases, subscriptions, token usage and cost records, support tickets, feedback you submit, and any content reports you file.",
        },
        {
          type: "p",
          text: "Technical data. Information needed to run and secure the Service, such as authentication cookies, CSRF tokens, request metadata, rate-limiting and failed-login counters, and server logs. Our logs are written to record operational events rather than the content of your conversations.",
        },
        {
          type: "p",
          text: "Your own API credentials (optional). If you use Bring Your Own Key, we store the API key you supply in encrypted form. A custom system prompt you provide is deliberately stored unencrypted so that it can be reviewed for moderation purposes — please do not put passwords or other secrets in it.",
        },
      ],
    },
    {
      id: "5",
      heading: "5. Why we process your information, and on what basis",
      blocks: [
        {
          type: "ul",
          items: [
            "To provide the Service you asked for — running chats, characters, memory, image generation and purchases. Basis: performance of our contract with you, or the necessary steps to provide a service you requested as a guest.",
            "To take payment and keep accurate financial records. Basis: contract and our legal obligations.",
            "To keep the Service safe — preventing abuse and fraud, enforcing our prohibited-content rules, reviewing reports, and protecting other users. Basis: our legitimate interests and our legal obligations.",
            "To support you and to fix faults. Basis: contract and legitimate interests.",
            "To confirm you are an adult and to record your acceptance of our terms. Basis: legal obligation and legitimate interests.",
            "To send service and account emails, such as verification and password-recovery messages. Basis: contract and legitimate interests.",
          ],
        },
        {
          type: "p",
          text: "We do not use your private conversations, characters or images to train our own or third-party AI models, and we do not sell your personal data.",
        },
      ],
    },
    {
      id: "6",
      heading: "6. AI providers and how your prompts are processed",
      blocks: [
        {
          type: "p",
          text: "UwenzAi does not run its own AI models. To produce a reply we send the relevant conversation context — which includes your messages, the character definition, and relevant memory notes and summaries — to a third-party AI provider over an encrypted connection. This processing is unavoidable if you want the Service to work.",
        },
        {
          type: "ul",
          items: [
            "Cortecs is our primary AI provider: it generates chat replies, and it also creates the numeric embeddings that power character memory.",
            "Mistral AI is the fallback provider for chat replies and for embeddings, used when Cortecs fails or is not configured. We also send text to Mistral AI directly for background tasks: extracting memory notes, writing conversation summaries, drafting character persona details, and judging conversation tone.",
            "Cortecs is an inference platform that runs third-party open models on our instructions, while Mistral AI develops and hosts its own. The provider we name is the company that receives the data; the particular model it runs is a configuration choice and may change.",
            "If you use Bring Your Own Key, your chat replies are instead generated by the third-party endpoint you configured. That provider’s own privacy terms then govern what it does with your content, and we have no control over or visibility into its retention. Embeddings and the background tasks above continue to use the providers we name here.",
            "Our prohibited-content rules and our billing continue to apply when you use your own key.",
          ],
        },
      ],
    },
    {
      id: "7",
      heading: "7. Web Search (optional feature)",
      blocks: [
        {
          type: "p",
          text: "When you enable the web search toggle in a conversation, we send a search query derived from your message to our search provider, Staan, operated by European Perspective SAS (42 avenue de la Porte de Clichy, 75017 Paris, France), in order to retrieve up-to-date information from the web. Only the search query is transmitted — never your name, account details, or conversation history.",
        },
        {
          type: "p",
          text: "Staan is a European provider: queries are processed on servers located in the European Union (OVHcloud, France), are not used to train AI models, and are not shared with third parties for commercial purposes. Where Staan's own search index cannot answer a query, Staan may use a fallback search provider; according to Staan, such queries are not retained by the fallback provider. For details, see Staan's privacy policy at https://staan.ai/privacy-policy.",
        },
        {
          type: "p",
          text: "Staan's FAQ is available at https://staan.ai/faq.",
        },
        {
          type: "p",
          text: "When the AI assistant opens a specific web page to answer your question, our server fetches that page directly. The website you are reading about sees only our server's technical request — never your IP address, identity, or account information.",
        },
        {
          type: "p",
          text: "Web search is switched off by default. You can enable or disable it at any time in each conversation's settings. Legal basis for this processing: performance of our contract with you (Art. 6(1)(b) GDPR) — the feature runs only when you actively enable it.",
        },
      ],
    },
    {
      id: "8",
      heading: "8. Images: generation, reference images, storage and access",
      blocks: [
        {
          type: "p",
          text: "Image generation. When you request an image we send the generated prompt text — built from the character’s attributes and the instructions you add — to fal.ai, which runs the image model we use. If fal.ai is unavailable, Stability AI may be used instead. We do not send your name, email or account identifier with these requests.",
        },
        {
          type: "p",
          text: "Reference images. Some features keep a character’s appearance consistent by sending an existing image of that character to fal.ai as a reference. To do this, the image file is uploaded to fal.ai’s content-delivery network, which returns a link that the image model reads. That link is not published by us, but it is hosted on the provider’s infrastructure rather than ours while the request is processed.",
        },
        {
          type: "p",
          text: "We ask fal.ai not to retain the inputs and outputs of these requests, and after we download a generated image we make a best-effort request to delete the job from the provider. We cannot independently verify a provider’s internal deletion, so we do not promise that no copy exists on their systems.",
        },
        {
          type: "p",
          text: "Storage. Uploaded and generated images are stored in a Railway S3-compatible bucket. The bucket is configured to be private: objects carry no public-read permission, and every link we hand out is a time-limited signed URL, valid for about one hour by default. Railway encrypts the stored data at rest at the storage layer. We do not add a separate per-image or application-level encryption layer on top of that, so we do not claim your images are individually encrypted by us.",
        },
        {
          type: "p",
          text: "Access. Images you generate in a chat, and gallery images you mark private, are not published in the public gallery and are not shown to other users. In practice they can be accessed by you, by authorised UwenzAi personnel where necessary for moderation, support, security or legal compliance, and by the infrastructure and image-generation providers we use to store and produce them. You should also know that a signed link is itself a credential: while it is valid, anyone you forward it to can open the image without signing in.",
        },
      ],
    },
    {
      id: "9",
      heading: "9. Encryption — and what it does not mean",
      blocks: [
        {
          type: "p",
          text: "We apply application-level encryption to the most sensitive text in our database. Message content, conversation summaries and memory notes are encrypted before they are written, as are stored email addresses, phone numbers and any Bring Your Own Key credential.",
        },
        {
          type: "p",
          text: "The encryption keys are held by our servers, not by you. That has two consequences we want to state plainly:",
        },
        {
          type: "ul",
          items: [
            "This is not end-to-end encryption. Our systems can decrypt your messages, and a limited number of authorised personnel can access them where it is necessary for moderation, debugging, support, security or legal compliance.",
            "Encryption protects data at rest in the database. It does not prevent the processing described elsewhere in this policy — in particular, the content sent to AI providers to generate replies and images.",
          ],
        },
        {
          type: "p",
          text: "Some information is not encrypted at the application level, including character attributes, message timestamps and status, token and cost records, and any custom system prompt you supply.",
        },
        {
          type: "p",
          text: "No security measure is perfect. We do not guarantee that your data can never be accessed, altered or lost.",
        },
      ],
    },
    {
      id: "10",
      heading: "10. Who can see your content",
      blocks: [
        {
          type: "ul",
          items: [
            "You. Your conversations, private characters and private images are visible to you in your account, and are not shown to other users.",
            "Authorised UwenzAi personnel. Administrators can access account records and, where necessary, user content — for example to investigate a report, handle a support ticket, respond to a security incident, or comply with a legal obligation. Access is limited to staff who need it for those purposes.",
            "Our service providers. The infrastructure, AI and payment providers listed in our provider disclosure process the data needed to deliver the feature you used, under contract and on our instructions.",
            "Other users, only where you choose it. If you publish a character or mark a gallery image public, it becomes visible to others. Your conversations are never made public by us.",
            "Authorities, where the law requires it. We may disclose information if we are legally obliged to, or to protect our rights, users or the public.",
          ],
        },
      ],
    },
    {
      id: "11",
      heading: "11. Service providers and international transfers",
      blocks: [
        {
          type: "p",
          text: "We rely on third-party providers for hosting and databases, AI text generation, image generation, payments, email delivery, sign-in and bot protection, and optional error monitoring. The current list, with the purpose of each, is set out in our [Service Provider Disclosure](/subprocessors).",
        },
        {
          type: "p",
          text: "UwenzAi is based in Kenya, but these providers operate outside Kenya, and our hosting region is determined by our hosting provider’s configuration. Using the Service therefore involves transferring your data across borders, including to providers in Europe and the United States.",
        },
        {
          type: "p",
          text: "When we transfer personal data outside Kenya we rely on the grounds available under the Kenyan Data Protection Act, 2019 — in particular that the transfer is necessary to perform our contract with you and is subject to appropriate contractual safeguards with the provider. Where a provider is covered by European data-protection rules, transfers are made on the standard contractual terms that provider offers.",
        },
      ],
    },
    {
      id: "12",
      heading: "12. Payments",
      blocks: [
        {
          type: "p",
          text: "Purchases are processed by Paystack, currently in Kenyan Shillings. Payment methods such as mobile money and cards are offered and handled by Paystack, not by us.",
        },
        {
          type: "p",
          text: "We do not receive or store your full card number, card security code, or PIN. What we keep is the record of the transaction: our own reference, the amount and currency, what was purchased, the references and status Paystack returns to us, and whether the purchase was fulfilled. Paystack processes your payment details under its own privacy policy.",
        },
        {
          type: "p",
          text: "We retain purchase records even after an account is deleted, with the account link removed, because we need them for accounting, tax, fraud-prevention and dispute-handling purposes.",
        },
      ],
    },
    {
      id: "13",
      heading: "13. Cookies, local storage and analytics",
      blocks: [
        {
          type: "p",
          text: "We use only the cookies needed to run the Service. We do not use advertising cookies, and we do not embed third-party analytics or tracking scripts such as advertising pixels.",
        },
        {
          type: "ul",
          items: [
            "A guest identity cookie, so guest conversations remain available on your device.",
            "A refresh-token cookie for signed-in sessions, set as HttpOnly and currently lasting up to 7 days.",
            "A CSRF token cookie, which the browser must be able to read in order to protect form and API requests against cross-site attacks.",
          ],
        },
        {
          type: "p",
          text: "Your browser’s local storage also holds preferences such as your chosen language and theme, and a cached copy of your session so you stay signed in. You can clear these through your browser at any time, which will sign you out and, for guests, disconnect you from earlier guest conversations.",
        },
        {
          type: "p",
          text: "We may enable a third-party error-monitoring service to capture technical diagnostics when something breaks. Where it is enabled, it is configured not to send personal data by default.",
        },
      ],
    },
    {
      id: "14",
      heading: "14. How long we keep information",
      blocks: [
        {
          type: "ul",
          items: [
            "Your account content — conversations, messages, characters, memory notes and images — is kept while your account exists, so that it is there when you return. We do not currently run an automatic job that deletes old conversations after a fixed period.",
            "Guest data is tied to the guest credential, which currently lasts up to 180 days and also expires after a period of inactivity.",
            "Verification and password-recovery codes expire quickly, typically within minutes, and are refused after that.",
            "Purchase, billing and consent records are kept for as long as we need them to meet accounting, tax and legal obligations, and to defend against disputes.",
            "Support tickets and content reports are kept while they are open and for a reasonable period afterwards, so that repeat problems can be handled.",
          ],
        },
        {
          type: "p",
          text: "You can delete individual conversations, characters and images yourself at any time, and you can clear a character’s memory from its settings.",
        },
      ],
    },
    {
      id: "15",
      heading: "15. Deleting your account — and the limits of deletion",
      blocks: [
        {
          type: "p",
          text: "You can delete your account from your settings. This is a permanent deletion, not a deactivation. It removes your user record and, through cascading deletion, the characters you created together with their conversations, messages, summaries, memory notes and gallery entries. It cannot be undone, and we cannot restore the content afterwards.",
        },
        {
          type: "p",
          text: "We want to be honest about what deletion does not cover:",
        },
        {
          type: "ul",
          items: [
            "Purchase and billing records are retained, with your account link removed, because we are required to keep them.",
            "Stored image files are removed together with the records that reference them, but we do not guarantee that every file is erased from object storage at the same instant.",
            "Copies may persist for a limited period in encrypted database backups and in operational logs before they age out in the ordinary course.",
            "Content already sent to an AI provider to generate a reply or an image is subject to that provider’s own retention. We request minimal retention where a provider supports it, but we cannot delete data from their systems on your behalf.",
            "We may retain limited records where we need them to comply with the law, prevent fraud or abuse, or resolve a dispute.",
          ],
        },
        {
          type: "p",
          text: "For these reasons we describe deletion as permanent and irreversible for you, but we do not promise instantaneous or absolute erasure everywhere.",
        },
      ],
    },
    {
      id: "16",
      heading: "16. Your rights",
      blocks: [
        {
          type: "p",
          text: "Under the Kenyan Data Protection Act, 2019 you have the right to be informed about how we use your data, to access it, to have inaccurate data corrected, to have data deleted where the law allows, to object to or restrict certain processing, and to receive your data in a portable form.",
        },
        {
          type: "p",
          text: "Two of these are built into the app: you can download your data and you can delete your account from your settings. The export is a JSON file containing your profile details, your consent records, your active memory notes, your own messages and your verification activity. It does not include AI replies, character data, images or payment records — if you need a broader copy, ask us and we will help.",
        },
        {
          type: "p",
          text: "To exercise any other right, or if you want us to act on your behalf, write to [info@uwenzai.com](mailto:info@uwenzai.com). We may need to verify your identity before we act, and we will respond within the time allowed by applicable law.",
        },
        {
          type: "p",
          text: "If you are in the European Union, the United Kingdom or Switzerland, additional rights may apply to you under the GDPR or the Swiss Federal Act on Data Protection where those laws cover our processing. We honour the rights described above for all users, and we will apply any further rights that genuinely apply to your situation. We are not suggesting that every requirement of every jurisdiction applies to the Service.",
        },
        {
          type: "p",
          text: "You may withdraw consent or close your account at any time. Withdrawing consent does not affect processing that already took place.",
        },
      ],
    },
    {
      id: "17",
      heading: "17. Security",
      blocks: [
        {
          type: "ul",
          items: [
            "Traffic between your device and our servers is encrypted in transit.",
            "Sensitive database fields, and the credentials described above, are encrypted at the application level.",
            "Images are held in a private bucket, reachable only through short-lived signed links, and encrypted at rest by our hosting provider.",
            "Passwords are stored as salted hashes, never in readable form.",
            "We use HttpOnly session cookies, CSRF protection, rate limiting, account lockout after repeated failed sign-ins, and bot protection on sensitive forms.",
            "Administrative access is limited to staff who need it.",
          ],
        },
        {
          type: "p",
          text: "No service can be completely secure, and we do not guarantee that our measures will prevent every unauthorised access, loss or disclosure. Keeping your password and device secure is your responsibility.",
        },
      ],
    },
    {
      id: "18",
      heading: "18. If there is a data breach",
      blocks: [
        {
          type: "p",
          text: "If a security incident affects your personal data, we will investigate, take steps to contain it, and notify the Office of the Data Protection Commissioner and affected users where the law requires — without undue delay and within the timeframes set by applicable law, which under the Kenyan Data Protection Act is within seventy-two hours of becoming aware of the breach. Our notice will describe what happened, the likely consequences, and what we and you can do about it.",
        },
      ],
    },
    {
      id: "19",
      heading: "19. Children",
      blocks: [
        {
          type: "p",
          text: "UwenzAi is strictly for adults. You must be at least 18 years old, and an adult where you live, to use the Service. We do not knowingly collect data from anyone under 18. If we learn that an account belongs to a minor we will close it and delete the associated data. If you believe a child is using the Service, tell us at [info@uwenzai.com](mailto:info@uwenzai.com).",
        },
      ],
    },
    {
      id: "20",
      heading: "20. Changes to this policy",
      blocks: [
        {
          type: "p",
          text: "We may update this policy as the Service develops or the law changes. When we do, we will change the “Last updated” date at the top of this page. If a change materially affects how we use your personal data, we will give you clearer notice — for example a notice in the app or an email — before it takes effect, and we will ask for consent again where the law requires it. Continuing to use the Service after a change takes effect means you accept the updated policy.",
        },
      ],
    },
    {
      id: "21",
      heading: "21. Contact and complaints",
      blocks: [
        {
          type: "p",
          text: "For any privacy question or request, contact UwenzAi Limited, Nairobi, Kenya, at [info@uwenzai.com](mailto:info@uwenzai.com).",
        },
        {
          type: "p",
          text: "If you are not satisfied with our response, you may complain to the Office of the Data Protection Commissioner in Kenya. If you are in the EU, the UK or Switzerland and those rules apply to you, you may also complain to your local supervisory authority.",
        },
        {
          type: "p",
          text: "Related documents: our [Terms of Use](/terms), our [Service Provider Disclosure](/subprocessors), and our [Responsible Use Guide](/responsible-use).",
        },
      ],
    },
  ],
  footer:
    "UwenzAi Limited – Nairobi, Kenya. Questions? [info@uwenzai.com](mailto:info@uwenzai.com)",
};
