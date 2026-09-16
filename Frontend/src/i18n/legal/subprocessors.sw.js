// src/i18n/legal/subprocessors.sw.js
//
// Utoaji taarifa wa watoa huduma (wachakataji wadogo), Kiswahili. Ni watoa
// huduma waliopo kwenye msimbo wa uzalishaji tu waliotajwa hapa.

export const subprocessorsSw = {
  title: "Taarifa ya Watoa Huduma wa UwenzAi",
  intro: [
    {
      type: "p",
      text: "UwenzAi Limited hutumia watoa huduma wa nje walioorodheshwa hapa chini kuendesha Huduma. Wanachakata data binafsi kwa maagizo yetu na chini ya mkataba, kwa lengo la kutoa kazi iliyoelezwa pekee. Ukurasa huu unaunga mkono kifungu cha 11 cha [Sera yetu ya Faragha](/privacy).",
    },
    {
      type: "p",
      text: "Baadhi ya watoa huduma hutumika tu wakati kipengele husika kimewashwa au kusanidiwa. Maeneo ya kuchakata data hutegemea mtoa huduma na huduma iliyosanidiwa, na uchakataji unaweza kuhusisha uhamishaji wa data kati ya nchi. Angalia kifungu cha 11 cha Sera ya Faragha kwa taarifa yetu kuhusu uhamishaji wa data kati ya nchi.",
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
      heading: "4. Utafutaji wa wavuti",
      blocks: [
        {
          type: "table",
          head: ["Mtoa huduma", "Lengo", "Data inayohusika"],
          rows: [
            [
              'European Perspective SAS ("Staan")',
              "Upataji wa matokeo ya utafutaji wa wavuti (tu pale mtumiaji anapowasha utafutaji wa wavuti kwenye mazungumzo). Mahali: Ufaransa / Umoja wa Ulaya (OVHcloud).",
              "Hoja za utafutaji zinazotokana na jumbe za mtumiaji; hakuna data ya akaunti, hakuna historia ya mazungumzo. Inaweza kutumia mtoa huduma mbadala wa utafutaji asiyehifadhi hoja. Sera ya faragha: https://staan.ai/privacy-policy",
            ],
          ],
        },
      ],
    },
    {
      id: "5",
      heading: "5. Malipo",
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
      id: "6",
      heading: "6. Kuingia, barua pepe na kuzuia matumizi mabaya",
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
      id: "7",
      heading: "7. Ufuatiliaji wa hitilafu (hiari)",
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
      id: "8",
      heading:
        "8. Eneo la wasanidi na API ya uendeshaji wa modeli za AI ya UwenzAi-Dev",
      blocks: [
        {
          type: "p",
          text: "UwenzAi-Dev hutoa eneo la majaribio kwa wasanidi, eneo la kujaribu mazungumzo ya maandishi na API ya uendeshaji wa modeli za AI kwa wenye akaunti wanaostahili na walioingia kwenye akaunti zao. Ni tofauti na kipengele cha Bring Your Own Key na haipatikani kwa wageni wasio na akaunti.",
        },
        {
          type: "p",
          text: "Watoa huduma wa awali wa lango ni Cortecs, Mistral AI, Fikra, Inceptron, AKI.IO na OVHcloud. Mtoa huduma hupatikana tu ikiwa amesanidiwa na amewekwa kwenye orodha ya lango. Kutegemea modeli na mtoa huduma uliowachagua, mtoa huduma anayechakata ombi lako hupokea ujumbe uliotuma, historia yoyote iliyojumuishwa katika ombi, na mipangilio inayohitajika kuzalisha jibu. Kutajwa kwa mtoa huduma hapa hakumaanishi kwamba kila ombi hutumwa kwake.",
        },
        {
          type: "p",
          text: "Rekodi za kawaida za matumizi ya lango zina metadata ya utendaji na malipo badala ya maudhui ya ujumbe au majibu. Ukichagua kuhifadhi historia katika eneo la majaribio, UwenzAi huhifadhi kando mazungumzo yanayobakizwa katika muundo uliosimbwa kwa njia fiche kwa akaunti yako na modeli pamoja na mtoa huduma uliowachagua. Mpangilio wa kuzimwa kwa chaguo-msingi, vikomo vya hifadhi, muda wa kuhifadhi na vidhibiti vya ufutaji vimeelezwa katika vifungu vya 9, 14 na 15 vya Sera ya Faragha. Miundombinu ya uwekaji mtandaoni huchakata data inayohitajika kuendesha kipengele hiki, kama ilivyoelezwa katika kifungu cha 1 cha taarifa hii.",
        },
        {
          type: "p",
          text: "Upatikanaji wa watoa huduma unaweza kubadilika. Tutasasisha taarifa hii watoa huduma wanapoongezwa au kuondolewa, na kutoa taarifa kuhusu mabadiliko makubwa ya uchakataji kama ilivyoelezwa katika kifungu cha 20 cha Sera ya Faragha. Neno “majaribio” wala mabadiliko ya orodha hii hayaondoi haki zako za kisheria za ulinzi wa data.",
        },
      ],
    },
    {
      id: "9",
      heading: "9. Mabadiliko kwenye orodha hii",
      blocks: [
        {
          type: "p",
          text: "Tunaweza kuongeza au kubadilisha mtoa huduma kadri Huduma inavyoendelea. Tutakapofanya hivyo, tutasasisha ukurasa huu na kubadilisha tarehe ya “Ilisasishwa mwisho”. Ikiwa mabadiliko yanaathiri kwa kiasi kikubwa jinsi data yako binafsi inachakatwa, tutatoa taarifa kama ilivyoelezwa katika kifungu cha 20 cha [Sera ya Faragha](/privacy).",
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
