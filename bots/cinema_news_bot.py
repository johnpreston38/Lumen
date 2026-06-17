import html
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

ssl._create_default_https_context = ssl._create_unverified_context

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "105015582")
SEND_TIME = os.getenv("REPORT_SEND_TIME", "09:00")
CHECK_INTERVAL_SECONDS = int(os.getenv("REPORT_CHECK_INTERVAL_SECONDS", "900"))
LAST_SENT_FILE = Path(__file__).with_suffix(".last_sent_day")

RF_SOURCES = [
    ("Kinobusiness", "https://www.kinobusiness.com/", "html"),
    ("Kinometro", "https://www.kinometro.ru/", "html"),
    ("Proficinema", "https://www.proficinema.com/", "html"),
]

WORLD_SOURCES = [
    ("Boxoffice Pro", "https://www.boxofficepro.com/feed/", "rss"),
    ("Deadline", "https://deadline.com/v/film/feed/", "rss"),
    ("Hollywood Reporter", "https://www.hollywoodreporter.com/c/movies/movie-news/feed/", "rss"),
    ("Screen Daily", "https://www.screendaily.com/feed/", "rss"),
]

IMPORTANT_RF = [
    "кассов", "сбор", "бокс-офис", "посещаем", "кинотеатр", "киносеть",
    "зал", "экран", "билет", "цена", "пушкинск", "imax", "premium",
    "премиум", "бар", "попкорн", "комбо", "минкульт", "фонд кино",
    "откры", "закры", "конкур", "акци", "технолог",
]

STRONG_RF = [
    "россии", "россия", "снг", "россий", "отечествен", "кинобизнес",
    "кинотеатр", "киносеть", "пушкинск", "минкульт", "фонд кино",
    "руб", "₽", "посещаем", "билет", "прокат", "кассовые сборы в россии",
]

IMPORTANT_WORLD = [
    "amc", "cinemark", "cineworld", "regal", "imax", "premium", "plf",
    "box office", "theater", "theatre", "exhibition", "exhibitor",
    "ticket", "pricing", "concession", "popcorn", "f&b", "streaming",
    "ai", "technology", "dynamic pricing", "admissions",
]

STRONG_WORLD = [
    "amc", "cinemark", "cineworld", "regal", "imax", "premium", "plf",
    "box office", "theater", "theatre", "exhibition", "exhibitor",
    "ticket", "pricing", "concession", "dynamic pricing", "admissions",
]

EXCLUDE = [
    "lawsuit", "legal bills", "cannes", "festival", "award", "awards",
    "oscar", "golden globes", "interview", "casting", "trailer", "book",
    "novel", "съемк", "фоторепортаж", "премия", "фестиваль", "интервью",
    "трейлер", "кастинг",
    "harassment", "complaint", "lawsuit", "actor", "actress",
    "великобритания:", "германия:", "австралия:", "сша:", "китай:",
    "франция:", "испания:", "италия:", "япония:",
]

GENRES = {
    "mario": "семейное / анимация",
    "hoppers": "семейное / анимация",
    "animal": "семейное / анимация",
    "shrek": "семейное / анимация",
    "michael": "биографический музыкальный фильм",
    "devil wears prada": "комедия / женская аудитория",
    "mortal kombat": "экшен / игровая аудитория",
    "mummy": "хоррор / приключения",
    "scream": "хоррор",
    "deep water": "триллер",
    "project hail mary": "фантастика / приключения",
    "drama": "драма",
}


def get_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def clean_text(text):
    text = re.sub(r"<script.*?</script>", " ", text or "", flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<.*?>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def absolute_url(base_url, href):
    if href.startswith("http"):
        return href

    protocol, rest = base_url.split("://", 1)
    domain = rest.split("/", 1)[0]

    if href.startswith("/"):
        return f"{protocol}://{domain}{href}"

    return base_url.rstrip("/") + "/" + href


def money(value):
    if value is None:
        return "н/д"

    value = float(value)

    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f} млрд"

    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f} млн"

    if value >= 1_000:
        return f"${value / 1_000:.0f} тыс."

    return f"${value:.0f}"


def parse_number(text):
    if not text:
        return None

    digits = re.sub(r"[^\d]", "", text)

    if not digits:
        return None

    return int(digits)


def rub_money(value):
    if value is None:
        return "н/д"

    value = float(value)

    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f} млрд ₽".replace(".", ",")

    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f} млн ₽".replace(".", ",")

    return f"{value:,.0f} ₽".replace(",", " ")


def format_count(value):
    if value is None:
        return "н/д"

    return f"{int(value):,}".replace(",", " ")


def percent(value):
    if value is None:
        return "новинка"

    sign = "+" if value > 0 else ""
    return f"{sign}{value}%"


def genre_for(title):
    title_lower = title.lower()

    for key, genre in GENRES.items():
        if key in title_lower:
            return genre

    return "массовый релиз"


def collect_world_box_office():
    report = json.loads(get_url("https://boxofficewatch.com/data/report.json").decode("utf-8"))
    yearly = json.loads(get_url("https://boxofficewatch.com/data/yearly.json").decode("utf-8"))
    worldwide_totals = {}

    for item in yearly.get(str(datetime.now().year), {}).get("worldwide", []):
        worldwide_totals[item.get("movie", "").lower()] = item.get("totalGross")

    movies = []

    for index, item in enumerate(report.get("estimates", [])[:10], start=1):
        title = item.get("movie", "Без названия")
        total = worldwide_totals.get(title.lower()) or item.get("total")
        movies.append({
            "rank": index,
            "title": title,
            "weekend": item.get("gross"),
            "total": total,
            "change": item.get("change"),
            "genre": genre_for(title),
        })

    return {
        "dates": report.get("dates", "последний доступный период"),
        "movies": movies,
    }


def post_form_json(url, payload):
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://ekinobilet.fond-kino.ru/",
        },
    )

    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))


def fond_kino_post(endpoint, payload):
    return post_form_json(f"https://ekinobilet.fond-kino.ru{endpoint}", payload)


def iso_date(value):
    return value.strftime("%Y-%m-%d")


def display_date(value):
    return value.strftime("%d.%m.%Y")


def display_short_period(start, end):
    return f"{start.strftime('%d.%m')} - {end.strftime('%d.%m')}"


def weekday_ru(value):
    names = {
        0: "понедельник",
        1: "вторник",
        2: "среда",
        3: "четверг",
        4: "пятница",
        5: "суббота",
        6: "воскресенье",
    }
    return names[value.weekday()]


def latest_complete_day():
    return datetime.now().date() - timedelta(days=1)


def latest_complete_week():
    today = datetime.now().date()
    current_week_start = today - timedelta(days=today.weekday())
    previous_week_start = current_week_start - timedelta(days=7)
    previous_week_end = current_week_start - timedelta(days=1)
    return previous_week_start, previous_week_end


def fond_kino_stats(start, end):
    data = fond_kino_post(
        "/ekb/general-stats/",
        {"periodStart": iso_date(start), "periodEnd": iso_date(end)},
    )

    for item in data:
        if item.get("periodId") == "TOTAL":
            return item

    return data[0] if data else {}


def calc_change(current, previous):
    if current is None or not previous:
        return None

    if current > 1_000_000 and previous < current * 0.1:
        return None

    return round((current - previous) / previous * 100, 2)


def fond_kino_top_films(start, end, previous_start=None, previous_end=None, limit=10):
    movies = fond_kino_post(
        "/ekb/top-films/",
        {"periodStart": iso_date(start), "periodEnd": iso_date(end)},
    )
    previous = {}

    if previous_start and previous_end:
        previous_movies = fond_kino_post(
            "/ekb/top-films/",
            {"periodStart": iso_date(previous_start), "periodEnd": iso_date(previous_end)},
        )
        previous = {item.get("id"): item.get("sum") for item in previous_movies}

    result = []

    for index, item in enumerate(movies[:limit], start=1):
        prev_sum = previous.get(item.get("id"))
        result.append({
            "rank": index,
            "title": item.get("title", "Без названия"),
            "weekend": item.get("sum"),
            "change": calc_change(item.get("sum"), prev_sum),
            "screens": item.get("sessions"),
            "total": item.get("money") or item.get("money0"),
            "viewers": item.get("quantity"),
        })

    return result


def collect_fond_kino_box_office():
    week_start, week_end = latest_complete_week()
    previous_week_start = week_start - timedelta(days=7)
    previous_week_end = week_end - timedelta(days=7)

    week_stats = fond_kino_stats(week_start, week_end)
    previous_week_stats = fond_kino_stats(previous_week_start, previous_week_end)
    movies = fond_kino_top_films(
        week_start,
        week_end,
        previous_week_start,
        previous_week_end,
        limit=10,
    )

    week_change = calc_change(week_stats.get("sum"), previous_week_stats.get("sum"))

    return {
        "daily": None,
        "weekend": {
            "title": "ЕАИС Фонд кино",
            "source": "ЕАИС Фонд кино",
            "summary": {
                "period": display_short_period(week_start, week_end),
                "gross": week_stats.get("sum"),
                "change": week_change,
                "films_count": None,
                "leader": movies[0]["title"] if movies else None,
            },
            "movies": movies,
            "link": "https://ekinobilet.fond-kino.ru/",
        },
    }


def extract_cells(row_html):
    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.I | re.S)
    return [clean_text(cell) for cell in cells]


def latest_kinobusiness_cards():
    page = get_url("https://www.kinobusiness.com/").decode("utf-8", errors="ignore")
    pattern = re.compile(
        r"<h4[^>]*class=[\"']news__title[\"'][^>]*>\s*"
        r"<a\s+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>\s*</h4>\s*"
        r"<span[^>]*class=[\"']news__description[\"'][^>]*>(.*?)</span>",
        re.I | re.S,
    )
    cards = []

    for href, title_html, description_html in pattern.findall(page):
        cards.append({
            "title": clean_text(title_html),
            "description": clean_text(description_html),
            "link": absolute_url("https://www.kinobusiness.com/", href),
        })

    return cards


def extract_daily_rf_report(card):
    page = get_url(card["link"]).decode("utf-8", errors="ignore")
    text = clean_text(page)
    start = text.find("МОСКВА")

    if start >= 0:
        text = text[start:]

    period_match = re.search(r"за\s+(.+?)(?:\s+\(|$)", card["title"], flags=re.I)
    period = period_match.group(1) if period_match else card["title"]
    gross_match = re.search(r"(\d+(?:[,.]\d+)?)\s*(?:млн|миллион)", text, flags=re.I)
    sessions_match = re.search(r"([\d\s]+)\s+сеанс", text, flags=re.I)
    viewers_match = re.search(r"посетили(?:\s+\w+)?\s+([\d\s]+)\s+человек", text, flags=re.I)
    change_match = re.search(
        r"Это\s+на\s+(\d+)%\s+(меньше|больше),\s+чем\s+было\s+(.+?)(?:\s+и\s+на|\.)",
        text,
        flags=re.I,
    )
    leader_match = re.search(r"вершину[^\"«]*(?:\"|«)([^\"»]+)(?:\"|»)", text, flags=re.I)

    gross = None
    if gross_match:
        gross = float(gross_match.group(1).replace(",", ".")) * 1_000_000

    change = None
    if change_match:
        direction = "ниже" if change_match.group(2).lower() == "меньше" else "выше"
        comparison = change_match.group(3).strip()
        comparison_map = {
            "в понедельник": "понедельником",
            "во вторник": "вторником",
            "в среду": "средой",
            "в четверг": "четвергом",
            "в пятницу": "пятницей",
            "в субботу": "субботой",
            "в воскресенье": "воскресеньем",
        }
        comparison_text = comparison_map.get(comparison.lower(), comparison)
        change = f"{direction} на {change_match.group(1)}% по сравнению с {comparison_text}"

    return {
        "period": period,
        "gross": gross,
        "sessions": parse_number(sessions_match.group(1)) if sessions_match else None,
        "viewers": parse_number(viewers_match.group(1)) if viewers_match else None,
        "change": change,
        "leader": leader_match.group(1) if leader_match else None,
        "link": card["link"],
    }


def extract_weekend_link(card):
    page = get_url(card["link"]).decode("utf-8", errors="ignore")
    match = re.search(r"https://www\.kinobusiness\.com/kassovye_sbory/weekend/\d{4}/\d{2}\.\d{2}\.\d{4}/", page)
    return match.group(0) if match else card["link"]


def extract_weekend_rf_report(card):
    link = extract_weekend_link(card)
    page = get_url(link).decode("utf-8", errors="ignore")
    tables = re.findall(r"<table[^>]*>.*?</table>", page, flags=re.I | re.S)
    summary = None
    movies = []

    for table in tables:
        table_text = clean_text(table).lower()

        if "дата уик-энда" in table_text and "общие сборы" in table_text:
            rows = re.findall(r"<tr[^>]*>.*?</tr>", table, flags=re.I | re.S)

            for row in rows:
                cells = extract_cells(row)

                if len(cells) >= 5 and re.match(r"\d{2}\.\d{2}\s*-\s*\d{2}\.\d{2}", cells[0]):
                    summary = {
                        "period": cells[0],
                        "gross": parse_number(cells[1]),
                        "change": cells[2],
                        "films_count": parse_number(cells[3]),
                        "leader": cells[4],
                    }
                    break

        if 'id="krestable"' in table or "id='krestable'" in table:
            rows = re.findall(r"<tr[^>]*id=[\"'][^\"']+[\"'][^>]*>.*?</tr>", table, flags=re.I | re.S)

            for row in rows[:10]:
                cells = extract_cells(row)

                if len(cells) < 13:
                    continue

                movies.append({
                    "rank": cells[1],
                    "title": cells[3],
                    "weekend": parse_number(cells[6]),
                    "change": cells[7],
                    "screens": parse_number(cells[8]),
                    "total": parse_number(cells[11]),
                    "viewers": parse_number(cells[12]),
                })

    return {
        "title": card["title"],
        "source": "Kinobusiness",
        "summary": summary,
        "movies": movies,
        "link": link,
    }


def collect_rf_box_office():
    try:
        return collect_fond_kino_box_office()
    except Exception as error:
        print(f"ЕАИС Фонд кино временно недоступен, использую резервный источник: {error}", flush=True)

    cards = latest_kinobusiness_cards()
    daily_report = None
    weekend_report = None

    for card in cards:
        title_lower = card["title"].lower()

        try:
            if not daily_report and "кассовые сборы в россии и снг за" in title_lower:
                daily_report = extract_daily_rf_report(card)
            elif not weekend_report and "полная касса уик" in title_lower:
                weekend_report = extract_weekend_rf_report(card)
        except Exception as error:
            print(f"Не удалось разобрать сборы РФ: {card['title']} ({error})", flush=True)

        if daily_report and weekend_report:
            break

    return {
        "daily": daily_report,
        "weekend": weekend_report,
    }


def parse_rss(url, source_name):
    data = get_url(url)
    root = ET.fromstring(data)
    items = []

    for item in root.iter():
        tag = item.tag.split("}")[-1]

        if tag not in ("item", "entry"):
            continue

        title = ""
        description = ""
        link = ""

        for child in item:
            child_tag = child.tag.split("}")[-1]

            if child_tag == "title":
                title = clean_text(child.text or "")
            elif child_tag in ("description", "summary", "content"):
                description = clean_text(child.text or "")
            elif child_tag == "link":
                link = (child.text or child.attrib.get("href", "") or "").strip()

        if title and link:
            items.append({
                "source": source_name,
                "title": title,
                "description": description,
                "link": link,
            })

    return items


def parse_html_links(url, source_name, limit=80):
    page = get_url(url).decode("utf-8", errors="ignore")
    page = re.sub(r"<script.*?</script>", " ", page, flags=re.I | re.S)
    page = re.sub(r"<style.*?</style>", " ", page, flags=re.I | re.S)
    pattern = re.compile(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
    items = []
    seen = set()

    for match in pattern.finditer(page):
        href = match.group(1).strip()
        title = clean_text(match.group(2))

        if not allowed_link(source_name, href):
            continue

        if bad_title(title):
            continue

        if len(title) < 8 or title.lower() in seen:
            continue

        start = max(0, match.start() - 220)
        end = min(len(page), match.end() + 260)
        description = clean_text(page[start:end])
        seen.add(title.lower())
        items.append({
            "source": source_name,
            "title": title,
            "description": description,
            "link": absolute_url(url, href),
        })

        if len(items) >= limit:
            break

    return items


def allowed_link(source_name, href):
    if source_name == "Kinobusiness":
        return href.startswith("/news/") or "/news/" in href

    if source_name == "Kinometro":
        return (
            href.startswith("/news/show/")
            or href.startswith("/forecast/show/")
            or href.startswith("/analytics/show/")
            or href.startswith("/kassovye-sbory")
            or href.startswith("/prebox")
        )

    if source_name == "Proficinema":
        return href.startswith("/news/detail.php") or "/news/detail.php" in href

    return True


def bad_title(title):
    title_lower = title.strip().lower()

    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", title_lower):
        return True

    return title_lower in {
        "новости", "кассовые сборы", "график премьер", "рецензии",
        "технологии", "в россии", "читать далее", "читать дальше",
    }


def collect_items(sources):
    items = []

    for source_name, url, source_type in sources:
        try:
            if source_type == "rss":
                items.extend(parse_rss(url, source_name))
            else:
                items.extend(parse_html_links(url, source_name))
        except Exception as error:
            print(f"Источник временно недоступен: {source_name} ({error})", flush=True)

    return dedupe_items(items)


def dedupe_items(items):
    result = []
    seen = set()

    for item in items:
        key = item["link"] or item["title"].lower()

        if key in seen:
            continue

        seen.add(key)
        result.append(item)

    return result


def is_important(item, keywords, strong_keywords=None):
    text = f"{item['title']} {item.get('description', '')}".lower()

    if any(word in text for word in EXCLUDE):
        return False

    if strong_keywords and not any(word in text for word in strong_keywords):
        return False

    return any(word in text for word in keywords)


def score_item(item, keywords):
    text = f"{item['title']} {item.get('description', '')}".lower()
    score = sum(1 for word in keywords if word in text)

    if re.search(r"\d+(?:[,.]\d+)?\s?(?:млн|млрд|%|руб|₽|\\$|million|billion)", text):
        score += 3

    if item["source"] in ("Kinobusiness", "Kinometro", "Proficinema", "Boxoffice Pro"):
        score += 2

    return score


def top_news(items, keywords, limit):
    strong = None

    if keywords is IMPORTANT_WORLD:
        strong = STRONG_WORLD
    elif keywords is IMPORTANT_RF:
        strong = STRONG_RF

    filtered = [item for item in items if is_important(item, keywords, strong)]
    filtered.sort(key=lambda item: score_item(item, keywords), reverse=True)
    return filtered[:limit]


def short_news_line(item):
    title = item["title"].rstrip(".")
    return f"• {title}. Источник: {item['source']}\n{item['link']}"


def box_office_section(box_office):
    message = "1. ТОП-10 фильмов мирового бокс-офиса\n"
    message += f"Период: {box_office['dates']}\n\n"

    for movie in box_office["movies"]:
        message += (
            f"{movie['rank']}. {movie['title']} — за период {money(movie['weekend'])}; "
            f"всего {money(movie['total'])}; динамика {percent(movie['change'])}.\n"
        )

    return message + "\n"


def change_text(value):
    if value in (None, "", "-"):
        return "новинка"

    cleaned = str(value).strip().replace(",", ".")

    if cleaned.startswith("-") or cleaned.startswith("+"):
        return f"{cleaned}%".replace(".", ",")

    if re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        return f"+{cleaned}%".replace(".", ",")

    return str(value).strip()


def rf_box_office_section(rf_box_office):
    message = "2. ТОП-10 фильмов РФ за прошлую неделю\n"
    weekend = rf_box_office.get("weekend")

    if not weekend:
        return message + "Свежие кассовые данные РФ сейчас не удалось получить из открытых источников.\n\n"

    if weekend and weekend.get("summary"):
        summary = weekend["summary"]
        message += (
            f"Период: {summary['period']}\n"
            f"Итого рынок: {rub_money(summary['gross'])}; "
            f"динамика {change_text(summary['change'])}"
        )

        if summary.get("films_count"):
            message += f"; фильмов в прокате {format_count(summary['films_count'])}"

        message += ".\n"

        if weekend.get("movies"):
            message += "\n"

            for movie in weekend["movies"][:10]:
                message += (
                    f"{movie['rank']}. {movie['title']} — "
                    f"{rub_money(movie['weekend'])} / {rub_money(movie['total'])}; "
                    f"динамика {change_text(movie['change'])}.\n"
                )

            message += "\n"

        message += f"Источник: {weekend.get('source', 'ЕАИС Фонд кино')}\n{weekend['link']}\n\n"

    return message


def news_section(title, items, empty_text):
    message = f"{title}\n"

    if not items:
        return message + f"{empty_text}\n\n"

    for item in items:
        message += short_news_line(item) + "\n\n"

    return message


def lumen_actions(box_office, rf_news, world_news):
    titles = " ".join(movie["title"].lower() for movie in box_office["movies"][:5])
    actions = [
        "Под топ-релизы дня поставить проверку расписания: прайм-тайм, большие залы, premium-места, отсутствие слабых сеансов в пиковые часы.",
        "Percap: на кассе и в онлайне первым предложением держать комбо под топ-2 фильма, а не отдельный попкорн/напиток.",
        "Средний чек: выделить лучшие места как отдельную ценность — название, цена, описание выгоды, обучение кассиров.",
        "Загрузка: в городах с просадкой дать короткие локальные офферы на дневные и будние сеансы, не трогая вечерний прайм.",
        "Сервис: проверить отзывы за 24 часа и быстро закрыть повторяющиеся жалобы по чистоте, очередям, бару и температуре в залах.",
    ]

    if any(word in titles for word in ("mario", "hoppers", "animal", "shrek")):
        actions.append("Семейные релизы: усилить дневные слоты, детские комбо и партнерства с детскими центрами/школами.")

    if any(word in titles for word in ("mortal", "mummy", "scream", "deep water")):
        actions.append("Жанры экшен/хоррор: продавать вечерние показы, большие напитки, снеки для компаний и late-night промо.")

    if any(is_important(item, ["imax", "premium", "plf", "премиум"]) for item in rf_news + world_news):
        actions.append("Premium: проверить загрузку premium-мест по каждому кинотеатру и поднять цену только там, где загрузка стабильно высокая.")

    return actions[:7]


def today_tasks():
    return [
        "Снять топ-10 кинотеатров по Percap и 10 худших: дать управляющим конкретную цель на сегодня.",
        "Проверить расписание топ-релизов на вечер: слабые сеансы заменить или перенести в меньшие залы.",
        "Проверить наличие комбо и скорость бара в пиковые часы.",
        "Собрать отзывы за вчера и закрыть 3 повторяющиеся проблемы.",
        "Выбрать один город для теста цены/комбо/локальной акции на ближайшие 48 часов.",
    ]


def build_report():
    today = datetime.now().strftime("%d.%m.%Y")
    box_office = collect_world_box_office()
    rf_box_office = collect_rf_box_office()

    message = f"🎬 КИНОБИЗНЕС — ДАЙДЖЕСТ ДНЯ\n{today}\n\n"
    message += box_office_section(box_office)
    message += rf_box_office_section(rf_box_office)

    return message.strip()


def split_message(text, max_length=3900):
    parts = []
    current = ""

    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}".strip()

        if len(candidate) <= max_length:
            current = candidate
            continue

        if current:
            parts.append(current)

        current = block

    if current:
        parts.append(current)

    return parts


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    chunks = split_message(text)

    for index, chunk in enumerate(chunks, start=1):
        payload = {
            "chat_id": CHAT_ID,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))

        if not result.get("ok"):
            raise RuntimeError(f"Telegram вернул ошибку: {result}")

        print(f"Отправлена часть {index}/{len(chunks)} в chat_id={CHAT_ID}", flush=True)


def run_once():
    message = build_report()
    print(f"Длина отчета: {len(message)} символов", flush=True)
    send_telegram_message(message)


def already_sent_today():
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        return LAST_SENT_FILE.read_text(encoding="utf-8").strip() == today
    except FileNotFoundError:
        return False


def mark_sent_today():
    LAST_SENT_FILE.write_text(datetime.now().strftime("%Y-%m-%d"), encoding="utf-8")


def next_send_datetime():
    hour, minute = map(int, SEND_TIME.split(":"))
    now = datetime.now()
    next_send = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if next_send <= now:
        next_send += timedelta(days=1)

    return next_send


def should_send_now():
    hour, minute = map(int, SEND_TIME.split(":"))
    now = datetime.now()
    planned = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now >= planned and not already_sent_today()


def run_daily():
    while True:
        if not should_send_now():
            wait_seconds = min(
                CHECK_INTERVAL_SECONDS,
                max(1, int((next_send_datetime() - datetime.now()).total_seconds())),
            )
            print(f"Следующая проверка расписания: через {wait_seconds // 60} мин.", flush=True)
            time.sleep(wait_seconds)
            continue

        try:
            run_once()
            mark_sent_today()
        except Exception as error:
            print(f"Ошибка при отправке отчета: {error}", flush=True)
            time.sleep(60 * 10)


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_once()
    elif "--preview" in sys.argv:
        print(build_report())
    else:
        run_daily()
