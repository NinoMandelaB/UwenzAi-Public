# capp/constants.py
"""
Global pricing constants as defined in the Pricing & Usage Model.
All values are in virtual sparks (1 spark = KES 0.1) unless otherwise noted.
"""

import os

# ----------------------------------------------------------------------
# Single‑use costs (sparks)
# ----------------------------------------------------------------------
MESSAGE_PAIR_COST = 85  # 1 user message + 1 AI reply
CHARACTER_GENERATION_COST = 500  # includes 1 image
CHARACTER_EDIT_COST = 50
IMAGE_GENERATION_COST = 450
CHARACTER_PHOTO_OFFER_COST = 395  # discounted: character-initiated photo the user accepts
BYOK_MESSAGE_PAIR_COST = 5

# Open product decision (see PR description): a search-assisted message pair
# does not currently carry a surcharge. The existing Spark reservation is
# made *before* the AI call, at a point where it is not yet known whether the
# model will actually invoke the web_search tool for this particular
# message -- charging WEB_SEARCH_SURCHARGE_COST unconditionally whenever the
# per-chat search toggle is on would overcharge messages where the model
# chose not to search, and charging it only after the fact would require a
# second reservation/settle cycle that does not exist yet. This constant is
# defined so a future PR can wire it in once product decides between
# "charge whenever the toggle is on" vs "charge only when search actually
# ran" (the latter needs a follow-up debit path, see
# capp/classes/billing/paid_features.py and capp/tasks.py process_ai_response
# where `search_calls_used` is already tracked on the persisted message
# metadata for exactly this purpose).
WEB_SEARCH_SURCHARGE_COST = 15

# ----------------------------------------------------------------------
# Cashback
# ----------------------------------------------------------------------
CASHBACK_PUBLIC_CHARACTER = 250  # per approved published character

# ----------------------------------------------------------------------
# Spark base value
# ----------------------------------------------------------------------
SPARK_BASE_VALUE_KES = 0.1  # 1 spark = KES 0.1

# ----------------------------------------------------------------------
# Limits
# ----------------------------------------------------------------------
MAX_MESSAGE_CHARACTERS = 800  # USER messages only; never truncate AI replies
MAX_TOKENS_PER_MESSAGE_PAIR = 20_000  # system prompt + character card + memory

GUEST_INITIAL_SPARKS = 510  # e.g. 6 message pairs
SIGNUP_SPARKS = 2000

# How long a due-but-undeliverable reminder (insufficient Sparks, transient
# provider/billing error) keeps retrying on the 5-minute Beat tick before we
# give up for good, send one final apology, and delete it. Env-overridable so
# ops can tune it in Railway without a redeploy.
REMINDER_EXPIRY_GRACE_HOURS = int(os.getenv("REMINDER_EXPIRY_GRACE_HOURS", "12"))

# Caps how many PENDING reminders a single user can stack up for a single
# character. Prevents unbounded growth from a chatty or adversarial user
# repeatedly asking for reminders, while still leaving plenty of headroom
# for legitimate everyday use (multiple reminders across a day/week).
# Env-overridable so ops can tune it in Railway without a redeploy.
MAX_PENDING_REMINDERS_PER_USER_CHARACTER = int(os.getenv("MAX_PENDING_REMINDERS_PER_USER_CHARACTER", "15"))

# Mapping of country names (as stored in Character.country) to IANA timezone.
# For multi‑timezone countries, the capital or most populous region is chosen.
COUNTRY_TIMEZONE_MAP = {
    # Africa
    "Algeria": "Africa/Algiers",
    "Angola": "Africa/Luanda",
    "Benin": "Africa/Porto-Novo",
    "Botswana": "Africa/Gaborone",
    "Burkina Faso": "Africa/Ouagadougou",
    "Burundi": "Africa/Bujumbura",
    "Cabo Verde": "Atlantic/Cape_Verde",
    "Cameroon": "Africa/Douala",
    "Central African Republic": "Africa/Bangui",
    "Chad": "Africa/Ndjamena",
    "Comoros": "Indian/Comoro",
    "Congo": "Africa/Brazzaville",
    "Democratic Republic of the Congo": "Africa/Kinshasa",
    "Djibouti": "Africa/Djibouti",
    "Egypt": "Africa/Cairo",
    "Equatorial Guinea": "Africa/Malabo",
    "Eritrea": "Africa/Asmara",
    "Eswatini": "Africa/Mbabane",
    "Ethiopia": "Africa/Addis_Ababa",
    "Gabon": "Africa/Libreville",
    "Gambia": "Africa/Banjul",
    "Ghana": "Africa/Accra",
    "Guinea": "Africa/Conakry",
    "Guinea-Bissau": "Africa/Bissau",
    "Ivory Coast": "Africa/Abidjan",
    "Kenya": "Africa/Nairobi",
    "Lesotho": "Africa/Maseru",
    "Liberia": "Africa/Monrovia",
    "Libya": "Africa/Tripoli",
    "Madagascar": "Indian/Antananarivo",
    "Malawi": "Africa/Blantyre",
    "Mali": "Africa/Bamako",
    "Mauritania": "Africa/Nouakchott",
    "Mauritius": "Indian/Mauritius",
    "Morocco": "Africa/Casablanca",
    "Mozambique": "Africa/Maputo",
    "Namibia": "Africa/Windhoek",
    "Niger": "Africa/Niamey",
    "Nigeria": "Africa/Lagos",
    "Rwanda": "Africa/Kigali",
    "Sao Tome and Principe": "Africa/Sao_Tome",
    "Senegal": "Africa/Dakar",
    "Seychelles": "Indian/Mahe",
    "Sierra Leone": "Africa/Freetown",
    "Somalia": "Africa/Mogadishu",
    "South Africa": "Africa/Johannesburg",
    "South Sudan": "Africa/Juba",
    "Sudan": "Africa/Khartoum",
    "Tanzania": "Africa/Dar_es_Salaam",
    "Togo": "Africa/Lome",
    "Tunisia": "Africa/Tunis",
    "Uganda": "Africa/Kampala",
    "Zambia": "Africa/Lusaka",
    "Zimbabwe": "Africa/Harare",
    "Western Sahara": "Africa/El_Aaiun",
    # Asia
    "Afghanistan": "Asia/Kabul",
    "Armenia": "Asia/Yerevan",
    "Azerbaijan": "Asia/Baku",
    "Bahrain": "Asia/Bahrain",
    "Bangladesh": "Asia/Dhaka",
    "Bhutan": "Asia/Thimphu",
    "Brunei": "Asia/Brunei",
    "Myanmar": "Asia/Yangon",
    "Cambodia": "Asia/Phnom_Penh",
    "China": "Asia/Shanghai",
    "Cyprus": "Asia/Nicosia",
    "East Timor": "Asia/Dili",
    "Georgia": "Asia/Tbilisi",
    "India": "Asia/Kolkata",
    "Indonesia": "Asia/Jakarta",
    "Iran": "Asia/Tehran",
    "Iraq": "Asia/Baghdad",
    "Israel": "Asia/Jerusalem",
    "Japan": "Asia/Tokyo",
    "Jordan": "Asia/Amman",
    "Kazakhstan": "Asia/Almaty",
    "Kuwait": "Asia/Kuwait",
    "Kyrgyzstan": "Asia/Bishkek",
    "Laos": "Asia/Vientiane",
    "Lebanon": "Asia/Beirut",
    "Malaysia": "Asia/Kuala_Lumpur",
    "Maldives": "Indian/Maldives",
    "Mongolia": "Asia/Ulaanbaatar",
    "Nepal": "Asia/Kathmandu",
    "North Korea": "Asia/Pyongyang",
    "Oman": "Asia/Muscat",
    "Pakistan": "Asia/Karachi",
    "Palestine": "Asia/Gaza",
    "Philippines": "Asia/Manila",
    "Qatar": "Asia/Qatar",
    "Saudi Arabia": "Asia/Riyadh",
    "Singapore": "Asia/Singapore",
    "South Korea": "Asia/Seoul",
    "Sri Lanka": "Asia/Colombo",
    "Syria": "Asia/Damascus",
    "Taiwan": "Asia/Taipei",
    "Tajikistan": "Asia/Dushanbe",
    "Thailand": "Asia/Bangkok",
    "Turkey": "Europe/Istanbul",
    "Turkmenistan": "Asia/Ashgabat",
    "United Arab Emirates": "Asia/Dubai",
    "Uzbekistan": "Asia/Tashkent",
    "Vietnam": "Asia/Ho_Chi_Minh",
    "Yemen": "Asia/Aden",
    # Europe
    "Albania": "Europe/Tirane",
    "Andorra": "Europe/Andorra",
    "Austria": "Europe/Vienna",
    "Belarus": "Europe/Minsk",
    "Belgium": "Europe/Brussels",
    "Bosnia and Herzegovina": "Europe/Sarajevo",
    "Bulgaria": "Europe/Sofia",
    "Croatia": "Europe/Zagreb",
    "Czech Republic": "Europe/Prague",
    "Denmark": "Europe/Copenhagen",
    "Estonia": "Europe/Tallinn",
    "Finland": "Europe/Helsinki",
    "France": "Europe/Paris",
    "Germany": "Europe/Berlin",
    "Greece": "Europe/Athens",
    "Hungary": "Europe/Budapest",
    "Iceland": "Atlantic/Reykjavik",
    "Ireland": "Europe/Dublin",
    "Italy": "Europe/Rome",
    "Kosovo": "Europe/Belgrade",
    "Latvia": "Europe/Riga",
    "Liechtenstein": "Europe/Vaduz",
    "Lithuania": "Europe/Vilnius",
    "Luxembourg": "Europe/Luxembourg",
    "Malta": "Europe/Malta",
    "Moldova": "Europe/Chisinau",
    "Monaco": "Europe/Monaco",
    "Montenegro": "Europe/Podgorica",
    "Netherlands": "Europe/Amsterdam",
    "North Macedonia": "Europe/Skopje",
    "Norway": "Europe/Oslo",
    "Poland": "Europe/Warsaw",
    "Portugal": "Europe/Lisbon",
    "Romania": "Europe/Bucharest",
    "Russia": "Europe/Moscow",
    "San Marino": "Europe/San_Marino",
    "Serbia": "Europe/Belgrade",
    "Slovakia": "Europe/Bratislava",
    "Slovenia": "Europe/Ljubljana",
    "Spain": "Europe/Madrid",
    "Sweden": "Europe/Stockholm",
    "Switzerland": "Europe/Zurich",
    "Ukraine": "Europe/Kyiv",
    "United Kingdom": "Europe/London",
    "UK": "Europe/London",
    "Vatican City": "Europe/Vatican",
    # North America
    "Antigua and Barbuda": "America/Antigua",
    "Bahamas": "America/Nassau",
    "Barbados": "America/Barbados",
    "Belize": "America/Belize",
    "Canada": "America/Toronto",
    "Costa Rica": "America/Costa_Rica",
    "Cuba": "America/Havana",
    "Dominica": "America/Dominica",
    "Dominican Republic": "America/Santo_Domingo",
    "El Salvador": "America/El_Salvador",
    "Grenada": "America/Grenada",
    "Guatemala": "America/Guatemala",
    "Haiti": "America/Port-au-Prince",
    "Honduras": "America/Tegucigalpa",
    "Jamaica": "America/Jamaica",
    "Mexico": "America/Mexico_City",
    "Nicaragua": "America/Managua",
    "Panama": "America/Panama",
    "Saint Kitts and Nevis": "America/St_Kitts",
    "Saint Lucia": "America/St_Lucia",
    "Saint Vincent and the Grenadines": "America/St_Vincent",
    "Trinidad and Tobago": "America/Port_of_Spain",
    "United States": "America/New_York",
    "USA": "America/New_York",
    # South America
    "Argentina": "America/Buenos_Aires",
    "Bolivia": "America/La_Paz",
    "Brazil": "America/Sao_Paulo",
    "Chile": "America/Santiago",
    "Colombia": "America/Bogota",
    "Ecuador": "America/Guayaquil",
    "Guyana": "America/Guyana",
    "Paraguay": "America/Asuncion",
    "Peru": "America/Lima",
    "Suriname": "America/Paramaribo",
    "Uruguay": "America/Montevideo",
    "Venezuela": "America/Caracas",
    # Oceania
    "Australia": "Australia/Sydney",
    "Fiji": "Pacific/Fiji",
    "Kiribati": "Pacific/Tarawa",
    "Marshall Islands": "Pacific/Majuro",
    "Micronesia": "Pacific/Chuuk",
    "Nauru": "Pacific/Nauru",
    "New Zealand": "Pacific/Auckland",
    "Palau": "Pacific/Palau",
    "Papua New Guinea": "Pacific/Port_Moresby",
    "Samoa": "Pacific/Apia",
    "Solomon Islands": "Pacific/Guadalcanal",
    "Tonga": "Pacific/Tongatapu",
    "Tuvalu": "Pacific/Funafuti",
    "Vanuatu": "Pacific/Efate",
    # Additional dependencies / commonly used
    "Puerto Rico": "America/Puerto_Rico",
    "Hong Kong": "Asia/Hong_Kong",
    "Macau": "Asia/Macau",
    "Kosovo": "Europe/Belgrade",  # de facto, uses CET
    "South Sudan": "Africa/Juba",
    "Montenegro": "Europe/Podgorica",
    "Serbia": "Europe/Belgrade",
}
