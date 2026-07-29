// src/i18n/legal/subprocessors.sw.js
//
// Utoaji taarifa wa watoa huduma (wachakataji wadogo), Kiswahili. Ni watoa
// huduma waliopo kwenye msimbo wa uzalishaji tu waliotajwa hapa.

export const subprocessorsSw = {
  title: "Taarifa ya Watoa Huduma wa UwenzAi",
  intro: [
    {
      type: "p",
      text: "UwenzAi Limited hutumia watoa huduma wa nje walioorodheshwa hapa chini kuendesha Huduma. Wanachakata data binafsi kwa maagizo yetu na chini ya mkataba, kwa lengo la kutoa kazi iliyoelezwa pekee. Ukurasa huu unaunga mkono kifungu cha 10 cha [Sera yetu ya Faragha](/privacy).",
    },
    {
      type: "p",
      text: "Watoa huduma hawa wote wanafanya kazi nje ya Kenya, kwa hivyo kutumia Huduma kunahusisha kuhamisha data kuvuka mipaka. Watoa huduma wengine hutumika tu wakati kipengele kinachohusika kimewashwa au kimesanidiwa.",
    },
  ],
  sections: [
    {
      id: "1",
      heading: "1. Uwekaji mtandaoni, hifadhidata na hifadhi ya faili",
      blocks: [
        {
          type: "table",
          head: ["Mtoa huduma", "Lengo", "Data inayohusika"],
          rows: [
            [
              "Railway",
              "Uwekaji wa programu mtandaoni, hifadhidata ya PostgreSQL, akiba ya Redis na safu za kazi za nyuma, na ndoo inayooana na S3 inayohifadhi picha zilizopakiwa na zilizozalishwa.",
              "Data yote ya akaunti na ya Huduma, ikijumuisha maudhui yako. Data iliyohifadhiwa imesimbwa kwa njia fiche ilipohifadhiwa kwenye tabaka la hifadhi; vitu vya picha ni vya faragha na vinafikiwa tu kupitia viungo vilivyotiwa sahihi vya muda mfupi.",
            ],
          ],
        },
      ],
    },
    {
      id: "2",
      heading: "2. Uzalishaji wa maandishi kwa AI na uwakilishi wa kihesabu",
      blocks: [
        {
          type: "table",
          head: ["Mtoa huduma", "Lengo", "Data inayohusika"],
          rows: [
            [
              "Cortecs",
              "Mtoa huduma mkuu kwa aina zote mbili za uchakataji wa maandishi tunaofanya: kuzalisha majibu ya mazungumzo, na kuunda uwakilishi wa kihesabu (embeddings) unaowezesha kumbukumbu ya mhusika.",
              "Kwa majibu, muktadha wa mazungumzo: ujumbe wako, maelezo ya mhusika, na muhtasari na kumbukumbu zinazohusika. Kwa uwakilishi wa kihesabu, maandishi ya kumbukumbu inayohifadhiwa au maandishi yanayolinganishwa nayo. Hakuna data ya malipo.",
            ],
            [
              "Mistral AI",
              "Mtoa huduma wa akiba kwa majibu ya mazungumzo na kwa uwakilishi wa kihesabu, hutumika Cortecs inaposhindwa au haijasanidiwa. Pia huitwa moja kwa moja kwa kazi za maandishi zinazoendeshwa nyuma: kuchambua kumbukumbu, kuandika muhtasari wa mazungumzo, kutunga maelezo ya tabia ya mhusika, na kupima hisia za mazungumzo.",
              "Muktadha huo huo wa mazungumzo na maandishi ya kumbukumbu kama yaliyotajwa hapo juu, kutegemea kazi inayoendeshwa.",
            ],
            [
              "Mtoa huduma wako mwenyewe wa AI (Leta Kitufe Chako Mwenyewe)",
              "Hutumika kwa majibu ya mazungumzo badala ya watoa huduma walio hapo juu tu ikiwa unasanidi mahali pako pa kufikia na kitufe chako cha API. Uwakilishi wa kihesabu na kazi zinazoendeshwa nyuma huendelea kutumia watoa huduma walio hapo juu.",
              "Muktadha wa mazungumzo yako hutumwa mahali ulipochagua. Masharti ya mtoa huduma huyo yanatumika na hatuna udhibiti wowote juu ya muda wanaobakiza data.",
            ],
          ],
        },
        {
          type: "p",
          text: "Mtoa huduma na muundo si kitu kimoja. Cortecs ni jukwaa la uendeshaji linaloendesha miundo huria ya wengine kwa maagizo yetu; Mistral AI hutengeneza na kuendesha miundo yake yenyewe. Kampuni iliyotajwa katika jedwali ni ile inayopokea data yako. Muundo ambao kila mmoja huendesha huwekwa katika usanidi wetu na unaweza kubadilika bila kubadilisha nani anachakata data.",
        },
      ],
    },
    {
      id: "3",
      heading: "3. Uzalishaji wa picha",
      blocks: [
        {
          type: "table",
          head: ["Mtoa huduma", "Lengo", "Data inayohusika"],
          rows: [
            [
              "fal.ai",
              "Mtoa huduma mkuu wa uzalishaji wa picha, anayeendesha modeli ya picha tunayotumia kwa picha za wasifu, nyumba ya picha na picha za ndani ya mazungumzo.",
              "Maandishi ya kidokezo yaliyozalishwa, na — kwa vipengele vinavyoweka mwonekano wa mhusika ukiwa sawa — picha iliyopo ya mhusika iliyopakiwa kama rejeleo. Hakuna vitambulisho vya akaunti vinavyotumwa. Tunamwomba mtoa huduma asihifadhi maombi na matokeo, na tunatuma ombi la kufuta kwa nia njema baada ya kukusanya matokeo.",
            ],
            [
              "Stability AI",
              "Mtoa huduma wa akiba wa uzalishaji wa picha, anayetumika wakati mtoa huduma mkuu hapatikani au hajasanidiwa.",
              "Maandishi ya kidokezo yaliyozalishwa na, kwa maombi yanayoongozwa na rejeleo, picha ya rejeleo.",
            ],
          ],
        },
      ],
    },
    {
      id: "4",
      heading: "4. Malipo",
      blocks: [
        {
          type: "table",
          head: ["Mtoa huduma", "Lengo", "Data inayohusika"],
          rows: [
            [
              "Paystack",
              "Kuchakata manunuzi ya cheche na ya usajili, ikijumuisha njia za malipo za pesa za simu na za kadi.",
              "Kiasi cha muamala, sarafu, kumbukumbu yetu na kilichonunuliwa, pamoja na maelezo ya malipo unayoweka moja kwa moja kwa Paystack. Hatupokei wala hatuhifadhi nambari yako kamili ya kadi, nambari ya usalama ya kadi wala PIN.",
            ],
          ],
        },
      ],
    },
    {
      id: "5",
      heading: "5. Kuingia, barua pepe na kuzuia matumizi mabaya",
      blocks: [
        {
          type: "table",
          head: ["Mtoa huduma", "Lengo", "Data inayohusika"],
          rows: [
            [
              "Google",
              "Kuingia kwa Google, na ulinzi wa reCAPTCHA dhidi ya roboti kwenye fomu nyeti.",
              "Kwa kuingia, kitambulisho cha akaunti na maelezo ya msingi ya wasifu ambayo Google inarudisha. Kwa reCAPTCHA, ishara za kiufundi ambazo Google hutumia kuamua kama ombi limetengenezwa na roboti.",
            ],
            [
              "Mtoa huduma wetu wa kutuma barua pepe",
              "Kutuma barua pepe za miamala kupitia muunganisho wa SMTP uliothibitishwa na uliosimbwa kwa njia fiche — kwa mfano uthibitishaji wa anwani na urejeshaji wa nenosiri.",
              "Anwani yako ya barua pepe na yaliyomo ya ujumbe huo. Hatutumi barua pepe za matangazo kupitia njia hii.",
            ],
          ],
        },
      ],
    },
    {
      id: "6",
      heading: "6. Ufuatiliaji wa hitilafu (hiari)",
      blocks: [
        {
          type: "table",
          head: ["Mtoa huduma", "Lengo", "Data inayohusika"],
          rows: [
            [
              "Sentry",
              "Kunasa taarifa za kiufundi za uchunguzi wakati programu inashindwa kufanya kazi. Hutumika tu wakati imewashwa katika usanidi wetu.",
              "Taarifa za uchunguzi wa hitilafu na utendaji. Imesanidiwa isitume data binafsi kwa chaguo-msingi.",
            ],
          ],
        },
      ],
    },
    {
      id: "7",
      heading: "7. Mabadiliko kwenye orodha hii",
      blocks: [
        {
          type: "p",
          text: "Tunaweza kuongeza au kubadilisha mtoa huduma kadri Huduma inavyoendelea. Tutakapofanya hivyo, tutasasisha ukurasa huu na kubadilisha tarehe ya “Ilisasishwa mwisho”. Ikiwa mabadiliko yanaathiri kwa kiasi kikubwa jinsi data yako binafsi inachakatwa, tutatoa taarifa kama ilivyoelezwa katika kifungu cha 19 cha [Sera ya Faragha](/privacy).",
        },
        {
          type: "p",
          text: "Maswali kuhusu mtoa huduma yeyote kwenye orodha hii: [info@uwenzai.com](mailto:info@uwenzai.com).",
        },
      ],
    },
  ],
  footer:
    "UwenzAi Limited – Nairobi, Kenya. Maswali? [info@uwenzai.com](mailto:info@uwenzai.com)",
};
