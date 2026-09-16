# UwenzAi — East African AI Companion 

**Sehiya na marafiki wa AI wanaokujua — English & Kiswahili** 🇰🇪🇹🇿🇺🇬

[English](#english) | [Kiswahili](#kiswahili)
(Readme is AI-Generated and might be wrong)

---

<a id="english"></a>
## English

### About

**East African AI Companion** (UwenzAi) is a full-stack AI companion platform built for East African users. It lets people create, customize, and chat with persistent AI characters that remember past conversations, speak multiple languages, and can proactively reach out — all backed by a real-time, credit-based ("Sparks") usage model designed for East African markets.

The platform is built and maintained by **UwenzAi Limited**, based in Nairobi, Kenya.

### Key Features

- **Deep character customization** — create fully custom AI companions: gender, age, occupation, personality traits, hobbies, tone of voice, relationship style, clothing style, and detailed appearance (skin tone, eye color, hairstyle/length/color, body type, beard style, and more) across **realistic, anime, and cartoon** visual styles.
- **Persistent memory** — conversations are summarized and stored per character using a `pgvector`-backed semantic memory layer, so companions recall context across sessions instead of starting from zero every time.
- **Proactive chat** — companions can be configured to send a follow-up message after a period of inactivity, keeping the conversation alive.
- **Agentic reminders** — characters can schedule, edit and deliver reminders themselves through a `create_reminder` tool, with a Celery Beat delivery pipeline and per-user limits.
- **In-chat memory editor** — review and edit what a character remembers about you, directly from the chat.
- **Advanced feature toggles** — per-user, backend-enforced switches for optional chat features (deep thinking, web search, proactive messages), default off.
- **UwenzAi-Dev (Agent Gateway)** — an OpenAI-compatible inference API for developers: mint API keys (shown once, stored as verification hashes), optionally cap each key with a monthly Sparks budget, and call six configurable providers — Cortecs, Mistral AI, Fikra, Inceptron, AKI.IO, OVHcloud — with automatic routing to the cheapest provider, all metered through the same Sparks balance with scoped idempotency for usage accounting.
- **Multi-provider AI backend** — both chat generation and memory embeddings are routed through Cortecs first, with automatic fallback to Mistral, keeping the platform resilient to any single provider's downtime.
- **Bilingual UI** — the frontend is fully localized in **English** and **Kiswahili**, with automatic language detection for incoming messages.
- **Guest mode** — visitors can chat immediately without creating an account, using a limited free allowance of Sparks credits tied to a local session ID.
- **Sparks credits** — a simple, transparent usage-based credit system covers message pairs, character generation/edits, and image generation, with bonus credits for creators whose public characters get approved.
- **Bring Your Own Key (BYOK)** — users can supply their own LLM API key for a drastically reduced per-message credit cost.
- **Real-time updates** — Sparks balance and chat messages are pushed live over WebSockets (Django Channels), with REST fallbacks for reliability.
- **Trust & safety** — message/content moderation, character review workflow before public listing, account lockout protection, reCAPTCHA on auth flows, and encryption of sensitive user data (email, phone, message content).
- **Community characters** — users can publish characters publicly for others to chat with, subject to moderator review.

### Tech Stack

**Backend**
- Django 6 + Django REST Framework
- Django Channels (WebSockets) with Redis-backed channel layer
- Celery for background tasks, split across isolated `general` / `images` queues, plus Celery Beat for reminder delivery and daily agent-catalog sync
- PostgreSQL with the `pgvector` extension for semantic memory
- Agent Gateway: OpenAI-compatible multi-provider inference API with Sparks metering, per-key budgets and idempotent usage accounting (`capp/classes/agent_gateway/`)
- Media in a private S3-compatible object storage bucket shared by the web and worker services
- Dependencies compiled with `pip-compile` from `requirements.in`
- JWT auth (`djangorestframework-simplejwt`) + Google OAuth
- Uvicorn (ASGI) for production serving
- AI/LLM integration: Cortecs routing for chat and embeddings, Mistral fallback (`mistral-common`), OpenAI-compatible client, `tiktoken`
- Paystack for payments; `fal-client` for image generation

**Frontend**
- React 19 + Vite
- Tailwind CSS
- React Router DOM
- Custom WebSocket context for live balance/chat updates

**Infrastructure**
- CI/CD runs lint, formatting, tests, and secret scanning (Gitleaks). The pipeline
  definitions and internal product/deployment documentation are maintained in the
  private development repository and are not part of this source release.

### Project Structure

```
UwenzAi/
├── Backend/                  Django project (capp app: models, views, WebSocket consumers, billing)
├── Frontend/                 Vite + React application
└── templates/                Code-generation templates
```

> **Note on this repository.** This is a curated source release. Internal
> engineering runbooks, deployment documentation, CI/CD workflow definitions,
> product documents, and development-only modules are intentionally excluded.
> Some in-code comments reference those internal documents by path; those paths
> exist only in the private development repository.

### Getting Started (local development)

**Backend**
```bash
cd Backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# set DB_NAME, DB_USR, DB_PWD, DJANGO_SPARE_SECRET_KEY / DJANGO_SECRET_KEY, FIELD_ENCRYPTION_KEY, etc.
python manage.py migrate
python manage.py runserver
```

**Frontend**
```bash
cd Frontend
npm install
# set VITE_API_BASE_URL and VITE_WS_BASE_URL in a .env file
npm run dev
```

### License

This project is licensed under the **UwenzAi Source-Available License v1.0**.

- The source code is available for review, learning, experimentation, and other **non-commercial use**.
- You may run, modify, and share the code **only for non-commercial purposes**, provided you keep the license text and proper attribution.
- **Commercial use is not allowed** without a separate written agreement with **UwenzAi Limited**. This includes selling the software, offering it as a paid or ad-supported service, or using it to provide monetized products or services.
- Each released version automatically converts to the **GNU Affero General Public License v3.0 (AGPL-3.0)** on the **7th anniversary** of its release date.
- Third-party software, libraries, models, and APIs used by this project remain subject to their **own** licenses.

For full details, see the [`LICENSE`](./LICENSE) file in this repository.

---

<a id="kiswahili"></a>
## Kiswahili

### Kuhusu Mradi

**East African AI Companion** (UwenzAi) ni jukwaa kamili la programu za AI zinazokutana na watumiaji, lililojengwa kwa ajili ya watumiaji wa Afrika Mashariki. Linawawezesha watu kuunda, kubinafsisha, na kuzungumza na wahusika wa AI wanaokumbuka mazungumzo yaliyopita, wanaozungumza lugha nyingi, na wanaoweza kuanzisha mazungumzo wenyewe — yote yakiendeshwa na mfumo wa matumizi wa wakati halisi unaotumia Cheche (credit za matumizi) ulioundwa kwa masoko ya Afrika Mashariki.

Jukwaa hili linajengwa na kudumishwa na **UwenzAi Limited**, kampuni yenye makazi Nairobi, Kenya.

### Vipengele Muhimu

- **Ubinafsishaji wa kina wa wahusika** — unda wahusika wa AI wa kipekee kabisa: jinsia, umri, kazi, sifa za utu, mambo anayopenda, sauti ya mazungumzo, mtindo wa uhusiano, mtindo wa mavazi, na maelezo ya kina ya sura (rangi ya ngozi, rangi ya macho, mtindo/urefu/rangi ya nywele, umbo la mwili, mtindo wa ndevu, na zaidi) katika mitindo ya kuona ya **halisi, anime, na katuni**.
- **Kumbukumbu ya kudumu** — mazungumzo yanafupishwa na kuhifadhiwa kwa kila mhusika kwa kutumia mfumo wa kumbukumbu wa kisemantiki unaotegemea `pgvector`, hivyo wahusika hukumbuka muktadha kati ya vipindi vya mazungumzo badala ya kuanza kutoka sifuri kila wakati.
- **Mazungumzo shirikishi (proactive chat)** — wahusika wanaweza kusanidiwa kutuma ujumbe wa kufuatilia baada ya muda wa kutokuwepo, kuendeleza mazungumzo hai.
- **Vikumbusho vya kiamri (agentic reminders)** — wahusika wenyewe wanaweza kupanga, kuhariri na kutuma vikumbusho kupitia zana ya `create_reminder`, kwa mfumo wa Celery Beat wa kusambaza na mipaka kwa kila mtumiaji.
- **Kihariri cha kumbukumbu ndani ya mazungumzo** — kagua na uhariri kile mhusika anakikumbuka kuhusu wewe, moja kwa moja kutoka kwenye mazungumzo.
- **Swichi za vipengele vya ziada** — swichi za kila mtumiaji zinazotekelezwa upande wa seva kwa vipengele vya hiari vya mazungumzo (kufikiri kwa kina, utafuti wa tovuti, ujumbe wa kuongoza), zikiwa zimezimwa kwa chaguo-msingi.
- **UwenzAi-Dev (Lango la Wataalamu / Agent Gateway)** — API ya uamuzi inayofanana na ya OpenAI kwa waendelezaji: tengeneza funguo za API (zinaonyeshwa mara moja, huhifadhiwa kama alama za uthibitisho), weka kikomo cha bajeti ya kila mwezi ya Cheche kwa kila funguo kwa hiari, na itumie kwa watoa huduma sita wanaosanidiwa — Cortecs, Mistral AI, Fikra, Inceptron, AKI.IO, OVHcloud — ikiwa na uelekezaji wa kiotomatiki kwa mtoa huduma wa bei nafuu zaidi, vyote vikipimwa kwa salio ileile ya Cheche kwa kutumia uhakiki wa kitambulisho wa shughuli (idempotency).
- **Mfumo wa AI wa watoa huduma wengi** — uzalishaji wa majibu ya mazungumzo na uwakilishi wa kihesabu wa kumbukumbu vyote hupitishwa kwa Cortecs kwanza, vikiwa na urejeshaji wa kiotomatiki kwa Mistral, hivyo jukwaa linakuwa thabiti hata ikiwa mtoa huduma mmoja ana tatizo.
- **Kiolesura cha lugha mbili** — programu ya mtumiaji imetafsiriwa kikamilifu kwa **Kiingereza** na **Kiswahili**, ikiwa na utambuzi wa kiotomatiki wa lugha kwa ujumbe unaoingia.
- **Hali ya mgeni (guest mode)** — wageni wanaweza kuzungumza mara moja bila kuunda akaunti, wakitumia kiwango kidogo cha bure cha Cheche kilichounganishwa na kitambulisho cha kipindi cha kivinjari.
- **Mfumo wa Cheche** — mfumo rahisi na wazi wa "credit" za matumizi unaogharamia jozi za ujumbe, uundaji/uhariri wa wahusika, na uzalishaji wa picha, ukiwa na Cheche za ziada kwa waundaji ambao wahusika wao wa umma wanakubaliwa.
- **Tumia Ufunguo Wako Mwenyewe (BYOK)** — watumiaji wanaweza kutumia ufunguo wao wa API wa LLM kwa gharama iliyopunguzwa sana kwa kila ujumbe.
- **Masasisho ya wakati halisi** — salio la Cheche na ujumbe wa mazungumzo hutumwa moja kwa moja kupitia WebSockets (Django Channels), ikiwa na njia mbadala ya REST kwa uhakika.
- **Usalama na uadilifu** — udhibiti wa ujumbe/maudhui, mchakato wa ukaguzi wa wahusika kabla ya kuorodheshwa hadharani, ulinzi wa kufungwa kwa akaunti, reCAPTCHA kwenye mifumo ya kuingia, na usimbaji fiche wa taarifa nyeti za mtumiaji (barua pepe, namba ya simu, maudhui ya ujumbe).
- **Wahusika wa jamii** — watumiaji wanaweza kuchapisha wahusika hadharani ili wengine wazungumze nao, baada ya ukaguzi wa wasimamizi.

### Muundo wa Kiteknolojia

**Backend (Nyuma ya Mfumo)**
- Django 6 + Django REST Framework
- Django Channels (WebSockets) na tabaka la kanali linalotegemea Redis
- Celery kwa kazi za nyuma, pamoja na Celery Beat kwa kusambaza vikumbusho na usawazishaji wa kila siku wa orodha ya mifano
- PostgreSQL yenye programu-jalizi ya `pgvector` kwa kumbukumbu ya kisemantiki
- Lango la Wataalamu (Agent Gateway): API ya uamuzi inayofanana na ya OpenAI kwa watoa huduma wengi, ikiwa na upimaji wa Cheche, bajeti kwa kila funguo, na uhasibu wa matumizi wenye uhakiki wa kitambulisho (`capp/classes/agent_gateway/`)
- Uthibitisho wa JWT (`djangorestframework-simplejwt`) + Google OAuth
- Uvicorn (ASGI) kwa uendeshaji wa uzalishaji
- Muunganiko wa AI/LLM: uelekezaji wa Cortecs kwa mazungumzo na uwakilishi wa kihesabu, akiba ya Mistral (`mistral-common`), mteja unaofanana na OpenAI, `tiktoken`
- Paystack kwa malipo; `fal-client` kwa uzalishaji wa picha

**Frontend (Mbele ya Mfumo)**
- React 19 + Vite
- Tailwind CSS
- React Router DOM
- Muktadha maalum wa WebSocket kwa masasisho ya moja kwa moja ya salio/mazungumzo

**Miundombinu**
- CI/CD huendesha ukaguzi wa msimbo, umbizo, majaribio, na ukaguzi wa siri
  (Gitleaks). Mafaili ya mfumo wa CI/CD pamoja na nyaraka za ndani za bidhaa na
  za uwekaji mtandaoni hutunzwa katika hazina ya kibinafsi ya uendelezaji na
  hayajumuishwi katika toleo hili la msimbo.

### Muundo wa Mradi

```
UwenzAi/
├── Backend/                  Mradi wa Django (programu ya capp: modeli, views, WebSocket consumers, malipo)
├── Frontend/                 Programu ya Vite + React
└── templates/                Violezo vya kuzalisha msimbo
```

> **Kumbuka kuhusu hazina hii.** Hili ni toleo la msimbo lililoteuliwa. Nyaraka
> za ndani za uhandisi, nyaraka za uwekaji mtandaoni, mafaili ya CI/CD, nyaraka
> za bidhaa, na moduli za uendelezaji pekee hazijumuishwi kwa makusudi. Maoni
> kadhaa ndani ya msimbo yanarejelea nyaraka hizo za ndani kwa njia ya mahali
> (path); njia hizo zipo tu katika hazina ya kibinafsi ya uendelezaji.

### Kuanza (Uendelezaji wa Ndani)

**Backend**
```bash
cd Backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# weka DB_NAME, DB_USR, DB_PWD, DJANGO_SPARE_SECRET_KEY / DJANGO_SECRET_KEY, FIELD_ENCRYPTION_KEY, n.k.
python manage.py migrate
python manage.py runserver
```

**Frontend**
```bash
cd Frontend
npm install
# weka VITE_API_BASE_URL na VITE_WS_BASE_URL kwenye faili la .env
npm run dev
```

### Leseni

Mradi huu una leseni ya **UwenzAi Source-Available License v1.0**.

- Msimbo wa chanzo unapatikana kwa ukaguzi, kujifunza, majaribio, na **matumizi mengine yasiyo ya kibiashara**.
- Unaweza kuendesha, kubadilisha, na kushiriki msimbo **kwa madhumuni yasiyo ya kibiashara pekee**, mradi ukiwekwa maandishi ya leseni na sifa zinazostahili.
- **Matumizi ya kibiashara hayaruhusiwi** bila mkataba tofauti wa maandishi na **UwenzAi Limited**. Hii inajumuisha kuuza programu, kuitoa kama huduma ya kulipia au inayotegemea matangazo, au kuitumia kutoa bidhaa au huduma zenye faida ya kifedha.
- Kila toleo lililotolewa hubadilika kiotomatiki kuwa **GNU Affero General Public License v3.0 (AGPL-3.0)** baada ya **miaka saba** kutoka tarehe ya kutolewa kwake.
- Programu, maktaba, miundo, na API za wahusika wengine zinazotumika katika mradi huu zinabaki chini ya leseni zao **wenyewe**.

Kwa maelezo kamili, tazama faili la [`LICENSE`](./LICENSE) katika hazina hii.
