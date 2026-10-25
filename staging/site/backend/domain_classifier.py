from functools import lru_cache
import hashlib
import math
import re


TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
HASH_SIZE = 1024


KEYWORD_RULES = (
    (
        "Порносайты",
        (
            "porn", "porno", "pornhub", "xvideos", "xnxx", "xhamster", "redtube",
            "youporn", "tube8", "spankbang", "adult", "xxx", "sex", "sexy",
            "nude", "nudes", "naked", "erotic", "erotica", "hentai", "rule34",
            "nhentai", "hanime", "camgirl", "cams", "webcam", "livejasmin",
            "chaturbate", "bongacams", "stripchat", "onlyfans", "fansly",
            "brazzers", "bangbros", "milf", "anal", "bdsm", "fetish", "escort",
            "escorts", "amateur", "hardcore", "gayporn", "gaytube", "lesbian",
            "transporn", "shemale", "twink", "bearwww", "granny", "boobs",
            "pussy", "dick", "cock", "cum", "blowjob", "handjob", "orgasm",
            "deepthroat", "threesome", "fisting", "jav", "javguru", "javhd",
            "javlibrary", "javbus", "xhamsterlive", "xhamsterlive.desi",
            "redgifs", "erome", "eporner", "porntrex", "drtuber", "tnaflix",
            "nuvid", "beeg", "adultfriendfinder", "ashleymadison",
            "секс", "порно", "эротик", "эротика", "голые", "интим",
        ),
    ),
    (
        "Знакомства",
        (
            "tinder", "gotinder", "bumble", "badoo", "mamba", "loveplanet",
            "love.mail", "dating", "date", "match", "okcupid", "hinge",
            "happn", "pure", "feeld", "her", "grindr", "hornet", "scruff",
            "planetromeo", "romeo", "adam4adam", "gaydar", "jackd", "taimi",
            "wapo", "wapa", "meetme", "zoosk", "lovoo", "plentyoffish",
            "pof", "maturedating", "rusdate", "teamo", "fotostrana",
            "знаком", "знакомства", "свидан", "мамба", "тиндер",
        ),
    ),
    (
        "Еда",
        (
            "burgerking", "burger-king", "bk", "mcdonalds", "mcdonald",
            "vkusnoitochka", "vkusno-i-tochka", "vkusno", "kfc", "rostics",
            "dodopizza", "dodo", "dominos", "pizzahut", "papajohns",
            "yandexeda", "eda.yandex", "deliveryclub", "delivery-club",
            "samokat", "lavka", "sbermarket", "kuper", "ubereats",
            "doordash", "glovo", "wolt", "food", "pizza", "sushi",
            "sushivesla", "yakitoriya", "tanuki", "teremok", "kroshka",
            "eda", "restoran", "restaurant", "burger", "coffee", "kofe",
            "starbucks", "shokoladnitsa", "cofix", "вкусно", "еда",
            "бургер", "пицца", "суши", "доставкаеды",
        ),
    ),
    (
        "Азартные игры",
        (
            "casino", "bet", "betting", "poker", "slots", "jackpot", "gambling",
            "bookmaker", "букмекер", "ставки", "казино", "1xbet", "fonbet",
            "winline", "pari", "leonbets", "melbet", "pin-up", "pinup",
        ),
    ),
    (
        "Крипто",
        (
            "crypto", "bitcoin", "btc", "ethereum", "eth", "binance", "bybit",
            "okx", "coinbase", "kraken", "tonkeeper", "metamask", "wallet",
            "blockchain", "dex", "defi", "nft",
        ),
    ),
    (
        "Видео",
        (
            "youtube", "googlevideo", "ytimg", "rutube", "vkvideo", "twitch",
            "netflix", "kinopoisk", "okko", "ivi", "premier", "wink", "video",
            "stream", "movie", "film", "serial", "кино", "видео",
        ),
    ),
    (
        "Соцсети",
        (
            "instagram", "facebook", "fbcdn", "vk", "vkontakte", "tiktok",
            "twitter", "xcdn", "snapchat", "reddit", "pinterest", "linkedin",
            "social", "userapi", "okcdn",
        ),
    ),
    (
        "Мессенджеры",
        (
            "telegram", "whatsapp", "discord", "viber", "signal", "skype",
            "wechat", "messenger", "imo", "icq", "slack-msg",
        ),
    ),
    (
        "Покупки",
        (
            "shop", "store", "market", "ozon", "wildberries", "aliexpress",
            "amazon", "ebay", "avito", "lamoda", "megamarket", "shopping",
        ),
    ),
    (
        "Разработка",
        (
            "github", "gitlab", "stackoverflow", "stackexchange", "npmjs",
            "pypi", "docker", "kubernetes", "developer", "dev", "docs",
            "api", "sdk", "habr", "code", "coding", "programming",
        ),
    ),
    (
        "Реклама",
        (
            "ads", "adservice", "adserver", "adfox", "doubleclick", "banner",
            "advert", "adnxs", "adform", "adroll", "taboola", "outbrain",
            "googlesyndication", "googleadservices",
        ),
    ),
    (
        "Трекеры",
        (
            "analytics", "metric", "metrics", "counter", "tracking", "tracker",
            "telemetry", "stat", "stats", "app-measurement", "amplitude",
            "segment", "sentry", "crashlytics",
        ),
    ),
)


EXTRA_KEYWORD_RULES = (
    (
        "Порносайты",
        (
            "sexshop", "sexcam", "webcamsex", "livecam", "livecams", "camgirls",
            "xxxvideo", "xxxvideos", "sexvideo", "sexvideos", "coomer", "bunkr",
            "gaysex", "gayvideo", "gayvideos", "gayxxx", "gayharem", "gaymaletube",
            "boyfriendtv", "menatplay", "lesbianporn", "transvideos", "femboy",
            "twinks", "eroticvideo", "eroticvideos", "camwhores",
        ),
    ),
    (
        "Знакомства",
        (
            "badoocdn", "hornetapp", "jack'd", "raya", "coffeemeetsbagel",
            "hily", "hilyapp", "chispa", "blk", "muzz", "christianmingle",
            "jdate", "silversingles", "eharmony", "ourtime", "lex", "archer",
            "translr", "growlr", "growlrapp",
        ),
    ),
    (
        "Еда",
        (
            "dominospizza", "pizzeria", "kroshka-kartoshka", "coffeemania",
            "dostaevsky", "ginza", "ginzadelivery", "vkusvill", "azbukavkusa",
            "perekrestok", "pyaterochka", "lenta", "magnit", "foodfox",
        ),
    ),
    ("Музыка", ("spotify", "soundcloud", "deezer", "tidal", "zvuk", "bandcamp", "music.yandex", "music.apple", "audio", "music")),
    ("AI", ("chatgpt", "openai", "claude", "anthropic", "gemini", "copilot", "deepseek", "perplexity", "poe", "grok", "ai")),
    ("Поиск", ("google", "yandex", "bing", "duckduckgo", "brave", "rambler", "search")),
    ("Почта", ("gmail", "googlemail", "mailru", "mail.yandex", "outlook", "hotmail", "protonmail", "proton", "imap", "smtp")),
    ("Финансы", ("sberbank", "sber", "tbank", "tinkoff", "yoomoney", "qiwi", "alfabank", "vtb", "gazprombank", "raiffeisen", "bank", "payment", "finance")),
    ("Игры", ("steam", "steampowered", "epicgames", "riotgames", "roblox", "battle.net", "blizzard", "minecraft", "playstation", "xboxlive", "unity3d", "game", "games", "gaming")),
    ("Работа", ("notion", "bitrix24", "slack", "zoom", "office", "sharepoint", "onedrive", "teams.microsoft", "atlassian", "trello", "figma", "miro", "hh", "adobe", "acrobat", "workspace", "meeting", "jira")),
    ("Облака и файлы", ("icloud", "drive.google", "docs.google", "dropbox", "box", "mega", "disk.yandex", "cloud.mail", "wetransfer", "mediafire", "airdrop", "airdroid", "cloud", "storage", "files")),
    ("Новости", ("ria", "rbc", "lenta", "meduza", "tass", "kommersant", "vedomosti", "bbc", "cnn", "reuters", "theguardian", "msn", "news")),
    ("Образование", ("coursera", "udemy", "stepik", "duolingo", "wikipedia", "wikimedia", "khanacademy", "skillbox", "geekbrains", "learn", "course", "study", "education")),
    ("Карты и такси", ("maps.google", "2gis", "yandexnavi", "taxi.yandex", "uber", "city-mobil", "gett", "maps", "taxi", "nav")),
    ("Путешествия", ("booking", "airbnb", "aviasales", "tutu", "ostrovok", "aeroflot", "rzd", "tripadvisor", "travel", "hotel", "avia", "flight")),
    ("Безопасность", ("avast", "avg", "avira", "kaspersky", "eset", "bitdefender", "malwarebytes", "mcafee", "norton", "drweb", "digicert", "sectigo", "letsencrypt", "security", "antivirus", "pki", "ocsp")),
    ("CDN и инфраструктура", ("cloudflare", "cloudfront", "akamaized", "akamaihd", "fastly", "cdn77", "edgesuite", "edgekey", "azureedge", "googleusercontent", "gstatic", "ggpht", "googleapis", "appspot", "firebaseio", "firebaseapp", "amazonaws", "ampproject", "cdn", "edge")),
    ("Windows и Microsoft", ("windows", "windowsupdate", "msftauth", "msftconnecttest", "msftncsi", "microsoftpersonalcontent", "microsoft", "msft")),
    ("Apple и iCloud", ("apple", "icloud", "mzstatic", "itunes", "aaplimg", "cdn-apple")),
    ("Android и Google Play", ("android", "play.googleapis", "gvt1", "gvt2", "gvt3", "googleplay")),
)

ALL_KEYWORD_RULES = KEYWORD_RULES + EXTRA_KEYWORD_RULES
DOMAIN_HINT_CATEGORIES = {"Порносайты", "Знакомства", "Еда"}
SHORT_TOKEN_ALLOWLIST = {
    "Порносайты": {"xxx", "jav", "sex", "anal"},
    "Еда": {"bk", "kfc"},
    "Крипто": {"btc", "eth"},
    "AI": {"ai"},
    "Соцсети": {"vk"},
}
AMBIGUOUS_ADULT_TOKENS = {"sex", "anal", "cum", "ass", "dick", "cock", "pussy", "boobs", "milf", "gay"}
AMBIGUOUS_DATING_TOKENS = {"date", "dating", "love", "her"}


def merge_keyword_rules(rules):
    merged = {}
    for category, words in rules:
        merged.setdefault(category, []).extend(words)
    return {category: tuple(dict.fromkeys(words)) for category, words in merged.items()}


AI_PROTOTYPES = merge_keyword_rules(ALL_KEYWORD_RULES)


def tokenize_domain(domain):
    raw = str(domain or "").lower()
    raw = raw.replace("ё", "е")
    return tuple(TOKEN_RE.findall(raw))


def normalized_domain_text(domain):
    tokens = tokenize_domain(domain)
    return " ".join(tokens), "".join(tokens)


def keyword_key(keyword):
    return "".join(TOKEN_RE.findall(str(keyword or "").lower().replace("ё", "е")))


def keyword_score(category, keyword, tokens, compact, text):
    key = keyword_key(keyword)
    if not key:
        return 0
    token_set = set(tokens)
    if len(key) <= 3 and key not in SHORT_TOKEN_ALLOWLIST.get(category, set()):
        return 0
    if len(key) <= 3:
        return 88 if key in token_set else 0
    if category == "Порносайты" and key in AMBIGUOUS_ADULT_TOKENS and key not in token_set:
        return 0
    if category == "Знакомства" and key in AMBIGUOUS_DATING_TOKENS and key not in token_set:
        return 0
    if key in token_set:
        return 98 if category in DOMAIN_HINT_CATEGORIES else 90
    raw_keyword = str(keyword).lower().replace("ё", "е")
    if raw_keyword and raw_keyword in text:
        return 94 if category in DOMAIN_HINT_CATEGORIES else 84
    if key in compact:
        return 92 if category in DOMAIN_HINT_CATEGORIES else 78
    return 0


@lru_cache(maxsize=250_000)
def classify_domain_by_keywords(domain):
    text, compact = normalized_domain_text(domain)
    tokens = tokenize_domain(domain)
    if not compact:
        return "", 0, ""
    best = ("", 0, "")
    for category, keywords in ALL_KEYWORD_RULES:
        for keyword in keywords:
            score = keyword_score(category, keyword, tokens, compact, text)
            if score > best[1]:
                best = (category, score, keyword)
    if best[1] >= 64:
        return best
    return "", 0, ""


def hashed_features(text):
    compact = "".join(TOKEN_RE.findall(str(text or "").lower().replace("ё", "е")))
    features = {}
    if not compact:
        return features
    grams = []
    for size in (3, 4, 5):
        if len(compact) >= size:
            grams.extend(compact[index:index + size] for index in range(len(compact) - size + 1))
    grams.extend(tokenize_domain(text))
    for gram in grams:
        digest = hashlib.blake2b(gram.encode("utf-8", errors="ignore"), digest_size=4).digest()
        index = int.from_bytes(digest, "little") % HASH_SIZE
        features[index] = features.get(index, 0.0) + 1.0
    norm = math.sqrt(sum(value * value for value in features.values())) or 1.0
    return {index: value / norm for index, value in features.items()}


@lru_cache(maxsize=1)
def category_weight_vectors():
    vectors = {}
    for category, prototypes in AI_PROTOTYPES.items():
        vector = {}
        for prototype in prototypes:
            for index, value in hashed_features(prototype).items():
                vector[index] = vector.get(index, 0.0) + value
        norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
        vectors[category] = {index: value / norm for index, value in vector.items()}
    return vectors


@lru_cache(maxsize=250_000)
def classify_domain_with_local_ai(domain):
    keyword_category, keyword_confidence, signal = classify_domain_by_keywords(domain)
    if keyword_category and keyword_confidence >= 78:
        return keyword_category, keyword_confidence, signal

    features = hashed_features(domain)
    if not features:
        return "", 0, ""
    scores = []
    for category, vector in category_weight_vectors().items():
        score = sum(features.get(index, 0.0) * value for index, value in vector.items())
        scores.append((score, category))
    scores.sort(reverse=True)
    if not scores:
        return "", 0, ""
    best_score, best_category = scores[0]
    second_score = scores[1][0] if len(scores) > 1 else 0.0
    confidence = int(max(0.0, min(0.99, best_score)) * 100)
    # Локальный AI используется только последним слоем и поэтому держит
    # консервативный порог: лучше оставить редкий домен в "Другое", чем
    # уверенно присвоить системному CDN неправильную категорию.
    if best_score >= 0.30 and best_score - second_score >= 0.055:
        return best_category, max(confidence, 68), "local-ai"
    if best_score >= 0.38:
        return best_category, max(confidence, 74), "local-ai"
    return "", 0, ""


def warm_local_domain_ai():
    category_weight_vectors()
    for category, words in ALL_KEYWORD_RULES:
        for word in words[:8]:
            classify_domain_by_keywords(word)
            classify_domain_with_local_ai(word)
