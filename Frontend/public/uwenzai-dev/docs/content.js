/* UwenzAi-Dev API documentation — bilingual content (EN / SW).
   Structure: chrome strings, hero, nav, and per-section block arrays.
   Block types: p, h3, h4, ul, ol, callout, code, table, steps, endpoint. */

const BASE = "https://agent-api.uwenzai.com/api/v1";

const CONTENT = {
  chrome: {
    en: {
      docsTag: "Docs",
      langLabel: "Language",
      openWorkspace: "Open UwenzAi-Dev",
      sidebarFoot:
        "UwenzAi-Dev is experimental. Prices and features may change.",
      copy: "Copy",
      copied: "Copied",
      termsLink: "Terms of Use",
      privacyLink: "Privacy Policy",
      providersLink: "Service Provider Disclosure",
      authBearer: "Bearer key",
      authSession: "Login session",
      scrollHint: "Swipe or scroll the table horizontally to see all columns.",
    },
    sw: {
      docsTag: "Nyaraka",
      langLabel: "Lugha",
      openWorkspace: "Fungua UwenzAi-Dev",
      sidebarFoot:
        "UwenzAi-Dev ni ya majaribio. Bei na vipengele vinaweza kubadilika.",
      copy: "Nakili",
      copied: "Imenakiliwa",
      termsLink: "Masharti ya Matumizi",
      privacyLink: "Sera ya Faragha",
      providersLink: "Taarifa ya Watoa Huduma",
      authBearer: "Ufunguo wa Bearer",
      authSession: "Kipindi cha kuingia",
      scrollHint: "Sogeza meza kando kuona safu zote.",
    },
  },

  hero: {
    en: {
      eyebrow: "UwenzAi-Dev · API",
      title: "Build with AI. Metered in Sparks.",
      sub: "UwenzAi-Dev is an OpenAI-compatible API gateway: one API key, many models from several providers, metered in Sparks, UwenzAi's API usage units for East African builders.",
      cta: "Read the quickstart",
      secondary: "Open the workspace",
      facts: [
        { icon: "bolt", text: "OpenAI-compatible" },
        { icon: "globe", text: "English & Kiswahili" },
        { icon: "gauge", text: "Metered in Sparks" },
      ],
    },
    sw: {
      eyebrow: "UwenzAi-Dev · API",
      title: "Jenga kwa AI. Inapimwa kwa Sparks.",
      sub: "UwenzAi-Dev ni lango la API linaloendana na OpenAI: ufunguo mmoja wa API, modeli nyingi kutoka kwa watoa huduma kadhaa, inapimwa kwa Sparks, vitengo vya matumizi ya API vya UwenzAi kwa wajenzi wa Afrika Mashariki.",
      cta: "Soma mwanzo wa haraka",
      secondary: "Fungua eneo la kazi",
      facts: [
        { icon: "bolt", text: "Inaoana na OpenAI" },
        { icon: "globe", text: "Kiswahili na Kiingereza" },
        { icon: "gauge", text: "Inapimwa kwa Sparks" },
      ],
    },
  },

  nav: [
    { group: "start", id: "overview", en: "Overview", sw: "Muhtasari" },
    { group: "start", id: "quickstart", en: "Quickstart", sw: "Anza haraka" },
    {
      group: "ref",
      id: "authentication",
      en: "Authentication",
      sw: "Uthibitisho",
    },
    { group: "ref", id: "base-urls", en: "Base URLs", sw: "Anwani za msingi" },
    {
      group: "ref",
      id: "inference",
      en: "Inference endpoints",
      sw: "Huduma za inference",
    },
    { group: "ref", id: "keys", en: "API keys", sw: "Funguo za API" },
    { group: "ref", id: "usage", en: "Usage", sw: "Matumizi" },
    { group: "ref", id: "consent", en: "Consent", sw: "Idhini" },
    { group: "ref", id: "pricing", en: "Pricing catalog", sw: "Orodha ya bei" },
    {
      group: "ref",
      id: "playground",
      en: "Playground",
      sw: "Eneo la majaribio",
    },
    { group: "pol", id: "errors", en: "Errors", sw: "Makosa" },
    {
      group: "pol",
      id: "limits",
      en: "Limits & budgets",
      sw: "Vikwazo na bajeti",
    },
    {
      group: "pol",
      id: "billing",
      en: "Billing & Sparks",
      sw: "Malipo na Sparks",
    },
    {
      group: "pol",
      id: "providers",
      en: "Providers & data regions",
      sw: "Watoa huduma na maeneo ya data",
    },
  ],

  groupLabels: {
    en: {
      start: "Getting started",
      ref: "API reference",
      pol: "Policies & limits",
    },
    sw: { start: "Mwanzo", ref: "Mwongozo wa API", pol: "Sera na vikwazo" },
  },

  foot: {
    en: {
      text: "UwenzAi-Dev is an experimental developer service of UwenzAi. Availability and latency are not guaranteed. This documentation is AI-generated and may contain errors.",
    },
    sw: {
      text: "UwenzAi-Dev ni huduma ya majaribio ya wasanidi programu kutoka UwenzAi. Upatikanaji na kasi hazihakikiwi. Nyaraka hizi zimetengenezwa na AI na zinaweza kuwa na makosa.",
    },
  },

  sections: {
    /* ── Overview ─────────────────────────────────────── */
    overview: {
      en: [
        {
          t: "callout",
          kind: "info",
          x: "**AI-generated documentation.** These pages were produced with AI assistance and may contain errors or outdated details. The API's actual responses and the UwenzAi-Dev workspace are authoritative — verify anything critical against them.",
        },
        {
          t: "p",
          x: "UwenzAi-Dev exposes one **OpenAI-compatible HTTP API** for chatting with models from several inference providers. Point any OpenAI SDK or HTTP client at the gateway base URL, send your UwenzAi key as a Bearer token, and choose a model from the catalog.",
        },
        {
          t: "p",
          x: "Every request is metered in **Sparks**, UwenzAi's usage units for API calls — like megabytes on a data bundle. Sparks are an internal allowance: you top them up with mobile money or card inside the UwenzAi app, and each API key can carry its own monthly Sparks budget.",
        },
        {
          t: "ul",
          items: [
            "`GET /models` and `POST /chat/completions` work like the OpenAI API you already know.",
            "Model IDs come from several providers — pass `provider` when a model name exists on more than one.",
            "Usage, keys, pricing and consent are managed from the UwenzAi-Dev workspace or its session endpoints.",
            "The provider label on each model tells you where its inference runs.",
          ],
        },
        {
          t: "callout",
          kind: "warn",
          x: "**Experimental.** API features, models and prices may change. Availability and latency are not guaranteed, and there is no developer API SLA.",
        },
      ],
      sw: [
        {
          t: "callout",
          kind: "info",
          x: "**Nyaraka zimetengenezwa na AI.** Kurasa hizi zilitayarishwa kwa msaada wa AI na zinaweza kuwa na makosa au maelezo yaliyopitwa na wakati. Majibu halisi ya API na eneo la kazi la UwenzAi-Dev ndiyo yenye mamlaka — thibitisha mambo muhimu kulingana navyo.",
        },
        {
          t: "p",
          x: "UwenzAi-Dev inatoa **API moja ya HTTP inayoendana na OpenAI** ya kuzungumza na modeli kutoka kwa watoa huduma wengi wa inference. Elekeza SDK yoyote ya OpenAI au mteja wa HTTP kwenye anwani ya msingi ya lango, tuma ufunguo wako wa UwenzAi kama Bearer token, na chagua modeli kutoka orodha.",
        },
        {
          t: "p",
          x: "Kila ombi hunapimwa kwa **Sparks**, vitengo vya matumizi ya UwenzAi kwa miito ya API — kama megabaiti kwenye furushi la data. Sparks ni kiasi cha ndani: unajaza kwa pesa ya mkononi au kadi ndani ya programu ya UwenzAi, na kila ufunguo wa API unaweza kuwa na bajeti yake ya Sparks kwa mwezi.",
        },
        {
          t: "ul",
          items: [
            "`GET /models` na `POST /chat/completions` zinafanya kazi kama API ya OpenAI unayoifahamu.",
            "Vitambulisho vya modeli vinatoka kwa watoa huduma wengi — tuma `provider` jina la modeli likiwapo kwa zaidi ya mmoja.",
            "Matumizi, funguo, bei na idhini husimamiwa kutoka eneo la kazi la UwenzAi-Dev au huduma zake za kipindi.",
            "Lebo ya mtoa huduma kwenye kila modeli inakuambia inference yake inaendeshwa wapi.",
          ],
        },
        {
          t: "callout",
          kind: "warn",
          x: "**Ya majaribio.** Vipengele vya API, modeli na bei zinaweza kubadilika. Upatikanaji na kasi hazihakikiwi, na hakuna SLA ya API ya wasanidi.",
        },
      ],
    },

    /* ── Quickstart ───────────────────────────────────── */
    quickstart: {
      en: [
        {
          t: "steps",
          steps: [
            {
              title: "Create an API key",
              body: [
                {
                  t: "p",
                  x: "Open the UwenzAi-Dev workspace, accept the developer terms once, and create a key. Give it a label and, optionally, a monthly Sparks budget. The full key is shown **exactly once** — copy it and store it safely.",
                },
              ],
            },
            {
              title: "Set your environment",
              body: [
                {
                  t: "code",
                  title: "shell",
                  x: 'export UWENZAI_KEY="uwenzai-dev-your-key-here"',
                },
                {
                  t: "p",
                  x: "Never put the key in client-side code or commit it to a repository.",
                },
              ],
            },
            {
              title: "Make your first request",
              body: [
                {
                  t: "p",
                  x: "Use the OpenAI Python SDK with `base_url` pointed at the gateway. The `provider` field is optional — it is only needed when the same model name exists on more than one provider.",
                },
                {
                  t: "code",
                  title: "python",
                  x: `import os\nfrom openai import OpenAI\n\nclient = OpenAI(\n    api_key=os.environ["UWENZAI_KEY"],\n    base_url="${BASE}",\n)\n\nresponse = client.chat.completions.create(\n    model="fikra-pro-20b",\n    extra_body={"provider": "fikra"},\n    messages=[{"role": "user", "content": "Hello!"}],\n    max_tokens=1024,\n)\nprint(response.choices[0].message.content)`,
                },
                { t: "p", x: "Prefer plain HTTP? The same request with cURL:" },
                {
                  t: "code",
                  title: "curl",
                  x: `curl "${BASE}/chat/completions/" \\\n  -H "Authorization: Bearer $UWENZAI_KEY" \\\n  -H "Content-Type: application/json" \\\n  --data-binary @- <<'UWENZAI_REQUEST'\n{\n  "model": "fikra-pro-20b",\n  "provider": "fikra",\n  "messages": [{"role": "user", "content": "Hello!"}],\n  "max_tokens": 1024\n}\nUWENZAI_REQUEST`,
                },
              ],
            },
            {
              title: "Watch your usage",
              body: [
                {
                  t: "p",
                  x: "The response includes an `_uwenzai` block with the exact Sparks charged and token counts. The workspace Usage tab and `GET /api/v1/agent/usage` show the same numbers per key.",
                },
              ],
            },
          ],
        },
      ],
      sw: [
        {
          t: "steps",
          steps: [
            {
              title: "Unda ufunguo wa API",
              body: [
                {
                  t: "p",
                  x: "Fungua eneo la kazi la UwenzAi-Dev, kukubali masharti ya wasanidi mara moja, na uunde ufunguo. Peana jina na, kwa hiari, bajeti ya Sparks ya mwezi. Ufunguo kamili unaonyeshwa **mara moja tu** — nakili na uuhifadhi kwa usalama.",
                },
              ],
            },
            {
              title: "Weka mazingira yako",
              body: [
                {
                  t: "code",
                  title: "shell",
                  x: 'export UWENZAI_KEY="uwenzai-dev-ufunguo-wako-hapa"',
                },
                {
                  t: "p",
                  x: "Usiweke ufunguo katika msimbo unaotumika kivinjararini wala kuutuma kwenye hazina ya msimbo.",
                },
              ],
            },
            {
              title: "Tuma ombi lako la kwanza",
              body: [
                {
                  t: "p",
                  x: "Tumia SDK ya OpenAI ya Python ukielekeza `base_url` kwenye lango. Uga wa `provider` ni wa hiari — unahitajika tu jina la modeli likiwapo kwa watoa huduma zaidi ya mmoja.",
                },
                {
                  t: "code",
                  title: "python",
                  x: `import os\nfrom openai import OpenAI\n\nclient = OpenAI(\n    api_key=os.environ["UWENZAI_KEY"],\n    base_url="${BASE}",\n)\n\nresponse = client.chat.completions.create(\n    model="fikra-pro-20b",\n    extra_body={"provider": "fikra"},\n    messages=[{"role": "user", "content": "Habari!"}],\n    max_tokens=1024,\n)\nprint(response.choices[0].message.content)`,
                },
                {
                  t: "p",
                  x: "Unapendelea HTTP tupu? Ombi hilo hilo kwa cURL:",
                },
                {
                  t: "code",
                  title: "curl",
                  x: `curl "${BASE}/chat/completions/" \\\n  -H "Authorization: Bearer $UWENZAI_KEY" \\\n  -H "Content-Type: application/json" \\\n  --data-binary @- <<'UWENZAI_REQUEST'\n{\n  "model": "fikra-pro-20b",\n  "provider": "fikra",\n  "messages": [{"role": "user", "content": "Habari!"}],\n  "max_tokens": 1024\n}\nUWENZAI_REQUEST`,
                },
              ],
            },
            {
              title: "Fuatilia matumizi yako",
              body: [
                {
                  t: "p",
                  x: "Jibu lina sehemu ya `_uwenzai` yenye idadi kamili ya Sparks zilizolipwa na tokeni. Kichupo cha Matumizi eneo la kazi na `GET /api/v1/agent/usage` zinaonyesha nambari zile zile kwa kila ufunguo.",
                },
              ],
            },
          ],
        },
      ],
    },

    /* ── Authentication ───────────────────────────────── */
    authentication: {
      en: [
        {
          t: "p",
          x: "Inference endpoints authenticate with your UwenzAi API key as a Bearer token. Keys start with `uwenzai-dev-` and are created in the UwenzAi-Dev workspace.",
        },
        {
          t: "code",
          title: "http",
          x: `Authorization: Bearer uwenzai-dev-...`,
        },
        {
          t: "ul",
          items: [
            "The full key is returned **once** at creation. Store it in a secret manager or environment variable.",
            "Keys can have an expiry date and an optional monthly Sparks budget (1–1,000,000).",
            "A revoked or expired key stops working immediately for inference, but its usage history remains.",
            "Failed authentication returns `401` with `invalid_api_key`.",
          ],
        },
        {
          t: "p",
          x: "Management endpoints (keys, usage, consent, pricing, playground) use your **UwenzAi login session** — call them from the app or an authenticated browser session, not with the Bearer key.",
        },
        {
          t: "callout",
          kind: "warn",
          x: "**Security.** Never embed a key in client-side code, URLs, or logs. Rotate keys by creating a new one and revoking the old. Provider endpoints and credentials cannot be overridden through the API — requests containing `base_url`, `endpoint`, `endpoint_url`, `api_url` or `api_key` fields are rejected.",
        },
      ],
      sw: [
        {
          t: "p",
          x: "Huduma za inference zinathibitisha kwa ufunguo wako wa API wa UwenzAi kama Bearer token. Funguo huanza na `uwenzai-dev-` na zinaundwa katika eneo la kazi la UwenzAi-Dev.",
        },
        {
          t: "code",
          title: "http",
          x: `Authorization: Bearer uwenzai-dev-...`,
        },
        {
          t: "ul",
          items: [
            "Ufunguo kamili hurudishwa **mara moja** tu unapoundwa. Uhifadhi kwenye kidhibiti cha siri au utofauti wa mazingira.",
            "Funguo zinaweza kuwa na tarehe ya kumalizika muda na bajeti ya hiari ya Sparks kwa mwezi (1–1,000,000).",
            "Ufunguo uliobatilishwa au ulioisha muda huacha kutumika mara moja kwa inference, lakini historia ya matumizi yake inabaki.",
            "Uthibitisho usiofaulu hurudisha `401` pamoja na `invalid_api_key`.",
          ],
        },
        {
          t: "p",
          x: "Huduma za usimamizi (funguo, matumizi, idhini, bei, eneo la majaribio) hutumia **kipindi chako cha kuingia** cha UwenzAi — ziite kutoka programu au kipindi cha kivinjari kilichothibitishwa, si kwa ufunguo wa Bearer.",
        },
        {
          t: "callout",
          kind: "warn",
          x: "**Usalama.** Usifunge ufunguo kwenye msimbo wa kivinjari, URL, au kumbukumbu. Badilisha funguo kwa kuunda mpya na kubatilisha wa zamani. Anwani na vitambulisho vya watoa huduma haviwezi kubadilishwa kupitia API — maombi yenye uga wa `base_url`, `endpoint`, `endpoint_url`, `api_url` au `api_key` yanakataliwa.",
        },
      ],
    },

    /* ── Base URLs ────────────────────────────────────── */
    "base-urls": {
      en: [
        {
          t: "p",
          x: "The gateway runs as a dedicated inference service. The base URL is shown in the workspace **Connect** tab — always copy it from there for the exact deployment you use.",
        },
        {
          t: "table",
          head: ["Base URL", "Service", "Notes"],
          rows: [
            [
              "`https://agent-api.uwenzai.com/api/v1`",
              "Dedicated Agent service",
              "Canonical inference base.",
            ],
            [
              "`https://…/api/v1/agent`",
              "Main app",
              "Legacy alias for every gateway endpoint; works on both services.",
            ],
          ],
        },
        {
          t: "ul",
          items: [
            "`GET /models` and `POST /chat/completions` live directly under the base URL.",
            "Management endpoints live under `…/agent/` on the main app (keys, usage, consent, pricing, playground).",
            "Both spellings — with and without the trailing slash — dispatch directly, so POST bodies never depend on redirects.",
          ],
        },
        {
          t: "callout",
          kind: "info",
          x: "**Pass `provider` explicitly** when the model name exists on more than one provider. The workspace code snippets always include it.",
        },
      ],
      sw: [
        {
          t: "p",
          x: "Lango linaendeshwa kama huduma maalum ya inference. Anwani ya msingi inaonyeshwa kwenye kichupo cha **Unganisha** eneo la kazi — nakili kutoka hapo kila wakati kwa uteuzi unaotumia.",
        },
        {
          t: "table",
          head: ["Anwani ya msingi", "Huduma", "Maelezo"],
          rows: [
            [
              "`https://agent-api.uwenzai.com/api/v1`",
              "Huduma maalum ya Agent",
              "Anwani rasmi ya inference.",
            ],
            [
              "`https://…/api/v1/agent`",
              "Programu kuu",
              "Jina la zamani la kila huduma ya lango; linafanya kazi kwenye huduma zote mbili.",
            ],
          ],
        },
        {
          t: "ul",
          items: [
            "`GET /models` na `POST /chat/completions` ziko moja kwa moja chini ya anwani ya msingi.",
            "Huduma za usimamizi ziko chini ya `…/agent/` kwenye programu kuu (funguo, matumizi, idhini, bei, eneo la majaribio).",
            "Maandishi yote mawili — yenye na isiyo na mkia wa mfululizo — hupitishwa moja kwa moja, kwa hiyo mwili wa POST hauitegemei mwelekeo upya.",
          ],
        },
        {
          t: "callout",
          kind: "info",
          x: "**Tuma `provider` wazi** jina la modeli likiwapo kwa watoa huduma zaidi ya mmoja. Vipande vya msimbo eneo la kazi vinavijumuisha kila wakati.",
        },
      ],
    },

    /* ── Inference endpoints ──────────────────────────── */
    inference: {
      en: [
        {
          t: "p",
          x: "The two endpoints your app calls. Both are OpenAI-compatible, authenticated with the Bearer key, and rate-limited per key (60 requests/minute).",
        },
        {
          t: "endpoint",
          method: "GET",
          path: "/models",
          auth: "bearer",
          desc: "List the models currently available on the gateway. OpenAI-compatible shape.",
          response: `{
  "object": "list",
  "data": [
    { "id": "fikra-pro-20b", "object": "model", "created": 0, "owned_by": "uwenzai" },
    { "id": "mistral-small-latest", "object": "model", "created": 0, "owned_by": "uwenzai" }
  ]
}`,
        },
        {
          t: "endpoint",
          method: "POST",
          path: "/chat/completions",
          auth: "bearer",
          desc: "Create a chat completion. `stream: true` returns an OpenAI-shaped SSE stream (the full reply arrives as chunk events once complete). Metered in Sparks per token.",
          params: [
            [
              "model",
              "string",
              "optional",
              "Model ID from `/models`. Uses the default model when omitted.",
            ],
            [
              "provider",
              "string",
              "optional",
              "Provider ID (e.g. `fikra`). Disambiguates models that exist on several providers.",
            ],
            [
              "messages",
              "array",
              "required",
              'OpenAI-style messages: `{"role": "user", "content": "Hello!"}`.',
            ],
            [
              "max_tokens",
              "integer",
              "optional",
              "Maximum tokens to generate.",
            ],
            [
              "stream",
              "boolean",
              "optional",
              "`true` returns an SSE stream: role chunk, content chunk(s), a final chunk with `finish_reason` and `usage`, then `data: [DONE]`. The reply is generated in full before the stream opens, so billing and idempotency behave exactly like the non-streaming path.",
            ],
            ["temperature", "number", "optional", "0.0–1.0."],
            ["top_p", "number", "optional", "Top-p sampling."],
            ["stop", "string / array", "optional", "Stop sequences."],
            ["presence_penalty", "number", "optional", "Presence penalty."],
            ["frequency_penalty", "number", "optional", "Frequency penalty."],
            [
              "user",
              "string",
              "optional",
              "End-user identifier for abuse tracking.",
            ],
            [
              "tools",
              "array",
              "optional",
              'OpenAI-style function tool definitions (max 128, function type only). The model may answer with `tool_calls`; you execute the tools and send the results back as `role: "tool"` messages.',
            ],
            [
              "tool_choice",
              "string / object",
              "optional",
              '`"auto"`, `"none"`, `"required"` or `{ "type": "function", "function": { "name": ... } }`.',
            ],
            [
              "client_operation_id",
              "string",
              "optional",
              "Idempotency key (1–300 chars). Replaying it returns the original result without a second charge.",
            ],
          ],
          request: `{
  "model": "fikra-pro-20b",
  "provider": "fikra",
  "messages": [
    { "role": "user", "content": "Hello!" }
  ],
  "max_tokens": 1024,
  "client_operation_id": "my-first-request"
}`,
          response: `{
  "id": "uwenzai-fikra-pro-20b",
  "object": "chat.completion",
  "created": 1760000000,
  "model": "fikra-pro-20b",
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "Habari! Naweza kukusaidiaje leo?" },
      "finish_reason": "stop"
    }
  ],
  "usage": { "prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21 },
  "_uwenzai": {
    "sparks_charged": 0.5,
    "actual_sparks": 0.5,
    "input_tokens": 12,
    "output_tokens": 9,
    "model": "fikra-pro-20b",
    "provider": "fikra"
  }
}`,
        },
        {
          t: "callout",
          kind: "info",
          x: "The `_uwenzai` block is added by the gateway: it reports the exact Sparks charged for this request and the token counts used for billing. Displayed prices are rounded; billing uses server rates.",
        },
      ],
      sw: [
        {
          t: "p",
          x: "Huduma mbili programu yako inaita. Zote mbili zinaoana na OpenAI, zinathibitishwa kwa ufunguo wa Bearer, na zina kikomo cha maombi kwa kila ufunguo (60 kwa dakika).",
        },
        {
          t: "endpoint",
          method: "GET",
          path: "/models",
          auth: "bearer",
          desc: "Orodha ya modeli zinazopatikana kwenye lango kwa sasa. Umbo linaloendana na OpenAI.",
          response: `{
  "object": "list",
  "data": [
    { "id": "fikra-pro-20b", "object": "model", "created": 0, "owned_by": "uwenzai" },
    { "id": "mistral-small-latest", "object": "model", "created": 0, "owned_by": "uwenzai" }
  ]
}`,
        },
        {
          t: "endpoint",
          method: "POST",
          path: "/chat/completions",
          auth: "bearer",
          desc: "Unda chat completion. `stream: true` inarudisha mtiririko wa SSE unaofanana na OpenAI (jibu zima huwasili kama vipande vikamilipo). Inapimwa kwa Sparks kwa kila tokeni.",
          params: [
            [
              "model",
              "string",
              "hiari",
              "Kitambulisho cha modeli kutoka `/models`. Hutumia modeli ya chaguo-msingi kama hakipo.",
            ],
            [
              "provider",
              "string",
              "hiari",
              "Kitambulisho cha mtoa huduma (mf. `fikra`). Kinatofautisha modeli zinazopo kwa watoa huduma wengi.",
            ],
            [
              "messages",
              "array",
              "lazima",
              'Ujumbe wa mtindo wa OpenAI: `{"role": "user", "content": "Habari!"}`.',
            ],
            [
              "max_tokens",
              "integer",
              "hiari",
              "Kiwango cha juu cha tokeni za kutengeneza.",
            ],
            [
              "stream",
              "boolean",
              "hiari",
              "`true` inarudisha mtiririko wa SSE: kipande cha jukumu, kipande cha maudhui, kipande cha mwisho chenye `finish_reason` na `usage`, kisha `data: [DONE]`. Jibu hutengenezwa kikamilifu kabla ya mtiririko kufunguka, kwa hiyo malipo na idempotency hufanya kazi kama njia isiyo ya mtiririko.",
            ],
            ["temperature", "number", "hiari", "0.0–1.0."],
            ["top_p", "number", "hiari", "Uchaguzi wa top-p."],
            ["stop", "string / array", "hiari", "Alama za kusimamisha."],
            ["presence_penalty", "number", "hiari", "Adhabu ya uwepo."],
            ["frequency_penalty", "number", "hiari", "Adhabu ya marudio."],
            [
              "user",
              "string",
              "hiari",
              "Kitambulisho cha mtumiaji wa mwisho cha kufuatilia matumizi mabaya.",
            ],
            [
              "tools",
              "array",
              "hiari",
              'Ufafanuzi wa zana za mtindo wa OpenAI (hadi 128, aina ya function pekee). Modeli inaweza kujibu na `tool_calls`; wewe unatekeleza zana na kutuma matokeo kama ujumbe wa `role: "tool"`.',
            ],
            [
              "tool_choice",
              "string / object",
              "hiari",
              '`"auto"`, `"none"`, `"required"` au `{ "type": "function", "function": { "name": ... } }`.',
            ],
            [
              "client_operation_id",
              "string",
              "hiari",
              "Ufunguo wa idempotency (herufi 1–300). Kukirudia hurudisha jibu la awali bila malipo ya pili.",
            ],
          ],
          request: `{
  "model": "fikra-pro-20b",
  "provider": "fikra",
  "messages": [
    { "role": "user", "content": "Habari!" }
  ],
  "max_tokens": 1024,
  "client_operation_id": "ombi-langu-la-kwanza"
}`,
          response: `{
  "id": "uwenzai-fikra-pro-20b",
  "object": "chat.completion",
  "created": 1760000000,
  "model": "fikra-pro-20b",
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "Habari! Naweza kukusaidiaje leo?" },
      "finish_reason": "stop"
    }
  ],
  "usage": { "prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21 },
  "_uwenzai": {
    "sparks_charged": 0.5,
    "actual_sparks": 0.5,
    "input_tokens": 12,
    "output_tokens": 9,
    "model": "fikra-pro-20b",
    "provider": "fikra"
  }
}`,
        },
        {
          t: "callout",
          kind: "info",
          x: "Sehemu ya `_uwenzai` inaongezwa na lango: inaripoti Sparks kamili zilizolipwa kwa ombi hili na idadi ya tokeni zilizotumika kwa malipo. Bei zinazoonyeshwa zimekaririwa; malipo yanatumia viwango vya seva.",
        },
      ],
    },

    /* ── Keys ─────────────────────────────────────────── */
    keys: {
      en: [
        {
          t: "p",
          x: "Create, list and revoke API keys. These endpoints use your login session — in practice you use the workspace UI, which calls them for you.",
        },
        {
          t: "endpoint",
          method: "GET",
          path: "/api/v1/agent/keys/",
          auth: "session",
          desc: "List all your keys with their status. The secret itself is never shown again.",
          response: `{
  "status": "ok",
  "data": {
    "keys": [
      {
        "id": "6f1c2d34-…",
        "label": "My test app",
        "hint": "1b48…",
        "is_active": true,
        "is_expired": false,
        "created_at": "2026-09-18T10:12:00Z",
        "last_used_at": "2026-09-18T14:03:11Z",
        "expires_at": null,
        "monthly_spark_budget": 5000
      }
    ]
  }
}`,
        },
        {
          t: "endpoint",
          method: "POST",
          path: "/api/v1/agent/keys/",
          auth: "session",
          desc: "Create a new key. Requires the developer terms to be accepted first (403 otherwise). The full key is returned exactly once.",
          params: [
            [
              "label",
              "string",
              "optional",
              "A name for the key, up to 100 characters.",
            ],
            [
              "expires_at",
              "string",
              "optional",
              "Future ISO timestamp with timezone, e.g. `2027-01-01T00:00:00Z`.",
            ],
            [
              "monthly_spark_budget",
              "integer",
              "optional",
              "Per-key monthly cap in Sparks, 1–1,000,000. Omit for unlimited.",
            ],
          ],
          request: `{
  "label": "My production app",
  "monthly_spark_budget": 5000
}`,
          response: `{
  "status": "ok",
  "data": {
    "full_key": "uwenzai-dev-…",
    "key_id": "6f1c2d34-…",
    "label": "My production app",
    "hint": "k41y…",
    "created_at": "2026-09-18T15:20:00Z",
    "is_active": true,
    "monthly_spark_budget": 5000
  },
  "warning": "This is the only time the full key will be shown. Store it securely."
}`,
        },
        {
          t: "endpoint",
          method: "DELETE",
          path: "/api/v1/agent/keys/{id}/",
          auth: "session",
          desc: "Revoke one of your keys by its ID. Connected apps lose access immediately. This cannot be undone — create a new key instead if you change your mind. Revoked keys are removed from the list automatically after a retention period; their usage history remains in your usage reports.",
          response: `{
  "status": "ok",
  "data": { "message": "API key revoked", "key_id": "6f1c2d34-…" }
}`,
        },
        {
          t: "endpoint",
          method: "PATCH",
          path: "/api/v1/agent/keys/{id}/budget/",
          auth: "session",
          desc: "Set or change the monthly Sparks spending limit of an active key. The workspace keys panel calls this for you; the field is required — pass an integer to set a limit, or null to remove it. A budget below this month's usage simply blocks further calls with the key until next month. Revoked keys cannot be edited.",
          params: [
            [
              "monthly_spark_budget",
              "integer | null",
              "required",
              "Per-key monthly cap in Sparks, 1–1,000,000, or null for unlimited.",
            ],
          ],
          request: `{ "monthly_spark_budget": 5000 }`,
          response: `{
  "status": "ok",
  "data": { "key_id": "6f1c2d34-…", "monthly_spark_budget": 5000 }
}`,
        },
      ],
      sw: [
        {
          t: "p",
          x: "Unda, orodhesha na batilisha funguo za API. Huduma hizi zinatumia kipindi chako cha kuingia — kwa vitendo unatumia kiolesura cha eneo la kazi, ambacho kinaizituma kwa niaba yako.",
        },
        {
          t: "endpoint",
          method: "GET",
          path: "/api/v1/agent/keys/",
          auth: "session",
          desc: "Orodhesha funguo zako zote na hali zake. Siri yenyewe haionyeshwi tena kamwe.",
          response: `{
  "status": "ok",
  "data": {
    "keys": [
      {
        "id": "6f1c2d34-…",
        "label": "Programu yangu ya majaribio",
        "hint": "1b48…",
        "is_active": true,
        "is_expired": false,
        "created_at": "2026-09-18T10:12:00Z",
        "last_used_at": "2026-09-18T14:03:11Z",
        "expires_at": null,
        "monthly_spark_budget": 5000
      }
    ]
  }
}`,
        },
        {
          t: "endpoint",
          method: "POST",
          path: "/api/v1/agent/keys/",
          auth: "session",
          desc: "Unda ufunguo mpya. Inahitaji kukubaliwa kwa masharti ya wasanidi kwanza (403 vinginevyo). Ufunguo kamili hurudishwa mara moja tu.",
          params: [
            ["label", "string", "hiari", "Jina la ufunguo, hadi herufi 100."],
            [
              "expires_at",
              "string",
              "hiari",
              "Wakati ujao wa ISO wenye saa za eneo, mf. `2027-01-01T00:00:00Z`.",
            ],
            [
              "monthly_spark_budget",
              "integer",
              "hiari",
              "Kikomo cha mwezi kwa kila ufunguo kwa Sparks, 1–1,000,000. Iache kwa ukomo usio na mpaka.",
            ],
          ],
          request: `{
  "label": "Programu yangu ya uzalishaji",
  "monthly_spark_budget": 5000
}`,
          response: `{
  "status": "ok",
  "data": {
    "full_key": "uwenzai-dev-…",
    "key_id": "6f1c2d34-…",
    "label": "Programu yangu ya uzalishaji",
    "hint": "k41y…",
    "created_at": "2026-09-18T15:20:00Z",
    "is_active": true,
    "monthly_spark_budget": 5000
  },
  "warning": "Hii ni mara pekee ufunguo kamili utakaonyeshwa. Uhifadhi salama."
}`,
        },
        {
          t: "endpoint",
          method: "DELETE",
          path: "/api/v1/agent/keys/{id}/",
          auth: "session",
          desc: "Batilisha moja ya funguo zako kwa kitambulisho chake. Programu zilizounganishwa zinapoteza ufikiaji mara moja. Hii haiwezi kutenduliwa — unda ufunguo mpya kama ubadilisha maoni. Funguo zilizobatilishwa hufutwa kwenye orodha kiotomatiki baada ya kipindi cha uhifadhi; historia yao ya matumizi inabaki kwenye ripoti zako za matumizi.",
          response: `{
  "status": "ok",
  "data": { "message": "Ufunguo wa API umebatilishwa", "key_id": "6f1c2d34-…" }
}`,
        },
        {
          t: "endpoint",
          method: "PATCH",
          path: "/api/v1/agent/keys/{id}/budget/",
          auth: "session",
          desc: "Weka au badilisha kikomo cha matumizi ya Sparks ya mwezi kwa ufunguo unaotumika. Paneleo la funguo la eneo la kazi linaloituma kwa niaba yako; sehemu hii lazima iwepo — weka namba kamili kuweka kikomo, au null kuiondoa. Bajeti iliyo chini ya matumizi ya mwezi huu inazuia miito mingine kwa ufunguo huo hadi mwezi ujao. Funguo zilizobatilishwa haziwezi kurekebishwa.",
          params: [
            [
              "monthly_spark_budget",
              "integer | null",
              "lazima",
              "Kikomo cha mwezi kwa kila ufunguo kwa Sparks, 1–1,000,000, au null kwa ukomo usio na mpaka.",
            ],
          ],
          request: `{ "monthly_spark_budget": 5000 }`,
          response: `{
  "status": "ok",
  "data": { "key_id": "6f1c2d34-…", "monthly_spark_budget": 5000 }
}`,
        },
      ],
    },

    /* ── Usage ────────────────────────────────────────── */
    usage: {
      en: [
        {
          t: "p",
          x: "One call returns your totals, per-key breakdown, per-model usage, the most recent operations, and the configured base URL.",
        },
        {
          t: "endpoint",
          method: "GET",
          path: "/api/v1/agent/usage/",
          auth: "session",
          desc: "Usage summary for your account across all keys.",
          response: `{
  "status": "ok",
  "data": {
    "usage": {
      "total_sparks": 412.5,
      "total_input_tokens": 81234,
      "total_output_tokens": 15230,
      "total_calls": 143,
      "by_status": [ { "status": "success", "count": 140, "sparks": 402.5 } ],
      "recent": [ "…" ]
    },
    "keys_usage": [
      { "key_id": "…", "label": "My test app", "hint": "1b48…", "is_active": true, "total_sparks": 412.5 }
    ],
    "by_model": [ "…" ],
    "base_url": "https://agent-api.uwenzai.com/api/v1"
  }
}`,
        },
        {
          t: "p",
          x: "The workspace **Usage** tab renders exactly this data, including Sparks per model and the Sparks remaining on your account.",
        },
      ],
      sw: [
        {
          t: "p",
          x: "Wito mmoja hurudisha jumla zako, maelezo kwa kila ufunguo, matumizi kwa kila modeli, shughuli za hivi karibuni, na anwani ya msingi iliyowekwa.",
        },
        {
          t: "endpoint",
          method: "GET",
          path: "/api/v1/agent/usage/",
          auth: "session",
          desc: "Muhtasari wa matumizi ya akaunti yako kwa funguo zote.",
          response: `{
  "status": "ok",
  "data": {
    "usage": {
      "total_sparks": 412.5,
      "total_input_tokens": 81234,
      "total_output_tokens": 15230,
      "total_calls": 143,
      "by_status": [ { "status": "success", "count": 140, "sparks": 402.5 } ],
      "recent": [ "…" ]
    },
    "keys_usage": [
      { "key_id": "…", "label": "Programu yangu ya majaribio", "hint": "1b48…", "is_active": true, "total_sparks": 412.5 }
    ],
    "by_model": [ "…" ],
    "base_url": "https://agent-api.uwenzai.com/api/v1"
  }
}`,
        },
        {
          t: "p",
          x: "Kichupo cha **Matumizi** eneo la kazi kinaonyesha data hii hiyo, ikiwemo Sparks kwa kila modeli na Sparks zilizobaki kwenye akaunti yako.",
        },
      ],
    },

    /* ── Consent ──────────────────────────────────────── */
    consent: {
      en: [
        {
          t: "p",
          x: "The developer terms must be accepted once before keys can be created or the playground used. Consent is append-only and versioned — historical acceptances are never relabelled.",
        },
        {
          t: "endpoint",
          method: "GET",
          path: "/api/v1/agent/consent/",
          auth: "session",
          desc: "Check the current consent state, the active terms version, and Spark pricing parameters.",
          response: `{
  "status": "ok",
  "data": {
    "confirmed": true,
    "version": "2026-09-12",
    "spark_value_kes": 0.1,
    "fx_eur_kes": 145.2,
    "markup_percent": 5.0
  }
}`,
        },
        {
          t: "endpoint",
          method: "POST",
          path: "/api/v1/agent/consent/",
          auth: "session",
          desc: "Record consent. Both fields are required; a stale version is rejected with 409 so you always review the current terms.",
          params: [
            ["accepted", "boolean", "required", "Must be exactly `true`."],
            [
              "version",
              "string",
              "required",
              "The exact version string returned by GET.",
            ],
          ],
          request: `{
  "accepted": true,
  "version": "2026-09-12"
}`,
          response: `{
  "status": "ok",
  "data": {
    "confirmed": true,
    "version": "2026-09-12",
    "accepted_at": "2026-09-18T14:00:00Z"
  }
}`,
        },
      ],
      sw: [
        {
          t: "p",
          x: "Masharti ya wasanidi yanapaswa kukubaliwa mara moja kabla funguo kuundwa au eneo la majaribio kutumika. Idhini huongezwa tu na ina toleo — idhini za kihistoria hazitajapwa upya wala kubadilishwa.",
        },
        {
          t: "endpoint",
          method: "GET",
          path: "/api/v1/agent/consent/",
          auth: "session",
          desc: "Kagua hali ya idhini ya sasa, toleo la masharti halali, na vigezo vya bei za Sparks.",
          response: `{
  "status": "ok",
  "data": {
    "confirmed": true,
    "version": "2026-09-12",
    "spark_value_kes": 0.1,
    "fx_eur_kes": 145.2,
    "markup_percent": 5.0
  }
}`,
        },
        {
          t: "endpoint",
          method: "POST",
          path: "/api/v1/agent/consent/",
          auth: "session",
          desc: "Hifadhi idhini. Uga wote mbili ni lazima; toleo la zamani linakataliwa kwa 409 ili uzipitie daima masharti ya sasa.",
          params: [
            ["accepted", "boolean", "lazima", "Lazima iwe `true` kamili."],
            [
              "version",
              "string",
              "lazima",
              "Kamba kamili ya toleo iliyorudishwa na GET.",
            ],
          ],
          request: `{
  "accepted": true,
  "version": "2026-09-12"
}`,
          response: `{
  "status": "ok",
  "data": {
    "confirmed": true,
    "version": "2026-09-12",
    "accepted_at": "2026-09-18T14:00:00Z"
  }
}`,
        },
      ],
    },

    /* ── Pricing ──────────────────────────────────────── */
    pricing: {
      en: [
        {
          t: "p",
          x: "The catalog endpoint returns every available model with prices in Sparks and KES, provider metadata (including where processing happens), playground limits and the configured base URL. The workspace pricing table is built from it.",
        },
        {
          t: "endpoint",
          method: "GET",
          path: "/api/v1/agent/pricing/",
          auth: "session",
          desc: "Model catalog with Spark prices, provider details and workspace parameters.",
          response: `{
  "status": "ok",
  "data": {
    "models": [
      {
        "model": "example-model",
        "provider": "fikra",
        "sparks_per_1m_input": 1355,
        "sparks_per_1m_output": 1355,
        "input_kes_per_1m": 135.45,
        "output_kes_per_1m": 135.45,
        "max_context": 131072,
        "max_output_tokens": 32768,
        "capabilities": ["text", "chat"],
        "description": "…"
      }
    ],
    "providers": { "…": "provider details incl. processing region" },
    "base_url": "https://agent-api.uwenzai.com/api/v1",
    "playground_limits": { "max_messages": 60, "max_input_tokens": 8192, "max_output_tokens": 1024 },
    "markup_percent": 5.0,
    "spark_value_kes": 0.1
  }
}`,
        },
        {
          t: "callout",
          kind: "info",
          x: "Prices include the provider markup and are rounded for display; billing uses exact server-side rates. Models without published prices are never listed — nothing is silently zero-priced.",
        },
      ],
      sw: [
        {
          t: "p",
          x: "Huduma ya orodha hurudisha kila modeli inayopatikana pamoja na bei kwa Sparks na KES, maelezo ya watoa huduma (ikiwemo mahali uchakataji unapofanyika), vikwazo vya eneo la majaribio na anwani ya msingi iliyowekwa. Meza ya bei eneo la kazi imejengwa kutoka humo.",
        },
        {
          t: "endpoint",
          method: "GET",
          path: "/api/v1/agent/pricing/",
          auth: "session",
          desc: "Orodha ya modeli pamoja na bei za Sparks, maelezo ya watoa huduma na vigezo vya eneo la kazi.",
          response: `{
  "status": "ok",
  "data": {
    "models": [
      {
        "model": "example-model",
        "provider": "fikra",
        "sparks_per_1m_input": 1355,
        "sparks_per_1m_output": 1355,
        "input_kes_per_1m": 135.45,
        "output_kes_per_1m": 135.45,
        "max_context": 131072,
        "max_output_tokens": 32768,
        "capabilities": ["text", "chat"],
        "description": "…"
      }
    ],
    "providers": { "…": "maelezo ya watoa huduma ikiwemo eneo la uchakataji" },
    "base_url": "https://agent-api.uwenzai.com/api/v1",
    "playground_limits": { "max_messages": 60, "max_input_tokens": 8192, "max_output_tokens": 1024 },
    "markup_percent": 5.0,
    "spark_value_kes": 0.1
  }
}`,
        },
        {
          t: "callout",
          kind: "info",
          x: "Bei zinajumuisha nyongeza ya mtoa huduma na zimekaririwa kwa kuonyeshwa; malipo yanatumia viwango kamili vya seva. Modeli zisizo na bei zilizochapishwa hazaorodheshwa kamwe — hakuna kinachopewa bei ya sifuri kwa siri.",
        },
      ],
    },

    /* ── Playground ───────────────────────────────────── */
    playground: {
      en: [
        {
          t: "p",
          x: "The playground is a text-only testing surface that bills through **exactly the same path** as external API calls, using one of your own keys. It is what the workspace Playground tab uses.",
        },
        {
          t: "endpoint",
          method: "POST",
          path: "/api/v1/agent/playground/",
          auth: "session",
          desc: "Send a playground message with a selected key. Limits: 60 messages per conversation, 8,192 input tokens, 1,024 output tokens, 64 KB request body, 20 requests/minute.",
          params: [
            [
              "key_id",
              "string",
              "required",
              "ID of one of your own active keys.",
            ],
            ["model", "string", "required", "Model ID."],
            ["provider", "string", "required", "Provider ID."],
            ["messages", "array", "required", "Conversation messages."],
            ["max_tokens", "integer", "optional", "Capped at 1,024."],
            [
              "save_history",
              "boolean",
              "optional",
              "Persist the exchange for the model+provider pair (optional saved history).",
            ],
            ["client_operation_id", "string", "optional", "Idempotency key."],
          ],
          response: `{ "…same shape as /chat/completions…", "_uwenzai": { "history_saved": true } }`,
        },
        {
          t: "endpoint",
          method: "GET",
          path: "/api/v1/agent/playground/history/",
          auth: "session",
          desc: "Read saved playground history for a model+provider pair. Query parameters: `model` and `provider`.",
          response: `{
  "status": "ok",
  "data": { "messages": [ { "role": "user", "content": "Hello!" }, { "role": "assistant", "content": "Habari!" } ] }
}`,
        },
        {
          t: "endpoint",
          method: "DELETE",
          path: "/api/v1/agent/playground/history/",
          auth: "session",
          desc: "Delete saved history for a model+provider pair and start fresh.",
          response: `{ "status": "ok", "data": { "messages": [] } }`,
        },
      ],
      sw: [
        {
          t: "p",
          x: "Eneo la majaribio ni uso wa majaribio wa maandishi tu unaolipa kwa **njia ile ile kabisa** kama miito ya nje ya API, ukitumia moja ya funguo zako wenyewe. Ni kile kichupo cha Majaribio eneo la kazi kinachotumia.",
        },
        {
          t: "endpoint",
          method: "POST",
          path: "/api/v1/agent/playground/",
          auth: "session",
          desc: "Tuma ujumbe wa majaribio kwa ufunguo uliochaguliwa. Vikwazo: ujumbe 60 kwa mazungumzo, tokeni 8,192 za kuingia, tokeni 1,024 za kutoka, mwili wa ombi wa KB 64, maombi 20 kwa dakika.",
          params: [
            [
              "key_id",
              "string",
              "lazima",
              "Kitambulisho cha moja ya funguo zako zinazotumika.",
            ],
            ["model", "string", "lazima", "Kitambulisho cha modeli."],
            ["provider", "string", "lazima", "Kitambulisho cha mtoa huduma."],
            ["messages", "array", "lazima", "Ujumbe wa mazungumzo."],
            ["max_tokens", "integer", "hiari", "Kimekishwa saa 1,024."],
            [
              "save_history",
              "boolean",
              "hiari",
              "Hifadhi mabadilishano kwa jozi ya modeli+mtoa huduma (historia ya hiari).",
            ],
            [
              "client_operation_id",
              "string",
              "hiari",
              "Ufunguo wa idempotency.",
            ],
          ],
          response: `{ "…umbo lile lile kama /chat/completions…", "_uwenzai": { "history_saved": true } }`,
        },
        {
          t: "endpoint",
          method: "GET",
          path: "/api/v1/agent/playground/history/",
          auth: "session",
          desc: "Soma historia iliyohifadhiwa ya eneo la majaribio kwa jozi ya modeli+mtoa huduma. Vigezo vya swali: `model` na `provider`.",
          response: `{
  "status": "ok",
  "data": { "messages": [ { "role": "user", "content": "Habari!" }, { "role": "assistant", "content": "Habari! Naweza kukusaidiaje?" } ] }
}`,
        },
        {
          t: "endpoint",
          method: "DELETE",
          path: "/api/v1/agent/playground/history/",
          auth: "session",
          desc: "Futa historia iliyohifadhiwa kwa jozi ya modeli+mtoa huduma na uanze upya.",
          response: `{ "status": "ok", "data": { "messages": [] } }`,
        },
      ],
    },

    /* ── Errors ───────────────────────────────────────── */
    errors: {
      en: [
        {
          t: "p",
          x: "Errors use the OpenAI-style `error` object with a machine-readable `status` and a human message. Provider details are omitted from error bodies — they may contain prompt content or secrets.",
        },
        {
          t: "code",
          title: "error shape",
          x: `{\n  "error": {\n    "status": "insufficient_sparks",\n    "message": "Insufficient sparks"\n  }\n}`,
        },
        {
          t: "table",
          head: ["HTTP", "status", "Meaning"],
          rows: [
            [
              "400",
              "`invalid_request`",
              "Malformed body, bad parameters, or forbidden override fields (`base_url`, `api_key`, …).",
            ],
            ["401", "`invalid_api_key`", "Missing, revoked or expired key."],
            [
              "402",
              "`insufficient_sparks`",
              "Not enough Sparks for the request.",
            ],
            ["403", "`consent_required`", "Developer terms not accepted."],
            [
              "409",
              "`idempotency_conflict`",
              "The `client_operation_id` was reused with a different request.",
            ],
            [
              "409",
              "`gateway_busy`",
              "Another operation for this account is still in flight. Retry after it settles.",
            ],
            [
              "429",
              "`budget_exceeded`",
              "The key's monthly Sparks budget is exhausted.",
            ],
            [
              "429",
              "`rate_limited`",
              "Too many requests (60/min per key; playground 20/min per user).",
            ],
            [
              "502",
              "`upstream_error`",
              "The provider failed. Check usage before retrying — failed requests are refunded.",
            ],
            [
              "503",
              "`gateway_unavailable`",
              "Gateway temporarily unavailable.",
            ],
            [
              "503",
              "`refund_pending`",
              "A previous operation's refund has not settled yet. Do not start a new paid attempt for the same operation; retry unchanged to check its state.",
            ],
            ["500", "`internal_error`", "Unexpected server error."],
          ],
        },
      ],
      sw: [
        {
          t: "p",
          x: "Makosa yanatumia kipengele cha `error` cha mtindo wa OpenAI chenye `status` inayosomeka kwa mashine na ujumbe wa kibinadamu. Maelezo ya watoa huduma huyo kutoka kwenye miili ya makosa — yanaweza kuwa na maudhui ya maelekezo au siri.",
        },
        {
          t: "code",
          title: "umbo la kosa",
          x: `{\n  "error": {\n    "status": "insufficient_sparks",\n    "message": "Sparks hazitoshi"\n  }\n}`,
        },
        {
          t: "table",
          head: ["HTTP", "status", "Maana"],
          rows: [
            [
              "400",
              "`invalid_request`",
              "Mwili uliojabandika, vigezo vibaya, au uga marufuku wa kubadilisha (`base_url`, `api_key`, …).",
            ],
            [
              "401",
              "`invalid_api_key`",
              "Ufunguo haupo, umebatilishwa au umeisha muda.",
            ],
            ["402", "`insufficient_sparks`", "Sparks hazitoshi kwa ombi hili."],
            [
              "403",
              "`consent_required`",
              "Masharti ya wasanidi hayajakubaliwa.",
            ],
            [
              "409",
              "`idempotency_conflict`",
              "`client_operation_id` imetumika tena kwa ombi tofauti.",
            ],
            [
              "409",
              "`gateway_busy`",
              "Operesheni nyingine ya akaunti hii bado inaendelea. Jaribu tena baada ya kumalizika.",
            ],
            [
              "429",
              "`budget_exceeded`",
              "Bajeti ya Sparks ya mwezi ya ufunguo imeisha.",
            ],
            [
              "429",
              "`rate_limited`",
              "Maombi mengi mno (60/dakika kwa kila ufunguo; eneo la majaribio 20/dakika kwa kila mtumiaji).",
            ],
            [
              "502",
              "`upstream_error`",
              "Mtoa huduma ameshindikana. Kagua matumizi kabla ya kujaribu tena — maombi yaliyoshindwa hurejeshwa.",
            ],
            ["503", "`gateway_unavailable`", "Lango halipatikani kwa muda."],
            [
              "503",
              "`refund_pending`",
              "Malipo ya kurejesha ya operesheni iliyopita hayajamalizika. Usianze jaribio jipya la kulipiwa kwa operesheni ile ile; jaribu bila kubadilisha kuangalia hali yake.",
            ],
            ["500", "`internal_error`", "Kosa lisilotarajiwa la seva."],
          ],
        },
      ],
    },

    /* ── Limits ───────────────────────────────────────── */
    limits: {
      en: [
        {
          t: "table",
          head: ["Limit", "Value", "Applies to"],
          rows: [
            ["Inference rate", "60 requests / minute", "Per API key"],
            ["Playground rate", "20 requests / minute", "Per user"],
            ["Monthly budget", "1 – 1,000,000 Sparks", "Per key, optional"],
            [
              "Playground conversation",
              "60 messages",
              "Per model+provider pair",
            ],
            ["Playground input", "8,192 tokens", "Per message"],
            ["Playground output", "1,024 tokens", "Per message"],
            ["Playground body", "64 KB", "Per request"],
            ["Idempotency key", "1 – 300 characters", "Per request"],
          ],
        },
        {
          t: "p",
          x: "Rate limits are enforced per key, so one busy key never blocks the others. When you hit a limit, back off and retry — repeated hammering only extends the wait.",
        },
      ],
      sw: [
        {
          t: "table",
          head: ["Kikomo", "Thamani", "Kinatumika kwa"],
          rows: [
            ["Kasi ya inference", "maombi 60 / dakika", "Kila ufunguo wa API"],
            [
              "Kasi ya eneo la majaribio",
              "maombi 20 / dakika",
              "Kila mtumiaji",
            ],
            [
              "Bajeti ya mwezi",
              "Sparks 1 – 1,000,000",
              "Kila ufunguo, kwa hiari",
            ],
            [
              "Mazungumzo ya majaribio",
              "ujumbe 60",
              "Kila jozi ya modeli+mtoa huduma",
            ],
            ["Kuingia kwa majaribio", "tokeni 8,192", "Kila ujumbe"],
            ["Kutoka kwa majaribio", "tokeni 1,024", "Kila ujumbe"],
            ["Mwili wa majaribio", "KB 64", "Kila ombi"],
            ["Ufunguo wa idempotency", "herufi 1 – 300", "Kila ombi"],
          ],
        },
        {
          t: "p",
          x: "Vikwazo vya kasi vinatumika kwa kila ufunguo, kwa hiyo ufunguo mmoja aliye na shughuli nyingi hamzuii wengine. Ukifika kikomo, subiri kidogo kisha ujaribu tena — kushudia bila kukoma kuongeza subiri tu.",
        },
      ],
    },

    /* ── Billing ──────────────────────────────────────── */
    billing: {
      en: [
        {
          t: "p",
          x: "Every request reserves an estimated number of Sparks before the model runs, then settles at the actual amount when the response arrives. You are never charged more than the reservation, and a failed request is refunded.",
        },
        {
          t: "callout",
          kind: "info",
          x: "**Sparks are not money, coins or a currency.** A Spark is a unit that measures how much of the UwenzAi service you use. Sparks are an internal allowance tied to your UwenzAi account: they are not a store of value, they cannot be transferred or exchanged, and the KES figures in this documentation are reference prices for buying Sparks — not a redemption value.",
        },
        {
          t: "ul",
          items: [
            "**1 Spark = KES 0.1** (10 Sparks = KES 1) — the reference price for buying Sparks, which then measure usage. Provider prices are converted to KES with the live EUR/KES rate plus the provider markup (8% default, shown per provider), then expressed in Sparks.",
            "Billing uses exact server-side rates per token; displayed prices are rounded for readability.",
            "Send a unique `client_operation_id` with every paid request. If a response is lost (timeout, crash), retrying the same ID returns the original outcome — never a second charge.",
            "The `_uwenzai` block in every response reports the exact Sparks charged.",
          ],
        },
        {
          t: "callout",
          kind: "warn",
          x: "**If a request fails with `refund_pending`**, the refund has not settled yet. Do not start a new paid attempt for the same operation — retry the unchanged request to check its state, or refresh usage.",
        },
      ],
      sw: [
        {
          t: "p",
          x: "Kila ombi huhifadhi makadirio ya Sparks kabla modeli haijaendeshwa, kisha linafikia kiasi halisi jibu linapofika. Hulipwi zaidi ya kilichohifadhiwa kamwe, na ombi lililoshindwa hurejeshwa.",
        },
        {
          t: "callout",
          kind: "info",
          x: "**Sparks si pesa, si sarafu wala kizio cha kifedha.** Spark ni kizio kinachopima kiasi cha huduma ya UwenzAi unachotumia. Sparks ni kiasi cha ndani cha akaunti yako ya UwenzAi: si akiba ya thamani, haziwezi kuhamishwa wala kubadilishwa, na takwimu za KES katika nyaraka hizi ni bei za rejea kununua Sparks — si thamani ya kurejeshwa.",
        },
        {
          t: "ul",
          items: [
            "**Spark 1 = KES 0.1** (Sparks 10 = KES 1) — bei ya rejea ya kununua Sparks, ambazo kisha zinapima matumizi. Bei za watoa huduma hubadilishwa kuwa KES kwa kiwango cha moja kwa moja cha EUR/KES pamoja na nyongeza ya mtoa huduma (8% chaguo-msingi, inaonyeshwa kwa kila mtoa huduma), kisha zinaonyeshwa kwa Sparks.",
            "Malipo yanatumia viwango kamili vya seva kwa kila tokeni; bei zinazoonyeshwa zimekaririwa kwa urahisi wa kusoma.",
            "Tuma `client_operation_id` ya kipekee na kila ombi lenye malipo. Jibu likipotea (muda umeisha, ajali), kuirudia hurudisha matokeo ya awali — kamwe malipo ya pili.",
            "Sehemu ya `_uwenzai` kwenye kila jibu inaripoti Sparks kamili zilizolipwa.",
          ],
        },
        {
          t: "callout",
          kind: "warn",
          x: "**Ombi likishindwa kwa `refund_pending`**, kurejeshwa hakujamalizika. Usianze jaribio jipya la kulipiwa kwa operesheni ile ile — jaribu ombi hilo bila kubadilisha kuangalia hali yake, au onyesha upya matumizi.",
        },
      ],
    },

    /* ── Providers ────────────────────────────────────── */
    providers: {
      en: [
        {
          t: "p",
          x: "The gateway routes your request to the provider that owns the model you chose. Providers are configured server-side with their own base URLs and credentials — you never talk to them directly, and their credentials never cross the API.",
        },
        {
          t: "ul",
          items: [
            "**Processing region.** Each provider's label (for example *United States (US processing)*) tells you where inference runs.",
            "**Model availability** differs per provider and region; `GET /models` always returns what is actually reachable right now.",
            "**Provider markup** is shown per provider in the pricing catalog and included in Spark prices.",
          ],
        },
        {
          t: "callout",
          kind: "info",
          x: "The full list of providers, their processing regions and the legal disclosure is published on the [Service Provider Disclosure](https://uwenzai.com/subprocessors) page.",
        },
      ],
      sw: [
        {
          t: "p",
          x: "Lango linaelekeza ombi lako kwa mtoa huduma anayemiliki modeli uliyochagua. Watoa huduma wamewekwa upande wa seva kwa anwani zao za msingi na vitambulisho vyao — huwasiliani nao moja kwa moja kamwe, na vitambulisho vyao huvuka API kamwe.",
        },
        {
          t: "ul",
          items: [
            "**Eneo la uchakataji.** Lebo ya kila mtoa huduma (mf. *Marekani (uchakataji wa Marekani)*) inakuambia inference inaendeshwa wapi.",
            "**Upatikanaji wa modeli** hutofautiana kwa mtoa huduma na eneo; `GET /models` hurudisha daima yaliyopatikana kwa kweli sasa hivi.",
            "**Nyongeza ya mtoa huduma** inaonyeshwa kwa kila mtoa huduma kwenye orodha ya bei na imejumuishwa kwenye bei za Sparks.",
          ],
        },
        {
          t: "callout",
          kind: "info",
          x: "Orodha kamili ya watoa huduma, maeneo yao ya uchakataji na taarifa ya kisheria imechapishwa kwenye ukurasa wa [Taarifa ya Watoa Huduma](https://uwenzai.com/subprocessors).",
        },
      ],
    },
  },
};
