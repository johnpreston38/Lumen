import calendar
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


BOT_TOKEN = os.getenv("BUSINESS360_BOT_TOKEN", "")
CHAT_ID = os.getenv("BUSINESS360_CHAT_ID", "105015582")
SEND_TIME = os.getenv("BUSINESS360_SEND_TIME", "09:50")
CHECK_INTERVAL_SECONDS = int(os.getenv("BUSINESS360_CHECK_INTERVAL_SECONDS", "900"))
DATA_CHECK_INTERVAL_SECONDS = int(os.getenv("BUSINESS360_DATA_CHECK_INTERVAL_SECONDS", "600"))
TELEGRAM_SEND_ATTEMPTS = int(os.getenv("BUSINESS360_TELEGRAM_SEND_ATTEMPTS", "6"))
TELEGRAM_RETRY_DELAY_SECONDS = int(os.getenv("BUSINESS360_TELEGRAM_RETRY_DELAY_SECONDS", "30"))
TELEGRAM_REQUEST_TIMEOUT_SECONDS = int(os.getenv("BUSINESS360_TELEGRAM_REQUEST_TIMEOUT_SECONDS", "60"))
FORECAST_ADJUSTMENT_LOOKBACK_DAYS = int(os.getenv("BUSINESS360_FORECAST_ADJUSTMENT_LOOKBACK_DAYS", "14"))
FORECAST_ADJUSTMENT_DAYS = int(os.getenv("BUSINESS360_FORECAST_ADJUSTMENT_DAYS", "3"))
FORECAST_ADJUSTMENT_MIN = float(os.getenv("BUSINESS360_FORECAST_ADJUSTMENT_MIN", "0.85"))
FORECAST_ADJUSTMENT_MAX = float(os.getenv("BUSINESS360_FORECAST_ADJUSTMENT_MAX", "1.15"))
PLAN_VIEWERS_FACTOR_MIN = float(os.getenv("BUSINESS360_PLAN_VIEWERS_FACTOR_MIN", "0.75"))
PLAN_VIEWERS_FACTOR_MAX = float(os.getenv("BUSINESS360_PLAN_VIEWERS_FACTOR_MAX", "1.35"))
PLAN_REVENUE_FACTOR_MIN = float(os.getenv("BUSINESS360_PLAN_REVENUE_FACTOR_MIN", "0.80"))
PLAN_REVENUE_FACTOR_MAX = float(os.getenv("BUSINESS360_PLAN_REVENUE_FACTOR_MAX", "1.45"))
PLAN_TARGET_UPLIFT = float(os.getenv("BUSINESS360_PLAN_TARGET_UPLIFT", "0.10"))
STATE_DIR = Path(os.getenv(
    "BUSINESS360_STATE_DIR",
    "/Users/evgenijnovickij/Library/Application Support/LumenBots",
))
STATE_DIR.mkdir(parents=True, exist_ok=True)
LAST_SENT_FILE = STATE_DIR / "business360_daily_bot.last_sent_day"
UPDATE_OFFSET_FILE = STATE_DIR / "business360_daily_bot.telegram_offset"
REGISTERED_CHATS_FILE = STATE_DIR / "business360_daily_bot.registered_chats.json"
FORECAST_SNAPSHOT_FILE = STATE_DIR / "business360_daily_bot.forecast_snapshots.json"
BUSINESS360_HISTORY_FILE = STATE_DIR / "business360_daily_history.jsonl"
OCCUPANCY_HISTORY_FILE = STATE_DIR / "lumen_occupancy_history.jsonl"
BUSINESS360_COOKIE_FILE = STATE_DIR / "business360.cookie"

B360_API = "https://business-360.ru/api/monitor/"
LUMEN_SITE_API = "https://lumenfilm.com/api/v1"
LUMEN_CINEMABOX_API = "https://lumen.cinemabox.team/api/v1"
LUMEN_SITE_API_KEY = os.getenv(
    "LUMEN_SITE_API_KEY",
    "548d60ccf6dc63780ad73727dfc447a233d75a3b8c32a5dcb2298db2ded5edab",
)
RUSSIAN_HOLIDAY_MONTH_DAYS = {
    (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (1, 8),
    (2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4),
}
RUSSIAN_OBSERVED_HOLIDAYS = {
    2026: {
        "2026-01-09", "2026-03-09", "2026-05-11",
    },
}
DAY_TYPE_LABELS = {
    "holiday": "праздничным дням",
    "weekend": "выходным",
    "weekday": "будням",
}
COMPARISON_DAY_TYPE_LABELS = {
    "holiday": "праздничным дням",
    "weekend": "выходным",
    "student_promo": "акционным пн/вт",
    "weekday": "обычным будням",
}
MONTH_NAMES = {
    1: "январь",
    2: "февраль",
    3: "март",
    4: "апрель",
    5: "май",
    6: "июнь",
    7: "июль",
    8: "август",
    9: "сентябрь",
    10: "октябрь",
    11: "ноябрь",
    12: "декабрь",
}
CINEMAS = {
    2: "Советск",
    3: "Гусев",
    4: "Арзамас",
    5: "Балахна",
    6: "Балашов",
    7: "Заречный",
    8: "Калининград",
    9: "Кингисепп",
    10: "Мурманск",
    11: "Саров",
    14: "Черняховск",
    16: "Сальск",
    17: "Североморск",
}
ACTIVE_IDS = sorted(CINEMAS)
MANAGER_CITY_IDS = {
    "kiselenk": [4, 5],
    "laraero": [6],
    "nataly_sovetsk": [3, 2, 14],
    "veramakarovavm": [7],
    "nude_craft": [9],
    "iralina23": [10, 17],
    "dashamir88": [16],
    "arefeva02": [11],
}


def normalize_username(username):
    return (username or "").strip().lstrip("@").lower()


def is_owner(user_id):
    return str(user_id or "") == str(CHAT_ID)


def allowed_city_ids(username=None, user_id=None):
    if is_owner(user_id):
        return ACTIVE_IDS

    return MANAGER_CITY_IDS.get(normalize_username(username), [])


def load_registered_chats():
    try:
        return json.loads(REGISTERED_CHATS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_registered_chats(chats):
    REGISTERED_CHATS_FILE.write_text(
        json.dumps(chats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_forecast_snapshots():
    try:
        return json.loads(FORECAST_SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_forecast_snapshots(snapshots):
    FORECAST_SNAPSHOT_FILE.write_text(
        json.dumps(snapshots, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def append_jsonl(path, item):
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(item, ensure_ascii=False, sort_keys=True))
        file.write("\n")


def register_chat(username, user_id, chat_id):
    normalized = normalize_username(username)

    if not normalized or not chat_id:
        return

    chats = load_registered_chats()
    chats[normalized] = {
        "chat_id": str(chat_id),
        "user_id": str(user_id or ""),
        "username": normalized,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_registered_chats(chats)


def business360_cookie():
    cookie = os.getenv("BUSINESS360_COOKIE", "").strip()

    if cookie:
        return cookie

    try:
        return BUSINESS360_COOKIE_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def api_fetch(action, params=None):
    payload = {"action": action, **(params or {})}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json",
        "Referer": "https://business-360.ru/",
    }
    cookie = business360_cookie()

    if cookie:
        headers["Cookie"] = cookie

    request = urllib.request.Request(
        B360_API + action,
        data=data,
        method="POST",
        headers=headers,
    )

    with urllib.request.urlopen(request, timeout=90) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(result.get("error") or f"B360 API error: {action}")

    return result


def telegram_request(method, payload=None):
    if not BOT_TOKEN:
        raise ValueError("Не указан BUSINESS360_BOT_TOKEN.")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    if payload is None:
        with urllib.request.urlopen(url, timeout=TELEGRAM_REQUEST_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))
    else:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

        with urllib.request.urlopen(request, timeout=TELEGRAM_REQUEST_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(f"Telegram вернул ошибку: {result}")

    return result


def report_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "🔄 Обновить результаты", "callback_data": "refresh_report"}],
            [{"text": "📆 Результат месяца", "callback_data": "month_result"}],
            [{"text": "📊 План-факт", "callback_data": "plan_fact"}],
            [{"text": "📊 Сравнение 3 месяца", "callback_data": "three_month_compare"}],
            [{"text": "🎬 Потенциал фильмов", "callback_data": "movie_potential"}],
            [{"text": "📦 Оборачиваемость товара", "callback_data": "turnover_month"}],
            [{"text": "🏙 Выбрать город", "callback_data": "choose_city"}],
        ]
    }


def city_choice_keyboard(username=None, user_id=None):
    city_buttons = [
        {"text": CINEMAS[cinema_id], "callback_data": f"city_report:{cinema_id}"}
        for cinema_id in sorted(allowed_city_ids(username, user_id), key=lambda city_id: CINEMAS[city_id])
    ]
    city_rows = [
        city_buttons[index:index + 2]
        for index in range(0, len(city_buttons), 2)
    ]

    return {
        "inline_keyboard": city_rows + [[{"text": "⬅️ Назад", "callback_data": "back_to_menu"}]]
    }


def send_telegram_message(text, chat_id=None, with_button=True, reply_markup=None):
    chat_id = chat_id or CHAT_ID
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup
    elif with_button:
        payload["reply_markup"] = report_keyboard()

    for attempt in range(1, TELEGRAM_SEND_ATTEMPTS + 1):
        try:
            result = telegram_request("sendMessage", payload)
            message_id = result.get("result", {}).get("message_id")
            print(f"Отчет отправлен в chat_id={chat_id}, message_id={message_id}", flush=True)
            return result
        except Exception as error:
            if attempt >= TELEGRAM_SEND_ATTEMPTS:
                raise

            print(
                f"Не удалось отправить сообщение в Telegram "
                f"(попытка {attempt}/{TELEGRAM_SEND_ATTEMPTS}): {error}",
                flush=True,
            )
            time.sleep(TELEGRAM_RETRY_DELAY_SECONDS * attempt)


OWNER_BOT_COMMANDS = [
    {"command": "start", "description": "Открыть меню"},
    {"command": "report", "description": "Обновить срез сети"},
    {"command": "month", "description": "Результат месяца"},
    {"command": "planfact", "description": "План-факт месяца и года"},
    {"command": "compare3", "description": "Сравнение со средним за 3 месяца"},
    {"command": "movies", "description": "Потенциал фильмов в прокате"},
    {"command": "turnover", "description": "Оборачиваемость товара"},
    {"command": "cities", "description": "Выбрать город"},
    {"command": "occupancy", "description": "Заполняемость залов"},
    {"command": "week_forecast", "description": "Прогноз vs факт за прошлую неделю"},
    {"command": "help", "description": "Что умеет бот"},
]

MANAGER_BOT_COMMANDS = [
    {"command": "start", "description": "Запустить бота"},
    {"command": "report", "description": "Обновить срез по своему городу"},
    {"command": "help", "description": "Что умеет бот"},
]


def setup_telegram_menu():
    telegram_request("setMyCommands", {
        "commands": MANAGER_BOT_COMMANDS,
        "scope": {"type": "all_private_chats"},
        "language_code": "ru",
    })
    telegram_request("setMyCommands", {
        "commands": OWNER_BOT_COMMANDS,
        "scope": {"type": "chat", "chat_id": CHAT_ID},
        "language_code": "ru",
    })
    telegram_request("setChatMenuButton", {
        "menu_button": {"type": "commands"},
    })


def help_text(is_owner_user=False):
    if is_owner_user:
        return (
            "Команды бота:\n"
            "/report — обновить срез сети\n"
            "/month — результат сети за текущий месяц\n"
            "/planfact — план-факт месяца и года\n"
            "/compare3 — сравнение со средним за 3 месяца\n"
            "/movies — потенциал фильмов в прокате\n"
            "/turnover — динамика оборачиваемости товара за месяц\n"
            "/cities — выбрать город\n"
            "/occupancy — текущая заполняемость залов\n"
            "/week_forecast — разница прогноза и факта за прошлую неделю\n"
            "/menu — открыть кнопки\n"
            "/help — список команд\n\n"
            "Также можно писать вопрос обычным текстом, например: "
            "дай разницу за прошлую неделю по выручке между прогнозом и фактом"
        )

    return (
        "Команды бота:\n"
        "/report — обновить срез по своему городу\n"
        "/menu — открыть меню\n"
        "/help — список команд"
    )


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def date_str(value):
    return value.strftime("%Y-%m-%d")


def display_date(value):
    return value.strftime("%d.%m.%Y")


def expected_report_date(report_date=None):
    report_date = report_date or datetime.now().date()
    return report_date - timedelta(days=1)


def build_unavailable_report(report_date, expected_date, latest_date, reason=None):
    message = f"📊 ЛЮМЕН — ЕЖЕДНЕВНЫЙ СРЕЗ\n{display_date(report_date)}\n"
    message += f"Период должен быть: {display_date(expected_date)}\n\n"
    message += "⚠️ Данные за ожидаемый период еще не появились в Business360.\n"
    message += f"Последний доступный период: {display_date(latest_date)}"

    if reason:
        message += f"\n{reason}"

    message += "\n\nНажми «Обновить результаты», когда выгрузка обновится."
    return message


def ru_money(value):
    if value is None:
        return "н/д"

    return f"{round(value):,} ₽".replace(",", " ")


def ru_money_mln(value):
    if value is None:
        return "н/д"

    return f"{value / 1_000_000:.1f} млн ₽".replace(".", ",")


def ru_money_compact(value):
    if value is None:
        return "н/д"

    if abs(value) >= 1_000_000:
        return ru_money_mln(value)

    return f"{round(value / 1_000):,}к ₽".replace(",", " ")


def ru_num(value):
    if value is None:
        return "н/д"

    return f"{round(value):,}".replace(",", " ")


def ru_rub(value):
    if value is None:
        return "н/д"

    return f"{round(value):,} ₽".replace(",", " ")


def ru_pct(value, digits=1):
    if value is None:
        return "н/д"

    return f"{value * 100:.{digits}f}%".replace(".", ",")


def delta_text(current, average, lower_is_better=False, good_word=False):
    if current is None or average in (None, 0):
        return "⚪ н/д"

    delta = (current - average) / average * 100
    good = delta >= 0

    if lower_is_better:
        good = delta <= 0

    emoji = "🟢" if good else "🔴"

    if good_word:
        return f"{emoji} лучше" if good else f"{emoji} хуже"

    sign = "+" if delta > 0 else ""
    return f"{emoji} {sign}{delta:.1f}%".replace(".", ",")


def delta_plain(current, previous, lower_is_better=False):
    if current is None or previous in (None, 0):
        return "н/д"

    delta = (current - previous) / previous * 100
    good = delta >= 0

    if lower_is_better:
        good = delta <= 0

    emoji = "🟢" if good else "🔴"
    sign = "+" if delta > 0 else ""
    return f"{emoji} {sign}{delta:.1f}%".replace(".", ",")


def delta_from_values(current, previous, lower_is_better=False):
    if current is None or previous in (None, 0):
        return None

    return (current - previous) / previous


def last_year_month_bounds(month_start):
    year = month_start.year - 1
    month = month_start.month
    last_day = calendar.monthrange(year, month)[1]
    start = month_start.replace(year=year)
    end = start.replace(day=last_day)
    return start, end


def shift_year(value, years):
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def shift_months(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def month_year_label(value):
    return f"{MONTH_NAMES[value.month]} {value.year}"


def aggregate_rows(rows):
    revenue_cinema = sum(float(row.get("revenue_cinema") or 0) for row in rows)
    revenue_bar = sum(float(row.get("revenue_bar") or 0) for row in rows)
    viewers = sum(float(row.get("viewers") or 0) for row in rows)
    bar_checks = sum(float(row.get("bar_checks") or 0) for row in rows)
    foodcost_amount = sum(
        float(row.get("revenue_bar") or 0) * float(row.get("foodcost_pct") or 0)
        for row in rows
    )
    total_revenue = revenue_cinema + revenue_bar

    return {
        "revenue": total_revenue,
        "tickets_revenue": revenue_cinema,
        "bar_revenue": revenue_bar,
        "viewers": viewers,
        "percap": revenue_bar / viewers if viewers else None,
        "avg_check": total_revenue / viewers if viewers else None,
        "avg_ticket": revenue_cinema / viewers if viewers else None,
        "bar_share": revenue_bar / total_revenue if total_revenue else None,
        "bar_conversion": bar_checks / viewers if viewers else None,
        "bar_check": revenue_bar / bar_checks if bar_checks else None,
        "foodcost": foodcost_amount / revenue_bar if revenue_bar else None,
        "foodcost_amount": foodcost_amount,
        "bar_checks": bar_checks,
    }


def group_by_date(rows):
    grouped = defaultdict(list)

    for row in rows:
        grouped[row.get("dt")].append(row)

    return {day: aggregate_rows(day_rows) for day, day_rows in grouped.items()}


def average(values):
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else None


def month_average(day_metrics, target_date, key, same_day_type=False, exclude_target=False):
    target_day_type = comparison_day_type(target_date)
    values = []

    for day, metrics in day_metrics.items():
        current_date = parse_date(day)

        if exclude_target and current_date == target_date:
            continue

        if same_day_type and comparison_day_type(current_date) != target_day_type:
            continue

        values.append(metrics.get(key))

    return average(values)


def is_russian_holiday(value):
    if (value.month, value.day) in RUSSIAN_HOLIDAY_MONTH_DAYS:
        return True

    return date_str(value) in RUSSIAN_OBSERVED_HOLIDAYS.get(value.year, set())


def day_type(value):
    if is_russian_holiday(value):
        return "holiday"

    if value.weekday() >= 5:
        return "weekend"

    return "weekday"


def is_student_promo_day(value):
    return not is_russian_holiday(value) and value.weekday() in (0, 1)


def comparison_day_type(value):
    if is_russian_holiday(value):
        return "holiday"

    if value.weekday() >= 5:
        return "weekend"

    if is_student_promo_day(value):
        return "student_promo"

    return "weekday"


def day_type_label(value):
    return DAY_TYPE_LABELS[day_type(value)]


def comparison_day_type_label(value):
    return COMPARISON_DAY_TYPE_LABELS[comparison_day_type(value)]


def student_promo_note(value):
    if not is_student_promo_day(value):
        return ""

    return (
        "• Акция дня: школьники и студенты — билет до 11:59 за 250 ₽, "
        "с 12:00 до 18:00 за 310 ₽.\n"
    )


def month_day_type_counts(month_start):
    days_in_month = calendar.monthrange(month_start.year, month_start.month)[1]
    counts = defaultdict(int)

    for day_number in range(1, days_in_month + 1):
        current_date = month_start.replace(day=day_number)
        counts[day_type(current_date)] += 1

    return dict(counts)


def payroll_projection_by_day_type(staff_by_day, month_start):
    payroll_values = defaultdict(list)

    for day, staff in staff_by_day.items():
        payroll_values[day_type(parse_date(day))].append(staff.get("payroll"))

    projected = 0

    for day_kind, count in month_day_type_counts(month_start).items():
        avg_payroll = average(payroll_values.get(day_kind, [])) or 0
        projected += avg_payroll * count

    return projected


def read_period(start, end, cmp_start=None, cmp_end=None, cinemas=None):
    cinemas = cinemas or ACTIVE_IDS
    params = {
        "start": date_str(start),
        "end": date_str(end),
        "cmp_start": date_str(cmp_start or start),
        "cmp_end": date_str(cmp_end or end),
        "cinemas": ",".join(str(cinema_id) for cinema_id in cinemas),
    }
    return api_fetch("period", params)


def read_period_rows(start, end, cinemas=None):
    if end < start:
        return []

    rows = []
    current = start

    while current <= end:
        month_end = current.replace(day=calendar.monthrange(current.year, current.month)[1])
        chunk_end = min(month_end, end)
        rows.extend(read_period(current, chunk_end, cinemas=cinemas).get("rows", []))
        current = chunk_end + timedelta(days=1)

    return rows


def rows_by_city(rows):
    grouped = defaultdict(list)

    for row in rows:
        cinema_id = row_cinema_id(row)

        if cinema_id is not None:
            grouped[cinema_id].append(row)

    return grouped


def empty_plan():
    return {
        "revenue": 0,
        "viewers": 0,
        "planned_cities": 0,
        "missing_cities": [],
    }


def add_plan_item(plan, revenue, viewers, city_planned=True, city_name=None, target_uplift=True):
    uplift = 1 + PLAN_TARGET_UPLIFT if target_uplift else 1
    plan["revenue"] += (revenue or 0) * uplift
    plan["viewers"] += (viewers or 0) * uplift

    if city_planned:
        plan["planned_cities"] += 1
    elif city_name:
        plan["missing_cities"].append(city_name)


def realistic_plan_factors(as_of_date):
    year_start = as_of_date.replace(month=1, day=1)

    if as_of_date < year_start:
        return {}, {"viewers": 1.0, "revenue": 1.0}

    actual_rows = read_period_rows(year_start, as_of_date)
    base_rows = read_period_rows(shift_year(year_start, -1), shift_year(as_of_date, -1))
    actual_by_city = rows_by_city(actual_rows)
    base_by_city = rows_by_city(base_rows)
    actual_network = aggregate_rows(actual_rows)
    base_network = aggregate_rows(base_rows)
    network_factors = {
        "viewers": clamp(
            actual_network["viewers"] / base_network["viewers"],
            PLAN_VIEWERS_FACTOR_MIN,
            PLAN_VIEWERS_FACTOR_MAX,
        ) if base_network["viewers"] else 1.0,
        "revenue": clamp(
            actual_network["revenue"] / base_network["revenue"],
            PLAN_REVENUE_FACTOR_MIN,
            PLAN_REVENUE_FACTOR_MAX,
        ) if base_network["revenue"] else 1.0,
    }
    factors = {}

    for cinema_id in ACTIVE_IDS:
        actual = aggregate_rows(actual_by_city.get(cinema_id, []))
        base = aggregate_rows(base_by_city.get(cinema_id, []))

        if base["viewers"] and base["revenue"]:
            factors[cinema_id] = {
                "viewers": clamp(
                    actual["viewers"] / base["viewers"],
                    PLAN_VIEWERS_FACTOR_MIN,
                    PLAN_VIEWERS_FACTOR_MAX,
                ),
                "revenue": clamp(
                    actual["revenue"] / base["revenue"],
                    PLAN_REVENUE_FACTOR_MIN,
                    PLAN_REVENUE_FACTOR_MAX,
                ),
                "has_base": True,
            }
        else:
            factors[cinema_id] = {
                "viewers": network_factors["viewers"],
                "revenue": network_factors["revenue"],
                "has_base": False,
            }

    return factors, network_factors


def fallback_period_plan_for_city(cinema_id, start, end, as_of_date):
    plan = {"revenue": 0, "viewers": 0}
    year_start = as_of_date.replace(month=1, day=1)

    if as_of_date < year_start:
        return plan

    rows = read_period_rows(year_start, as_of_date, cinemas=[cinema_id])
    grouped = group_by_date(rows)
    averages = defaultdict(lambda: {"revenue": [], "viewers": []})

    for day, metrics in grouped.items():
        day_kind = day_type(parse_date(day))
        averages[day_kind]["revenue"].append(metrics["revenue"])
        averages[day_kind]["viewers"].append(metrics["viewers"])

    current = start
    while current <= end:
        day_kind = day_type(current)
        plan["revenue"] += average(averages[day_kind]["revenue"]) or 0
        plan["viewers"] += average(averages[day_kind]["viewers"]) or 0
        current += timedelta(days=1)

    return plan


def realistic_period_plan(start, end, as_of_date, target_uplift=True):
    plan = empty_plan()

    if end < start:
        return plan

    factors, _ = realistic_plan_factors(as_of_date)
    base_rows = read_period_rows(shift_year(start, -1), shift_year(end, -1))
    base_by_city = rows_by_city(base_rows)

    for cinema_id in ACTIVE_IDS:
        city_rows = base_by_city.get(cinema_id, [])

        if not city_rows:
            fallback = fallback_period_plan_for_city(cinema_id, start, end, as_of_date)
            has_fallback = bool(fallback["revenue"] or fallback["viewers"])
            add_plan_item(
                plan,
                fallback["revenue"],
                fallback["viewers"],
                city_planned=has_fallback,
                city_name=None if has_fallback else CINEMAS.get(cinema_id, str(cinema_id)),
                target_uplift=target_uplift,
            )
            continue

        base = aggregate_rows(city_rows)
        factor = factors.get(cinema_id, {"viewers": 1.0, "revenue": 1.0})
        add_plan_item(
            plan,
            base["revenue"] * factor["revenue"],
            base["viewers"] * factor["viewers"],
            target_uplift=target_uplift,
        )

    return plan


def actual_period_totals(start, end):
    rows = read_period_rows(start, end)
    actual = aggregate_rows(rows)
    return {
        "revenue": actual["revenue"],
        "viewers": actual["viewers"],
    }


def apply_plan_uplift(value):
    return (value or 0) * (1 + PLAN_TARGET_UPLIFT)


def day_metric_average(day_metrics, target_date, key, exclude_target=True):
    values = []
    fallback_values = []
    target_type = comparison_day_type(target_date)

    for day_key, metrics in day_metrics.items():
        current_date = parse_date(day_key)

        if exclude_target and current_date == target_date:
            continue

        value = metrics.get(key)
        fallback_values.append(value)

        if comparison_day_type(current_date) == target_type:
            values.append(value)

    return average(values) if values else average(fallback_values)


def day_plan_from_current_month(month_rows, target_date):
    day_metrics = group_by_date(month_rows)
    day_rows = [
        row for row in month_rows
        if row.get("dt") == date_str(target_date)
    ]
    current_day = aggregate_rows(day_rows)
    revenue_avg = day_metric_average(day_metrics, target_date, "revenue")
    viewers_avg = day_metric_average(day_metrics, target_date, "viewers")

    return {
        "revenue": apply_plan_uplift(revenue_avg if revenue_avg is not None else current_day["revenue"]),
        "viewers": apply_plan_uplift(viewers_avg if viewers_avg is not None else current_day["viewers"]),
    }


def current_month_dynamics_forecast(month_rows, start, end):
    result = {"revenue": 0, "viewers": 0}

    if end < start:
        return result

    day_metrics = group_by_date(month_rows)
    current = start

    while current <= end:
        day_key = date_str(current)

        if day_key in day_metrics:
            result["revenue"] += day_metrics[day_key]["revenue"]
            result["viewers"] += day_metrics[day_key]["viewers"]
        else:
            result["revenue"] += day_metric_average(
                day_metrics,
                current,
                "revenue",
                exclude_target=False,
            ) or 0
            result["viewers"] += day_metric_average(
                day_metrics,
                current,
                "viewers",
                exclude_target=False,
            ) or 0

        current += timedelta(days=1)

    return result


def current_month_dynamics_plan(month_rows, start, end):
    forecast = current_month_dynamics_forecast(month_rows, start, end)

    return {
        "revenue": apply_plan_uplift(forecast["revenue"]),
        "viewers": apply_plan_uplift(forecast["viewers"]),
    }


def realistic_full_year_plan(target_date, target_uplift=True):
    year_start = target_date.replace(month=1, day=1)
    year_end = target_date.replace(month=12, day=31)
    actual_ytd = actual_period_totals(year_start, target_date)
    full_plan = realistic_period_plan(year_start, year_end, target_date, target_uplift=target_uplift)
    remaining = realistic_period_plan(
        target_date + timedelta(days=1),
        year_end,
        target_date,
        target_uplift=target_uplift,
    )

    return {
        "revenue": full_plan["revenue"],
        "viewers": full_plan["viewers"],
        "actual_ytd": actual_ytd,
        "remaining_plan": remaining,
    }


def scope_key(cinema_id=None):
    return f"cinema:{cinema_id}" if cinema_id else "network"


def forecast_revenue(row):
    if "rev" in row:
        return float(row.get("rev") or 0)

    return float(row.get("revenue_cinema") or 0) + float(row.get("revenue_bar") or 0)


def row_cinema_id(row):
    try:
        return int(row.get("cinema_id"))
    except (TypeError, ValueError):
        return None


def daily_revenue_by_weekday(rows, cinema_ids):
    totals = defaultdict(float)

    for row in rows:
        cinema_id = row_cinema_id(row)
        day = row.get("dt")

        if cinema_id not in cinema_ids or not day:
            continue

        totals[day] += forecast_revenue(row)

    values = defaultdict(list)

    for day, revenue in totals.items():
        values[parse_date(day).weekday()].append(revenue)

    return {
        weekday: average(day_values)
        for weekday, day_values in values.items()
    }


def revenue_by_date_from_rows(rows, cinema_ids):
    cinema_set = set(cinema_ids)
    totals = defaultdict(float)

    for row in rows:
        cinema_id = row_cinema_id(row)
        day = row.get("dt")

        if cinema_id in cinema_set and day:
            totals[day] += forecast_revenue(row)

    return dict(totals)


def clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


def recent_forecast_adjustment(max_dt, key, cinema_ids, recent_actual_rows):
    actual_by_day = revenue_by_date_from_rows(recent_actual_rows, cinema_ids)
    snapshots = load_forecast_snapshots().get(key, {})
    comparable = []
    current_day = max_dt
    lookback_start = max_dt - timedelta(days=FORECAST_ADJUSTMENT_LOOKBACK_DAYS)

    while current_day >= lookback_start and len(comparable) < FORECAST_ADJUSTMENT_DAYS:
        day_key = date_str(current_day)
        actual = actual_by_day.get(day_key)
        forecast_item = snapshots.get(day_key)
        forecast = float((forecast_item or {}).get("value") or 0)

        if actual is not None and forecast > 0:
            comparable.append((day_key, forecast, actual))

        current_day -= timedelta(days=1)

    if not comparable:
        return {
            "factor": 1.0,
            "raw_factor": None,
            "days": 0,
            "comparables": [],
        }

    forecast_total = sum(item[1] for item in comparable)
    actual_total = sum(item[2] for item in comparable)
    raw_factor = actual_total / forecast_total if forecast_total else 1.0

    return {
        "factor": clamp(raw_factor, FORECAST_ADJUSTMENT_MIN, FORECAST_ADJUSTMENT_MAX),
        "raw_factor": raw_factor,
        "days": len(comparable),
        "comparables": comparable,
    }


def calculate_daily_forecast(max_dt=None):
    max_dt = max_dt or parse_date(api_fetch("init")["max_dt"])
    month_start = max_dt.replace(day=1)
    month_end = month_start.replace(day=calendar.monthrange(max_dt.year, max_dt.month)[1])
    cmp_start = shift_year(month_start, -1)
    cmp_end = shift_year(max_dt, -1)
    cinemas_param = ",".join(str(cinema_id) for cinema_id in ACTIVE_IDS)

    forecast_data = api_fetch("forecast", {
        "cinemas": cinemas_param,
        "max_date": date_str(max_dt),
    })
    period_data = read_period(month_start, max_dt, cmp_start, cmp_end)
    current_rows = forecast_data.get("rows", [])
    last_year_forecast_rows = forecast_data.get("rows_ly", [])
    cmp_rows = period_data.get("rows_cmp", [])
    actual_month_rows = period_data.get("rows", [])
    recent_start = max_dt - timedelta(days=FORECAST_ADJUSTMENT_LOOKBACK_DAYS)
    recent_actual_rows = read_period(recent_start, max_dt).get("rows", [])

    scopes = {scope_key(cinema_id): [cinema_id] for cinema_id in ACTIVE_IDS}

    result = {}

    for key, cinema_ids in scopes.items():
        cinema_set = set(cinema_ids)
        cur_base = sum(
            forecast_revenue(row)
            for row in current_rows
            if row_cinema_id(row) in cinema_set
        )
        ly_base = sum(
            forecast_revenue(row)
            for row in last_year_forecast_rows
            if row_cinema_id(row) in cinema_set
        )
        yoy_coeff = cur_base / ly_base if ly_base else None

        ly_revenue_by_date = defaultdict(float)
        for row in cmp_rows + last_year_forecast_rows:
            cinema_id = row_cinema_id(row)
            day = row.get("dt")

            if cinema_id in cinema_set and day:
                ly_revenue_by_date[day] += forecast_revenue(row)

        dow_avg = daily_revenue_by_weekday(actual_month_rows, cinema_set)
        forecasts = {}
        current_day = max_dt + timedelta(days=1)

        while current_day <= month_end:
            ly_day = shift_year(current_day, -1)
            ly_value = ly_revenue_by_date.get(date_str(ly_day))
            fallback = dow_avg.get(current_day.weekday())

            if ly_value and yoy_coeff is not None:
                value = ly_value * yoy_coeff
            else:
                value = fallback

            if value:
                forecasts[date_str(current_day)] = round(value, 2)

            current_day += timedelta(days=1)

        adjustment = recent_forecast_adjustment(max_dt, key, cinema_ids, recent_actual_rows)
        adjusted_forecasts = {
            day: round(value * adjustment["factor"], 2)
            for day, value in forecasts.items()
        }

        result[key] = {
            "source_max_dt": date_str(max_dt),
            "yoy_coeff": yoy_coeff,
            "adjustment_factor": adjustment["factor"],
            "adjustment_raw_factor": adjustment["raw_factor"],
            "adjustment_days": adjustment["days"],
            "base_forecasts": forecasts,
            "forecasts": adjusted_forecasts,
        }

    network_forecasts = defaultdict(float)
    network_base_forecasts = defaultdict(float)

    for cinema_id in ACTIVE_IDS:
        city_data = result.get(scope_key(cinema_id), {})

        for day, value in city_data.get("forecasts", {}).items():
            network_forecasts[day] += value

        for day, value in city_data.get("base_forecasts", {}).items():
            network_base_forecasts[day] += value

    result["network"] = {
        "source_max_dt": date_str(max_dt),
        "yoy_coeff": None,
        "adjustment_factor": None,
        "adjustment_raw_factor": None,
        "adjustment_days": None,
        "base_forecasts": {day: round(value, 2) for day, value in network_base_forecasts.items()},
        "forecasts": {day: round(value, 2) for day, value in network_forecasts.items()},
        "method": "sum_city_adjusted",
    }

    return result


def save_current_forecast_snapshots():
    max_dt = parse_date(api_fetch("init")["max_dt"])
    calculated = calculate_daily_forecast(max_dt)
    snapshots = load_forecast_snapshots()
    saved_at = datetime.now().isoformat(timespec="seconds")

    for key, data in calculated.items():
        scope_snapshots = snapshots.setdefault(key, {})

        for day, value in data["forecasts"].items():
            scope_snapshots[day] = {
                "value": value,
                "base_value": data.get("base_forecasts", {}).get(day),
                "saved_at": saved_at,
                "source_max_dt": data["source_max_dt"],
                "yoy_coeff": data["yoy_coeff"],
                "adjustment_factor": data.get("adjustment_factor"),
                "adjustment_raw_factor": data.get("adjustment_raw_factor"),
                "adjustment_days": data.get("adjustment_days"),
                "method": data.get("method", "city_adjusted"),
            }

    save_forecast_snapshots(snapshots)
    return calculated


def saved_forecast_value(target_date, cinema_id=None):
    snapshots = load_forecast_snapshots()
    item = snapshots.get(scope_key(cinema_id), {}).get(date_str(target_date))

    if not item:
        return None

    return item.get("value")


def forecast_comparison_line(target_date, actual_revenue, cinema_id=None):
    forecast = saved_forecast_value(target_date, cinema_id)

    if not forecast:
        return ""

    return (
        f"• Прогноз вчера: {ru_money_compact(forecast)} | "
        f"факт: {ru_money_compact(actual_revenue)} | "
        f"{delta_plain(actual_revenue, forecast)}\n"
    )


def previous_week_bounds(today=None):
    today = today or datetime.now().date()
    current_week_start = today - timedelta(days=today.weekday())
    start = current_week_start - timedelta(days=7)
    end = current_week_start - timedelta(days=1)
    return start, end


def actual_revenue_by_date(start, end, cinemas=None):
    rows = read_period(start, end, cinemas=cinemas).get("rows", [])
    return revenue_by_date_from_rows(rows, cinemas or ACTIVE_IDS)


def lumen_fetch_json(base_url, path, params=None):
    query = {"key": LUMEN_SITE_API_KEY, **(params or {})}
    url = f"{base_url}{path}?{urllib.parse.urlencode(query, doseq=True)}"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def lumen_city_items():
    return lumen_fetch_json(LUMEN_SITE_API, "/cities").get("items", [])


def lumen_movies_for_date(day, cinema_ids):
    return lumen_fetch_json(
        LUMEN_SITE_API,
        f"/movies/date/{date_str(day)}",
        {"cinemaIds": ",".join(str(cinema_id) for cinema_id in cinema_ids)},
    ).get("items", [])


def lumen_cinemas_for_ids(cinema_ids):
    return lumen_fetch_json(
        LUMEN_SITE_API,
        "/cinemas",
        {"cinemaIds": ",".join(str(cinema_id) for cinema_id in cinema_ids)},
    ).get("items", [])


def parse_utc_offset(value):
    value = value or "+02:00"
    sign = -1 if value.startswith("-") else 1
    hours, minutes = value.lstrip("+-").split(":", 1)
    return timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))


def lumen_show_seat_stats(show_id):
    data = lumen_fetch_json(LUMEN_CINEMABOX_API, f"/ext5/shows/{show_id}")
    seat_ids = set()

    for item in data.get("scheme") or []:
        for place in item.get("places") or []:
            place_id = place.get("id")

            if place_id is not None:
                seat_ids.add(place_id)

    taken_places = data.get("taken_places") or data.get("takenPlaces") or []
    taken_ids = set()

    for place in taken_places:
        if isinstance(place, dict):
            place_id = place.get("id") or place.get("placeId") or place.get("place_id")

            if place_id is not None:
                taken_ids.add(place_id)
        else:
            taken_ids.add(place)

    return {
        "seats": len(seat_ids),
        "taken": len(taken_ids) if taken_ids else len(taken_places),
        "datetime": data.get("datetime"),
        "duration": float((data.get("movie") or {}).get("duration") or 0),
        "ads_duration": float(data.get("ads_duration") or data.get("adsDuration") or 0),
    }


def occupancy_pct(taken, seats):
    return taken / seats if seats else 0


def occupancy_pct_text(taken, seats):
    return ru_pct(occupancy_pct(taken, seats), 1)


def collect_lumen_occupancy(day=None):
    collected_at = datetime.now()
    utc_now = datetime.now(timezone.utc)
    film_stats = defaultdict(lambda: {
        "shows": 0,
        "seats": 0,
        "taken": 0,
        "cities": set(),
        "max_occ": 0,
        "max_show": "",
    })
    city_stats = defaultdict(lambda: {
        "shows": 0,
        "seats": 0,
        "taken": 0,
        "empty_shows": 0,
    })
    empty_cities = []
    errors = []
    active_cities = []
    last_show_ends = {}
    city_dates = {}

    for city in lumen_city_items():
        cinema_ids = city.get("cinemaIds") or []

        if not cinema_ids:
            continue

        city_name = city.get("name") or "н/д"
        timezone_value = "+02:00"

        try:
            cinemas = lumen_cinemas_for_ids(cinema_ids)
            timezone_value = (cinemas[0] or {}).get("utcOffset") or timezone_value if cinemas else timezone_value
        except Exception as error:
            errors.append(f"{city_name}: часовой пояс {error}")

        city_timezone = parse_utc_offset(timezone_value)
        city_now = utc_now.astimezone(city_timezone)
        city_day = day or city_now.date()
        city_dates[city_name] = date_str(city_day)

        try:
            movies = lumen_movies_for_date(city_day, cinema_ids)
        except Exception as error:
            errors.append(f"{city_name}: афиша {error}")
            continue

        if not movies:
            empty_cities.append(city_name)

        for movie in movies:
            movie_name = movie.get("name") or "Без названия"

            for show in movie.get("shows") or []:
                show_id = show.get("id")

                if not show_id:
                    continue

                try:
                    seat_stats = lumen_show_seat_stats(show_id)
                except Exception as error:
                    errors.append(f"{city_name} show {show_id}: {error}")
                    continue

                seats = seat_stats["seats"]
                taken = seat_stats["taken"]
                show_occ = occupancy_pct(taken, seats)
                show_datetime = seat_stats.get("datetime") or show.get("datetime")
                show_end_local = None

                if show_datetime:
                    show_start = datetime.strptime(show_datetime, "%Y-%m-%d %H:%M").replace(tzinfo=city_timezone)
                    show_end_local = show_start + timedelta(
                        minutes=seat_stats.get("duration", 0) + seat_stats.get("ads_duration", 0)
                    )
                    last_show_ends[city_name] = max(
                        last_show_ends.get(city_name, show_end_local),
                        show_end_local,
                    )

                    if city_now <= show_end_local and city_name not in active_cities:
                        active_cities.append(city_name)

                film = film_stats[movie_name]
                film["shows"] += 1
                film["seats"] += seats
                film["taken"] += taken
                film["cities"].add(city_name)

                if show_occ > film["max_occ"]:
                    film["max_occ"] = show_occ
                    film["max_show"] = (
                        f"{city_name} {show.get('time')} "
                        f"зал {show.get('hallName') or show.get('hallCategory') or 'н/д'}"
                    )

                city_item = city_stats[city_name]
                city_item["shows"] += 1
                city_item["seats"] += seats
                city_item["taken"] += taken

                if taken == 0:
                    city_item["empty_shows"] += 1

    return {
        "day": day or collected_at.date(),
        "collected_at": collected_at,
        "films": film_stats,
        "cities": city_stats,
        "empty_cities": empty_cities,
        "errors": errors,
        "active_cities": active_cities,
        "city_dates": city_dates,
        "last_show_ends": {
            city_name: value.isoformat(timespec="minutes")
            for city_name, value in last_show_ends.items()
        },
        "has_active_shows": bool(active_cities),
    }


def serialize_lumen_occupancy_report(report):
    cities = report["cities"]
    films = report["films"]
    total_taken = sum(item["taken"] for item in cities.values())
    total_seats = sum(item["seats"] for item in cities.values())
    total_shows = sum(item["shows"] for item in cities.values())

    return {
        "kind": "lumen_occupancy",
        "collected_at": report["collected_at"].isoformat(timespec="seconds"),
        "day": date_str(report["day"]),
        "network": {
            "taken": total_taken,
            "seats": total_seats,
            "shows": total_shows,
            "occupancy": occupancy_pct(total_taken, total_seats),
        },
        "active_cities": report["active_cities"],
        "city_dates": report["city_dates"],
        "last_show_ends": report["last_show_ends"],
        "has_active_shows": report["has_active_shows"],
        "films": {
            name: {
                "taken": item["taken"],
                "seats": item["seats"],
                "shows": item["shows"],
                "occupancy": occupancy_pct(item["taken"], item["seats"]),
                "cities": sorted(item["cities"]),
                "max_occupancy": item["max_occ"],
                "max_show": item["max_show"],
            }
            for name, item in films.items()
        },
        "cities": {
            name: {
                "taken": item["taken"],
                "seats": item["seats"],
                "shows": item["shows"],
                "empty_shows": item["empty_shows"],
                "occupancy": occupancy_pct(item["taken"], item["seats"]),
            }
            for name, item in cities.items()
        },
        "empty_cities": report["empty_cities"],
        "errors": report["errors"],
    }


def save_lumen_occupancy_history(report):
    append_jsonl(OCCUPANCY_HISTORY_FILE, serialize_lumen_occupancy_report(report))


def load_latest_lumen_occupancy_snapshot(day):
    target_day = date_str(day)
    latest = None
    latest_at = None

    try:
        lines = OCCUPANCY_HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None

    for line in lines:
        if not line.strip():
            continue

        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue

        if item.get("kind") != "lumen_occupancy" or item.get("day") != target_day:
            continue

        if not item.get("network", {}).get("shows"):
            continue

        try:
            collected_at = datetime.fromisoformat(item["collected_at"])
        except (KeyError, ValueError):
            continue

        if latest_at is None or collected_at > latest_at:
            latest = item
            latest_at = collected_at

    return latest


def build_lumen_occupancy_report(day=None, report=None):
    report = report or collect_lumen_occupancy(day)
    if isinstance(report["day"], str):
        report["day"] = parse_date(report["day"])
    if isinstance(report["collected_at"], str):
        report["collected_at"] = datetime.fromisoformat(report["collected_at"])

    films = report["films"]
    cities = report["cities"]
    total_taken = sum(item["taken"] for item in cities.values())
    total_seats = sum(item["seats"] for item in cities.values())
    total_shows = sum(item["shows"] for item in cities.values())
    film_rows = []

    for name, item in films.items():
        film_rows.append((
            occupancy_pct(item["taken"], item["seats"]),
            item["taken"],
            item["seats"],
            item["shows"],
            name,
            len(item["cities"]),
            item.get("max_occ", item.get("max_occupancy", 0)),
            item["max_show"],
        ))

    film_rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
    city_rows = []

    for name, item in cities.items():
        city_rows.append((
            occupancy_pct(item["taken"], item["seats"]),
            item["taken"],
            item["seats"],
            item["shows"],
            item.get("empty_shows", 0),
            name,
        ))

    city_rows.sort(key=lambda row: (row[0], row[1]), reverse=True)

    message = "🎟 ЗАПОЛНЯЕМОСТЬ ЗАЛОВ — ЛЮМЕН\n"
    message += f"{display_date(report['day'])} · {report['collected_at'].strftime('%H:%M')}\n"
    message += f"Активные города: {len(report['active_cities'])}\n\n"
    message += (
        f"Сеть: {ru_num(total_taken)} / {ru_num(total_seats)} мест | "
        f"{occupancy_pct_text(total_taken, total_seats)} | "
        f"сеансов {ru_num(total_shows)}\n\n"
    )

    if not film_rows:
        message += "В открытой афише на сегодня сеансов не найдено."
        return message

    message += "По фильмам\n"

    for index, (occ, taken, seats, shows, name, cities_count, max_occ, max_show) in enumerate(film_rows[:15], 1):
        best = f"; лучший {ru_pct(max_occ, 1)} — {max_show}" if max_show else ""
        message += (
            f"{index}. {name} — {ru_pct(occ, 1)} | "
            f"{ru_num(taken)}/{ru_num(seats)} | "
            f"сеансов {shows}, городов {cities_count}{best}\n"
        )

    message += "\nПо городам\n"

    for occ, taken, seats, shows, empty_shows, name in city_rows[:15]:
        message += (
            f"• {name}: {ru_pct(occ, 1)} | "
            f"{ru_num(taken)}/{ru_num(seats)} | "
            f"сеансов {shows} | пустых {empty_shows}\n"
        )

    if report["empty_cities"]:
        message += "\nБез сеансов в открытой афише: "
        message += ", ".join(report["empty_cities"][:10])

        if len(report["empty_cities"]) > 10:
            message += f" и еще {len(report['empty_cities']) - 10}"

        message += ".\n"

    if report["errors"]:
        message += f"\nОшибки сбора: {len(report['errors'])}."

    message += "\nДанные: открытая онлайн-схема продаж lumenfilm.com."
    return message.strip()


def normalize_movie_name(value):
    value = html.unescape(value or "").lower().replace("ё", "е")
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"[^a-zа-я0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def movie_name_matches(left, right):
    left = normalize_movie_name(left)
    right = normalize_movie_name(right)

    if not left or not right:
        return False

    return left == right or left in right or right in left


def parse_money_rub(value):
    value = (value or "").split("$", 1)[0]
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def clean_text(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<.*?>", " ", value or ""))).strip()


def kinometro_get(path):
    request = urllib.request.Request(
        f"https://www.kinometro.ru{path}",
        headers={"User-Agent": "Mozilla/5.0"},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "ignore")


def kinometro_latest_article_path(section_path):
    page = kinometro_get(section_path)
    match = re.search(
        rf'href="({re.escape(section_path)}/[^"]+)"',
        page,
    )
    return match.group(1) if match else None


def kinometro_table_rows(path):
    page = kinometro_get(path)
    rows = []

    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cells = [
            clean_text(cell)
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S)
        ]

        if cells:
            rows.append(cells)

    return rows


def kinometro_forecast_data():
    article_path = kinometro_latest_article_path("/forecast")

    if not article_path:
        return {}

    result = {}

    for cells in kinometro_table_rows(article_path):
        if len(cells) < 5 or not cells[0].isdigit():
            continue

        title = cells[1]
        forecast = parse_money_rub(cells[4])

        if title and forecast:
            result[normalize_movie_name(title)] = {
                "title": title,
                "forecast": forecast,
                "source": f"https://www.kinometro.ru{article_path}",
            }

    return result


def kinometro_prebox_data():
    article_path = kinometro_latest_article_path("/prebox")

    if not article_path:
        return {}

    result = {}

    for cells in kinometro_table_rows(article_path):
        if len(cells) < 9 or not cells[0].isdigit():
            continue

        title = cells[1]
        weekend = parse_money_rub(cells[5])
        total = parse_money_rub(cells[8])

        if title and (weekend or total):
            result[normalize_movie_name(title)] = {
                "title": title,
                "weekend": weekend,
                "total": total,
                "source": f"https://www.kinometro.ru{article_path}",
            }

    return result


def kinometro_find_match(name, data):
    for source_name, item in data.items():
        if movie_name_matches(name, source_name):
            return item

    return None


def current_network_avg_ticket(default=450):
    try:
        target_date = parse_date(api_fetch("init")["max_dt"])
        rows = read_period(target_date, target_date).get("rows", [])
        metrics = aggregate_rows(rows)
        return metrics["avg_ticket"] or default
    except Exception as error:
        print(f"Не удалось получить среднюю цену билета: {error}", flush=True)
        return default


def movie_expectation_level(occ, shows, cities_count):
    score = occ * 100 + min(shows, 60) * 0.25 + min(cities_count, 12) * 1.2

    if score >= 24:
        return "высокая"
    if score >= 12:
        return "средняя"
    return "низкая"


def projected_movie_viewers(taken, seats, occ, shows, cities_count):
    if not seats:
        return taken

    target_occ = 0.10

    if occ >= 0.22:
        target_occ = 0.42
    elif occ >= 0.14:
        target_occ = 0.32
    elif occ >= 0.08:
        target_occ = 0.23
    elif occ >= 0.04:
        target_occ = 0.16

    if shows >= 30 or cities_count >= 8:
        target_occ += 0.03

    return max(taken, min(seats, seats * target_occ))


def movie_potential_action(occ, shows, cities_count, empty_share):
    if occ >= 0.16 and cities_count >= 4:
        return "усилить прайм, premium и комбо"
    if occ >= 0.08:
        return "держать расписание, проверить слабые дневные сеансы"
    if empty_share >= 0.35:
        return "сократить пустые сеансы или перенести в меньшие залы"
    return "оставить точечно, без расширения"


def build_movie_potential_payload():
    report = collect_lumen_occupancy()
    avg_ticket = current_network_avg_ticket()
    kinometro_forecast = kinometro_forecast_data()
    kinometro_prebox = kinometro_prebox_data()
    films = report["films"]
    rows = []

    for name, item in films.items():
        shows = item["shows"]
        seats = item["seats"]
        taken = item["taken"]
        cities_count = len(item["cities"])
        occ = occupancy_pct(taken, seats)
        empty_share = 1 - (taken / shows if shows else 0)
        forecast_viewers = projected_movie_viewers(taken, seats, occ, shows, cities_count)
        forecast_revenue = forecast_viewers * avg_ticket
        expectation = movie_expectation_level(occ, shows, cities_count)
        action = movie_potential_action(occ, shows, cities_count, empty_share)
        market_forecast = kinometro_find_match(name, kinometro_forecast)
        market_fact = kinometro_find_match(name, kinometro_prebox)
        market_score = 0

        if market_forecast and market_forecast.get("forecast"):
            market_score += min(market_forecast["forecast"] / 10_000_000, 40)

        if market_fact and market_fact.get("weekend"):
            market_score += min(market_fact["weekend"] / 10_000_000, 40)

        rows.append((
            market_score,
            forecast_revenue,
            occ,
            taken,
            seats,
            shows,
            cities_count,
            name,
            expectation,
            forecast_viewers,
            action,
            item.get("max_occ", 0),
            item.get("max_show", ""),
            market_forecast,
            market_fact,
        ))

    rows.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)

    message = "🎬 ЛЮМЕН — ПОТЕНЦИАЛ ФИЛЬМОВ\n"
    message += f"{display_date(report['day'])} · {report['collected_at'].strftime('%H:%M')}\n"
    message += f"Источники рынка: Kinometro прогноз + предварительная касса.\n"
    message += f"Средняя цена билета Lumen: {ru_rub(avg_ticket)}\n\n"

    if not rows:
        message += "В открытой афише на сегодня фильмов не найдено."
        return message

    for index, (market_score, forecast_revenue, occ, taken, seats, shows, cities_count, name, expectation, forecast_viewers, action, max_occ, max_show, market_forecast, market_fact) in enumerate(rows[:12], 1):
        best = f"\n   Лучший сеанс: {ru_pct(max_occ, 1)} — {max_show}" if max_show else ""
        market_lines = []

        if market_forecast and market_forecast.get("forecast"):
            market_lines.append(f"прогноз БК {ru_money(market_forecast['forecast'])}")

        if market_fact and market_fact.get("weekend"):
            market_lines.append(f"факт уикенда {ru_money(market_fact['weekend'])}")

        if market_fact and market_fact.get("total"):
            market_lines.append(f"общая касса {ru_money(market_fact['total'])}")

        market_text = " | ".join(market_lines) if market_lines else "нет совпадения в свежих данных Kinometro"
        message += (
            f"{index}. {name}\n"
            f"• Города/сеансы: {cities_count}/{shows}\n"
            f"• Сейчас: {ru_num(taken)}/{ru_num(seats)} мест | {ru_pct(occ, 1)}\n"
            f"• Рынок: {market_text}\n"
            f"• Потенциал Lumen: {expectation}; {ru_num(forecast_viewers)} зрителей | {ru_money(forecast_revenue)}\n"
            f"• Действие: {action}{best}\n\n"
        )

    message += "Логика: рынок Kinometro показывает силу фильма в РФ, Lumen-потенциал показывает нашу текущую предпродажу и ёмкость расписания."
    return message.strip()


def kinobusiness_release_items():
    request = urllib.request.Request(
        "https://www.kinobusiness.com/release/",
        headers={"User-Agent": "Mozilla/5.0"},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        page = response.read().decode("utf-8", "ignore")

    current_date = ""
    items = []

    for chunk in re.split(r'<div class="sortable__brick"', page):
        date_match = re.search(r'<div class="sortable__date">\s*<span>(.*?)</span>', chunk, re.S)

        if date_match:
            current_date = re.sub(r"\s+", " ", html.unescape(re.sub(r"<.*?>", "", date_match.group(1)))).strip()

        title_match = re.search(r'class="fname">(.*?)</a>', chunk, re.S)

        if not title_match:
            continue

        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<.*?>", "", title_match.group(1)))).strip()
        distributor_match = re.search(r'Дистрибьютор:.*?<a[^>]*>(.*?)</a>', chunk, re.S)
        distributor = (
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<.*?>", "", distributor_match.group(1)))).strip()
            if distributor_match else "н/д"
        )
        genres = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<.*?>", "", genre))).strip()
            for genre in re.findall(r'itemprop="genre"[^>]*>(.*?)</span>', chunk, re.S)
        ]
        country_match = re.search(r'<span>Страна: </span>\s*<span>(.*?)</span>', chunk, re.S)
        country = (
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<.*?>", "", country_match.group(1)))).strip()
            if country_match else "н/д"
        )

        items.append({
            "title": title,
            "date": current_date,
            "distributor": distributor,
            "genres": genres,
            "country": country,
        })

    return items


def send_lumen_occupancy_report():
    report = collect_lumen_occupancy()
    save_lumen_occupancy_history(report)

    if not report["has_active_shows"]:
        print(
            "Отчет по заполняемости не отправлен: все последние сеансы уже завершились.",
            flush=True,
        )
        return False

    message = build_lumen_occupancy_report(report=report)

    for part in split_message(message):
        send_telegram_message(part, chat_id=CHAT_ID, with_button=False)

    return True


def send_lumen_occupancy_current_report(chat_id):
    report = collect_lumen_occupancy()
    save_lumen_occupancy_history(report)
    message = build_lumen_occupancy_report(report=report)

    for part in split_message(message):
        send_telegram_message(part, chat_id=chat_id, with_button=True)

    return True


def save_lumen_occupancy_snapshot():
    report = collect_lumen_occupancy()
    save_lumen_occupancy_history(report)
    return True


def save_lumen_occupancy_today_snapshot():
    report = collect_lumen_occupancy(datetime.now().date())
    save_lumen_occupancy_history(report)
    return True


def save_lumen_occupancy_yesterday_snapshot():
    report = collect_lumen_occupancy(datetime.now().date() - timedelta(days=1))
    save_lumen_occupancy_history(report)
    return True


def send_lumen_occupancy_yesterday_report():
    target_day = datetime.now().date() - timedelta(days=1)
    report = load_latest_lumen_occupancy_snapshot(target_day)

    if report is None:
        report = collect_lumen_occupancy(target_day)

        if not report["films"]:
            message = (
                "🎟 ЗАПОЛНЯЕМОСТЬ ЗАЛОВ — ЛЮМЕН\n"
                f"{display_date(target_day)}\n\n"
                "⚠️ Нет сохранённого снимка за этот день.\n"
                "Открытая афиша lumenfilm.com уже не отдаёт прошедший день, "
                "поэтому корректный отчёт можно собрать только до смены даты."
            )

            for part in split_message(message):
                send_telegram_message(part, chat_id=CHAT_ID, with_button=False)

            return False

        save_lumen_occupancy_history(report)

    message = build_lumen_occupancy_report(report=report)

    for part in split_message(message):
        send_telegram_message(part, chat_id=CHAT_ID, with_button=False)

    return True


def revenue_forecast_fact_week_answer():
    requested_start, requested_end = previous_week_bounds()
    latest_date = parse_date(api_fetch("init")["max_dt"])
    actual_end = min(requested_end, latest_date)

    if actual_end < requested_start:
        return (
            "Данных за прошлую неделю еще нет.\n"
            f"Нужен период: {display_date(requested_start)}–{display_date(requested_end)}.\n"
            f"Последний доступный факт: {display_date(latest_date)}."
        )

    actual_by_day = actual_revenue_by_date(requested_start, actual_end)
    snapshots = load_forecast_snapshots().get("network", {})
    comparable = []
    missing_forecast = []
    missing_actual = []

    current_day = requested_start

    while current_day <= requested_end:
        day_key = date_str(current_day)
        actual = actual_by_day.get(day_key)
        forecast_item = snapshots.get(day_key)

        if current_day > latest_date:
            missing_actual.append(current_day)
        elif actual is None:
            missing_actual.append(current_day)
        elif not forecast_item:
            missing_forecast.append(current_day)
        else:
            comparable.append((current_day, float(forecast_item.get("value") or 0), actual))

        current_day += timedelta(days=1)

    if not comparable:
        message = (
            "За прошлую неделю пока нет дней, где одновременно есть сохраненный прогноз и факт.\n"
            f"Период: {display_date(requested_start)}–{display_date(requested_end)}."
        )

        if missing_actual:
            message += f"\nНет факта: {', '.join(display_date(day) for day in missing_actual)}."

        if missing_forecast:
            message += f"\nНет сохраненного прогноза: {', '.join(display_date(day) for day in missing_forecast)}."

        return message

    total_forecast = sum(day_forecast for _, day_forecast, _ in comparable)
    total_actual = sum(day_actual for _, _, day_actual in comparable)

    lines = [
        "📊 Прогноз / факт по выручке",
        f"Период: {display_date(requested_start)}–{display_date(requested_end)}",
        f"Сравнимые дни: {len(comparable)} из 7",
        "",
        f"• Прогноз: {ru_money_compact(total_forecast)}",
        f"• Факт: {ru_money_compact(total_actual)}",
        f"• Разница: {ru_money_compact(total_actual - total_forecast)} | {delta_plain(total_actual, total_forecast)}",
    ]

    lines.append("")
    lines.append("По дням:")

    for day, forecast, actual in comparable:
        lines.append(
            f"• {display_date(day)}: прогноз {ru_money_compact(forecast)} | "
            f"факт {ru_money_compact(actual)} | {delta_plain(actual, forecast)}"
        )

    notes = []

    if missing_actual:
        notes.append(f"нет факта: {', '.join(display_date(day) for day in missing_actual)}")

    if missing_forecast:
        notes.append(f"нет сохраненного прогноза: {', '.join(display_date(day) for day in missing_forecast)}")

    if notes:
        lines.append("")
        lines.append("Ограничение: " + "; ".join(notes) + ".")

    return "\n".join(lines)


def analytics_question_answer(text):
    normalized = text.lower().replace("ё", "е")
    has_week = "недел" in normalized
    has_revenue = any(word in normalized for word in ("выруч", "сбор", "деньг"))
    has_forecast = any(word in normalized for word in ("прогноз", "план"))
    has_fact = "факт" in normalized
    has_delta = any(word in normalized for word in ("разниц", "отклон", "сравн", "между"))

    if has_week and has_revenue and has_forecast and (has_fact or has_delta):
        return revenue_forecast_fact_week_answer()

    return (
        "Пока я умею отвечать на такие вопросы:\n"
        "• дай разницу за прошлую неделю по выручке между прогнозом и фактом\n\n"
        "Можно писать обычным текстом, без команды."
    )


def projected_month_revenue_by_pnl(target_date, actual_revenue):
    try:
        forecast = calculate_daily_forecast(target_date).get("network", {})
    except Exception as error:
        print(f"Не удалось рассчитать PnL-прогноз месяца: {error}", flush=True)
        return None

    remaining_revenue = sum(forecast.get("forecasts", {}).values())

    if not remaining_revenue:
        return None

    return actual_revenue + remaining_revenue


def build_business360_history_snapshot():
    init = api_fetch("init")
    report_date = datetime.now().date()
    target_date = parse_date(init["max_dt"])
    month_start = target_date.replace(day=1)
    days_in_month = calendar.monthrange(target_date.year, target_date.month)[1]
    period_data = read_period(month_start, target_date)
    month_rows = period_data.get("rows", [])
    day_rows = [row for row in month_rows if row.get("dt") == date_str(target_date)]

    if not day_rows:
        return None

    day = aggregate_rows(day_rows)
    day_metrics = group_by_date(month_rows)
    staff_month_by_day = staff_by_date(month_start, target_date)

    for day_key, metrics in day_metrics.items():
        payroll = staff_month_by_day.get(day_key, {}).get("payroll", 0)
        metrics["payroll"] = payroll
        metrics["lc"] = payroll / metrics["revenue"] if metrics["revenue"] else None

    staff_day = staff_month_by_day.get(date_str(target_date), {"payroll": 0, "hours": 0})
    day["payroll"] = staff_day["payroll"]
    day["hours"] = staff_day["hours"]
    day["lc"] = staff_day["payroll"] / day["revenue"] if day["revenue"] else None
    month_revenue = sum(metrics["revenue"] for metrics in day_metrics.values())
    month_bar = sum(metrics["bar_revenue"] for metrics in day_metrics.values())
    month_viewers = sum(metrics["viewers"] for metrics in day_metrics.values())
    days_with_data = max(1, len(day_metrics))
    projected_revenue_runrate = month_revenue / days_with_data * days_in_month
    projected_revenue_pnl = projected_month_revenue_by_pnl(target_date, month_revenue)
    projected_bar = month_bar / days_with_data * days_in_month
    projected_viewers = month_viewers / days_with_data * days_in_month
    projected_percap = projected_bar / projected_viewers if projected_viewers else None
    city_metrics = city_day_metrics(target_date, month_rows)
    saved_forecast = saved_forecast_value(target_date)

    return {
        "kind": "business360_daily",
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "report_date": date_str(report_date),
        "period": date_str(target_date),
        "network": {
            "revenue": day["revenue"],
            "bar_revenue": day["bar_revenue"],
            "tickets_revenue": day["tickets_revenue"],
            "viewers": day["viewers"],
            "percap": day["percap"],
            "bar_share": day["bar_share"],
            "bar_conversion": day["bar_conversion"],
            "foodcost": day["foodcost"],
            "foodcost_amount": day["foodcost_amount"],
            "payroll": day["payroll"],
            "hours": day["hours"],
            "lc": day["lc"],
            "saved_forecast_revenue": saved_forecast,
            "forecast_delta": (
                (day["revenue"] - saved_forecast) / saved_forecast
                if saved_forecast else None
            ),
        },
        "month_projection": {
            "revenue": projected_revenue_pnl or projected_revenue_runrate,
            "revenue_pnl": projected_revenue_pnl,
            "revenue_runrate": projected_revenue_runrate,
            "bar_revenue": projected_bar,
            "viewers": projected_viewers,
            "percap": projected_percap,
        },
        "cities": {
            city["name"]: {
                key: value
                for key, value in city.items()
                if key not in {"name"}
            }
            for city in city_metrics
        },
    }


def save_business360_history_snapshot():
    snapshot = build_business360_history_snapshot()

    if snapshot:
        append_jsonl(BUSINESS360_HISTORY_FILE, snapshot)

    return snapshot


def read_staff(start, end, cinemas=None):
    cinemas = cinemas or ACTIVE_IDS
    return api_fetch("staff", {
        "start": date_str(start),
        "end": date_str(end),
        "cinemas": ",".join(str(cinema_id) for cinema_id in cinemas),
    })


def staff_totals(start, end, cinemas=None):
    try:
        data = read_staff(start, end, cinemas)
    except Exception as error:
        print(f"Не удалось получить персонал: {error}", flush=True)
        return {"payroll": 0, "hours": 0}

    return {
        "payroll": sum(float(row.get("payroll") or 0) for row in data.get("by_day", [])),
        "hours": sum(float(row.get("hours") or 0) for row in data.get("by_day", [])),
    }


def staff_by_date(start, end, cinemas=None):
    try:
        data = read_staff(start, end, cinemas)
    except Exception as error:
        print(f"Не удалось получить персонал: {error}", flush=True)
        return {}

    result = defaultdict(lambda: {"payroll": 0, "hours": 0})

    for row in data.get("by_day", []):
        day = row.get("dt")

        if not day:
            continue

        result[day]["payroll"] += float(row.get("payroll") or 0)
        result[day]["hours"] += float(row.get("hours") or 0)

    return dict(result)


def dish_category(name, category):
    text = f"{name or ''} {category or ''}".lower()

    if "комбо" in text:
        return "Комбо"

    if "начос" in text or "nachos" in text:
        return "Начос"

    if any(word in text for word in ("напит", "кола", "cola", "pepsi", "вода", "сок", "чай", "кофе")):
        return "Напитки"

    if any(word in text for word in ("попкорн", "карамель", "сырный", "солен", "малый", "средний", "большой")):
        return "Попкорн"

    return "Прочее"


def bar_structure(start, end):
    data = api_fetch("bar", {
        "start": date_str(start),
        "end": date_str(end),
        "cinemas": ",".join(str(cinema_id) for cinema_id in ACTIVE_IDS),
    })
    totals = defaultdict(float)

    for dish in data.get("dishes", []):
        totals[dish_category(dish.get("dish_name"), dish.get("dish_category"))] += float(dish.get("revenue") or 0)

    total = sum(totals.values())

    if not total:
        return []

    order = ["Попкорн", "Напитки", "Комбо", "Начос", "Прочее"]
    return [(name, totals[name] / total) for name in order if totals.get(name)]


def city_day_metrics(target_date, month_rows):
    month_by_city = defaultdict(list)

    for row in month_rows:
        month_by_city[int(row.get("cinema_id"))].append(row)

    result = []

    for cinema_id, rows in month_by_city.items():
        day_rows = [row for row in rows if row.get("dt") == date_str(target_date)]

        if not day_rows:
            continue

        day = aggregate_rows(day_rows)
        month_day_metrics = group_by_date(rows)
        same_type_viewers_avg = month_average(
            month_day_metrics,
            target_date,
            "viewers",
            same_day_type=True,
            exclude_target=True,
        )
        bar_avg = month_average(
            month_day_metrics,
            target_date,
            "bar_revenue",
            exclude_target=True,
        )
        staff = staff_totals(target_date, target_date, [cinema_id])
        lc = staff["payroll"] / day["revenue"] if day["revenue"] else None
        ebitda = day["revenue"] - staff["payroll"] - day["foodcost_amount"]
        ebitda_pct = ebitda / day["revenue"] if day["revenue"] else None

        result.append({
            "id": cinema_id,
            "name": CINEMAS.get(cinema_id, str(cinema_id)),
            **day,
            "lc": lc,
            "ebitda_pct": ebitda_pct,
            "viewers_avg_same_type": same_type_viewers_avg,
            "bar_avg": bar_avg,
            "bar_growth": (day["bar_revenue"] - bar_avg) / bar_avg if bar_avg else None,
            "viewers_delta": (day["viewers"] - same_type_viewers_avg) / same_type_viewers_avg if same_type_viewers_avg else None,
        })

    return result


def best_worst_cities(city_metrics):
    active = [city for city in city_metrics if city["viewers"] > 0]

    if not active:
        return [], []

    best_percap = max(active, key=lambda city: city["percap"] or 0)
    best_ebitda = max(active, key=lambda city: city["ebitda_pct"] if city["ebitda_pct"] is not None else -9)
    best_bar = max(active, key=lambda city: city["bar_growth"] if city["bar_growth"] is not None else -9)
    worst_lc = max(active, key=lambda city: city["lc"] if city["lc"] is not None else -9)
    worst_bar_conversion = min(active, key=lambda city: city["bar_conversion"] if city["bar_conversion"] is not None else 9)
    worst_viewers = min(active, key=lambda city: city["viewers_delta"] if city["viewers_delta"] is not None else 9)

    bar_growth_label = (
        f"рост бара {delta_from_ratio(best_bar['bar_growth'])}"
        if best_bar["bar_growth"] is not None and best_bar["bar_growth"] >= 0
        else f"наименьшее падение бара {delta_from_ratio(best_bar['bar_growth'])}"
    )
    best = [
        f"{best_percap['name']} — Percap {ru_rub(best_percap['percap'])}",
        f"{best_ebitda['name']} — EBITDA {ru_pct(best_ebitda['ebitda_pct'], 0)}",
        f"{best_bar['name']} — {bar_growth_label}",
    ]
    worst = [
        f"{worst_lc['name']} — LC {ru_pct(worst_lc['lc'], 0)}",
        f"{worst_bar_conversion['name']} — конверсия бара {ru_pct(worst_bar_conversion['bar_conversion'], 1)}",
        f"{worst_viewers['name']} — зрители {delta_from_ratio(worst_viewers['viewers_delta'])}",
    ]
    return best, worst


def delta_from_ratio(value):
    if value is None:
        return "н/д"

    sign = "+" if value > 0 else ""
    return f"{sign}{value * 100:.0f}%".replace(".", ",")


def ai_insights(day, month_avg, city_metrics):
    insights = []

    if day["percap"] and month_avg["percap"]:
        diff = (day["percap"] - month_avg["percap"]) / month_avg["percap"]
        if diff >= 0.05:
            insights.append(f"Percap выше среднего месяца на {delta_from_ratio(diff)} — бар сегодня работает сильнее нормы.")
        elif diff <= -0.05:
            insights.append(f"Percap ниже среднего месяца на {delta_from_ratio(diff)} — проверить комбо, выкладку и скрипты бара.")

    weak_bar = [city for city in city_metrics if city["bar_conversion"] is not None and city["bar_conversion"] < 0.35 and city["viewers"] >= 30]
    if weak_bar:
        weak_bar.sort(key=lambda city: city["bar_conversion"])
        weak_bar_names = ", ".join(
            f"{city['name']} ({ru_pct(city['bar_conversion'], 1)})"
            for city in weak_bar[:5]
        )
        more = f" и ещё {len(weak_bar) - 5}" if len(weak_bar) > 5 else ""
        insights.append(f"Конверсия бара ниже 35%: {weak_bar_names}{more} — приоритет для управляющих смен.")

    weak_lc = [city for city in city_metrics if city["lc"] is not None and city["lc"] > 0.22]
    if weak_lc:
        weak_lc.sort(key=lambda city: city["lc"], reverse=True)
        weak_lc_names = ", ".join(
            f"{city['name']} ({ru_pct(city['lc'], 0)})"
            for city in weak_lc[:5]
        )
        more = f" и ещё {len(weak_lc) - 5}" if len(weak_lc) > 5 else ""
        insights.append(f"LC выше 22%: {weak_lc_names}{more} — сверить часы персонала с трафиком.")

    if city_metrics:
        top_eff = max(city_metrics, key=lambda city: city["ebitda_pct"] if city["ebitda_pct"] is not None else -9)
        insights.append(f"{top_eff['name']} показывает лучшую операционную эффективность сети.")

        low_drinks_hint = min(city_metrics, key=lambda city: city["bar_conversion"] if city["bar_conversion"] is not None else 9)
        insights.append(f"{low_drinks_hint['name']} — первая точка для проверки бара: конверсия {ru_pct(low_drinks_hint['bar_conversion'], 1)}.")

    return insights[:5]


def build_city_report_payload(cinema_id):
    cinema_name = CINEMAS.get(cinema_id)

    if not cinema_name:
        return "Город не найден в списке сети.", False

    init = api_fetch("init")
    report_date = datetime.now().date()
    expected_date = expected_report_date(report_date)
    target_date = parse_date(init["max_dt"])

    if target_date < expected_date:
        return (
            build_unavailable_report(report_date, expected_date, target_date),
            False,
        )

    month_start = target_date.replace(day=1)
    period_data = read_period(month_start, target_date, cinemas=[cinema_id])
    month_rows = period_data.get("rows", [])
    day_rows = [row for row in month_rows if row.get("dt") == date_str(target_date)]

    if not day_rows:
        return (
            build_unavailable_report(
                report_date,
                expected_date,
                target_date,
                f"По городу {cinema_name} API вернул 0 строк.",
            ),
            False,
        )

    city = aggregate_rows(day_rows)
    staff_month_by_day = staff_by_date(month_start, target_date, [cinema_id])
    day_metrics = group_by_date(month_rows)

    for day_key, metrics in day_metrics.items():
        payroll = staff_month_by_day.get(day_key, {}).get("payroll", 0)
        metrics["payroll"] = payroll
        metrics["lc"] = payroll / metrics["revenue"] if metrics["revenue"] else None

    staff_day = staff_month_by_day.get(date_str(target_date), {"payroll": 0, "hours": 0})
    city["payroll"] = staff_day["payroll"]
    city["lc"] = staff_day["payroll"] / city["revenue"] if city["revenue"] else None

    month_avg = {
        "percap": month_average(
            day_metrics,
            target_date,
            "percap",
            same_day_type=True,
            exclude_target=True,
        ),
        "avg_check": month_average(
            day_metrics,
            target_date,
            "avg_check",
            same_day_type=True,
            exclude_target=True,
        ),
        "avg_ticket": month_average(
            day_metrics,
            target_date,
            "avg_ticket",
            same_day_type=True,
            exclude_target=True,
        ),
        "viewers_same_type": month_average(
            day_metrics,
            target_date,
            "viewers",
            same_day_type=True,
            exclude_target=True,
        ),
        "bar_revenue": month_average(
            day_metrics,
            target_date,
            "bar_revenue",
            same_day_type=True,
            exclude_target=True,
        ),
        "bar_conversion": month_average(
            day_metrics,
            target_date,
            "bar_conversion",
            same_day_type=True,
            exclude_target=True,
        ),
        "foodcost": month_average(
            day_metrics,
            target_date,
            "foodcost",
            same_day_type=True,
            exclude_target=True,
        ),
        "lc_same_type": month_average(
            day_metrics,
            target_date,
            "lc",
            same_day_type=True,
            exclude_target=True,
        ),
    }

    message = f"🏙 {cinema_name} — ежедневный срез\n"
    message += f"{display_date(report_date)}\n"
    message += f"Период: {display_date(target_date)}\n\n"
    message += "Главные KPI\n"
    message += f"• Выручка: {ru_money(city['revenue'])}\n"
    message += forecast_comparison_line(target_date, city["revenue"], cinema_id)
    message += f"• Бар: {ru_money(city['bar_revenue'])}\n"
    message += f"• Доля бара: {ru_pct(city['bar_share'])}\n"
    message += f"• Билеты: {ru_money(city['tickets_revenue'])}\n"
    message += f"• Зрители: {ru_num(city['viewers'])}\n\n"
    message += "Сравнение со средним месяца\n"
    message += student_promo_note(target_date)
    message += f"• Percap: {ru_rub(city['percap'])} | среднее по {comparison_day_type_label(target_date)}: {ru_rub(month_avg['percap'])} | {delta_text(city['percap'], month_avg['percap'])}\n"
    message += f"• Средний чек: {ru_rub(city['avg_check'])} | среднее по {comparison_day_type_label(target_date)}: {ru_rub(month_avg['avg_check'])} | {delta_text(city['avg_check'], month_avg['avg_check'])}\n"
    message += f"• Средняя цена билета: {ru_rub(city['avg_ticket'])} | среднее по {comparison_day_type_label(target_date)}: {ru_rub(month_avg['avg_ticket'])} | {delta_text(city['avg_ticket'], month_avg['avg_ticket'])}\n"
    message += f"• Зрители: {ru_num(city['viewers'])} | среднее по {comparison_day_type_label(target_date)}: {ru_num(month_avg['viewers_same_type'])} | {delta_text(city['viewers'], month_avg['viewers_same_type'])}\n"
    message += f"• Бар: {ru_money(city['bar_revenue'])} | среднее по {comparison_day_type_label(target_date)}: {ru_money(month_avg['bar_revenue'])} | {delta_text(city['bar_revenue'], month_avg['bar_revenue'])}\n"
    message += f"• Конверсия бара: {ru_pct(city['bar_conversion'])} | среднее по {comparison_day_type_label(target_date)}: {ru_pct(month_avg['bar_conversion'])} | {delta_text(city['bar_conversion'], month_avg['bar_conversion'])}\n"
    message += f"• FC: {ru_pct(city['foodcost'])} | среднее по {comparison_day_type_label(target_date)}: {ru_pct(month_avg['foodcost'])} | {delta_text(city['foodcost'], month_avg['foodcost'], lower_is_better=True)}\n"
    message += f"• LC: {ru_pct(city['lc'])} | среднее по {comparison_day_type_label(target_date)}: {ru_pct(month_avg['lc_same_type'])} | {delta_text(city['lc'], month_avg['lc_same_type'], lower_is_better=True, good_word=True)}\n"
    message += f"• ФОТ: {ru_money_compact(city['payroll'])}\n"

    return message.strip(), True


def compact_date_ranges(days):
    dates = sorted(parse_date(day) if isinstance(day, str) else day for day in days)

    if not dates:
        return "нет данных"

    ranges = []
    start = dates[0]
    previous = dates[0]

    for current in dates[1:]:
        if current == previous + timedelta(days=1):
            previous = current
            continue

        ranges.append((start, previous))
        start = previous = current

    ranges.append((start, previous))

    result = []
    for range_start, range_end in ranges:
        if range_start == range_end:
            result.append(display_date(range_start))
        else:
            result.append(f"{display_date(range_start)}–{display_date(range_end)}")

    return ", ".join(result)


def build_month_result_payload():
    init = api_fetch("init")
    report_date = datetime.now().date()
    target_date = parse_date(init["max_dt"])
    month_start = target_date.replace(day=1)
    days_in_month = calendar.monthrange(target_date.year, target_date.month)[1]
    period_data = read_period(month_start, target_date)
    month_rows = period_data.get("rows", [])

    if not month_rows:
        return (
            build_unavailable_report(
                report_date,
                month_start,
                target_date,
                "За текущий месяц API вернул 0 строк.",
            ),
            False,
        )

    month = aggregate_rows(month_rows)
    day_metrics = group_by_date(month_rows)
    data_days = sorted(day_metrics)
    staff_month_by_day = staff_by_date(month_start, target_date)
    payroll = sum(item.get("payroll", 0) for item in staff_month_by_day.values())
    hours = sum(item.get("hours", 0) for item in staff_month_by_day.values())
    lc = payroll / month["revenue"] if month["revenue"] else None
    days_with_data = max(1, len(data_days))
    projected_revenue = (
        projected_month_revenue_by_pnl(target_date, month["revenue"])
        or month["revenue"] / days_with_data * days_in_month
    )
    projected_bar = month["bar_revenue"] / days_with_data * days_in_month
    projected_viewers = month["viewers"] / days_with_data * days_in_month
    projected_payroll = payroll_projection_by_day_type(staff_month_by_day, month_start)
    projected_foodcost = month["foodcost_amount"] / days_with_data * days_in_month
    projected_ebitda = projected_revenue - projected_payroll - projected_foodcost
    projected_percap = projected_bar / projected_viewers if projected_viewers else None
    city_metrics = city_day_metrics(target_date, month_rows)
    best, worst = best_worst_cities(city_metrics)
    month_plan = current_month_dynamics_plan(month_rows, month_start, target_date)
    year_forecast = actual_period_totals(target_date.replace(month=1, day=1), target_date)
    year_remaining_forecast = current_month_dynamics_forecast(
        month_rows,
        target_date + timedelta(days=1),
        target_date.replace(month=12, day=31),
    )
    year_plan = {
        "revenue": apply_plan_uplift(year_forecast["revenue"] + year_remaining_forecast["revenue"]),
        "viewers": apply_plan_uplift(year_forecast["viewers"] + year_remaining_forecast["viewers"]),
    }

    message = f"📆 ЛЮМЕН — РЕЗУЛЬТАТ МЕСЯЦА\n{display_date(report_date)}\n"
    message += f"Месяц: {month_year_label(month_start)}\n"
    message += f"Данные участвуют за дни: {compact_date_ranges(data_days)}\n"
    message += f"Последний доступный день: {display_date(target_date)}\n\n"

    message += "Итог месяца на сейчас\n"
    message += f"• Выручка: {ru_money(month['revenue'])}\n"
    message += f"• Билеты: {ru_money(month['tickets_revenue'])}\n"
    message += f"• Бар: {ru_money(month['bar_revenue'])}\n"
    message += f"• Зрители: {ru_num(month['viewers'])}\n"
    message += f"• Percap: {ru_rub(month['percap'])}\n"
    message += f"• Доля бара: {ru_pct(month['bar_share'])}\n"
    message += f"• Конверсия бара: {ru_pct(month['bar_conversion'])}\n"
    message += f"• FC: {ru_pct(month['foodcost'])}\n"
    message += f"• ФОТ: {ru_money(payroll)}\n"
    message += f"• Часы работы: {ru_num(hours)}\n"
    message += f"• LC: {ru_pct(lc)}\n\n"

    message += "План-факт месяца\n"
    message += f"• Выручка: факт {ru_money(month['revenue'])} | план {ru_money(month_plan['revenue'])} | {delta_plain(month['revenue'], month_plan['revenue'])}\n"
    message += f"• Зрители: факт {ru_num(month['viewers'])} | план {ru_num(month_plan['viewers'])} | {delta_plain(month['viewers'], month_plan['viewers'])}\n\n"

    message += "Прогноз на полный месяц\n"
    message += f"• Выручка: {ru_money_mln(projected_revenue)}\n"
    message += f"• Бар: {ru_money_mln(projected_bar)}\n"
    message += f"• Зрители: {ru_num(projected_viewers)}\n"
    message += f"• Percap: {ru_rub(projected_percap)}\n"
    message += f"• ФОТ: {ru_money_mln(projected_payroll)}\n"
    message += f"• EBITDA: {ru_money_mln(projected_ebitda)}\n"

    message += "\nПлан 2026 по динамике текущего месяца (+10%)\n"
    message += f"• Выручка: {ru_money_mln(year_plan['revenue'])}\n"
    message += f"• Зрители: {ru_num(year_plan['viewers'])}\n"

    if best:
        message += "\nЛучшие города по последнему дню\n"
        for index, item in enumerate(best, 1):
            message += f"{index}. {item}\n"

    if worst:
        message += "\nЗоны внимания по последнему дню\n"
        for item in worst:
            message += f"• {item}\n"

    return message.strip(), True


def seasonality_share_to_date(target_date, key):
    year_start = target_date.replace(month=1, day=1)
    year_end = target_date.replace(month=12, day=31)
    base_ytd_rows = read_period_rows(shift_year(year_start, -1), shift_year(target_date, -1))
    base_year_rows = read_period_rows(shift_year(year_start, -1), shift_year(year_end, -1))
    base_ytd = aggregate_rows(base_ytd_rows)
    base_year = aggregate_rows(base_year_rows)

    if base_year.get(key):
        return base_ytd.get(key, 0) / base_year[key]

    days_in_year = 366 if calendar.isleap(target_date.year) else 365
    return target_date.timetuple().tm_yday / days_in_year


def plan_fact_status(forecast, plan):
    if forecast is None or plan in (None, 0):
        return "⚪ Нет данных"

    ratio = forecast / plan

    if ratio >= 1:
        return "🟢 Выше плана"
    if ratio >= 0.95:
        return "🟡 Близко к плану"
    return "🔴 Ниже плана"


def plan_fact_metric_lines(label, actual, forecast, plan, remaining_days):
    gap = (forecast or 0) - (plan or 0)
    completion = forecast / plan if plan else None
    required_left = max((plan or 0) - (actual or 0), 0)
    required_per_day = required_left / remaining_days if remaining_days > 0 else required_left
    sign = "+" if gap > 0 else "-"

    return (
        f"{label}\n"
        f"• Факт сейчас: {ru_money(actual) if label == 'Выручка' else ru_num(actual)}\n"
        f"• Прогноз: {ru_money(forecast) if label == 'Выручка' else ru_num(forecast)}\n"
        f"• План: {ru_money(plan) if label == 'Выручка' else ru_num(plan)}\n"
        f"• Выполнение по прогнозу: {ru_pct(completion)}\n"
        f"• Разрыв к плану: {sign}{ru_money(abs(gap)) if label == 'Выручка' else ru_num(abs(gap))}\n"
        f"• Нужно в среднем до конца: {ru_money(required_per_day) if label == 'Выручка' else ru_num(required_per_day)} в день\n"
    )


def plan_fact_daily_gap(actual, forecast, plan, remaining_days):
    if remaining_days <= 0:
        return 0

    required_per_day = max((plan or 0) - (actual or 0), 0) / remaining_days
    forecast_per_day = max((forecast or 0) - (actual or 0), 0) / remaining_days
    return required_per_day - forecast_per_day


def build_plan_fact_payload():
    init = api_fetch("init")
    report_date = datetime.now().date()
    target_date = parse_date(init["max_dt"])
    month_start = target_date.replace(day=1)
    month_end = target_date.replace(day=calendar.monthrange(target_date.year, target_date.month)[1])
    year_start = target_date.replace(month=1, day=1)
    year_end = target_date.replace(month=12, day=31)
    month_rows = read_period(month_start, target_date).get("rows", [])
    year_rows = read_period_rows(year_start, target_date)

    if not month_rows or not year_rows:
        return (
            build_unavailable_report(
                report_date,
                month_start,
                target_date,
                "Для план-факта API вернул недостаточно строк.",
            ),
            False,
        )

    month_actual = aggregate_rows(month_rows)
    year_actual = aggregate_rows(year_rows)
    month_day_metrics = group_by_date(month_rows)
    days_with_month_data = max(1, len(month_day_metrics))
    days_in_month = calendar.monthrange(target_date.year, target_date.month)[1]
    month_forecast = current_month_dynamics_forecast(month_rows, month_start, month_end)
    month_plan = current_month_dynamics_plan(month_rows, month_start, month_end)
    year_remaining_forecast = current_month_dynamics_forecast(
        month_rows,
        target_date + timedelta(days=1),
        year_end,
    )
    year_forecast = {
        "revenue": year_actual["revenue"] + year_remaining_forecast["revenue"],
        "viewers": year_actual["viewers"] + year_remaining_forecast["viewers"],
    }
    year_plan = {
        "revenue": apply_plan_uplift(year_forecast["revenue"]),
        "viewers": apply_plan_uplift(year_forecast["viewers"]),
    }
    month_remaining_days = max((month_end - target_date).days, 0)
    year_remaining_days = max((year_end - target_date).days, 0)

    message = f"📊 ЛЮМЕН — ПЛАН-ФАКТ\n{display_date(report_date)}\n"
    message += f"Последний доступный день: {display_date(target_date)}\n\n"

    message += f"МЕСЯЦ — {month_year_label(month_start)}\n"
    message += f"Статус: {plan_fact_status(month_forecast['revenue'], month_plan['revenue'])}\n"
    message += f"Прошло дней: {days_with_month_data} из {days_in_month}\n\n"
    message += plan_fact_metric_lines(
        "Выручка",
        month_actual["revenue"],
        month_forecast["revenue"],
        month_plan["revenue"],
        month_remaining_days,
    )
    message += "\n"
    message += plan_fact_metric_lines(
        "Зрители",
        month_actual["viewers"],
        month_forecast["viewers"],
        month_plan["viewers"],
        month_remaining_days,
    )

    message += f"\nГОД — {target_date.year}\n"
    message += f"Статус: {plan_fact_status(year_forecast['revenue'], year_plan['revenue'])}\n\n"
    message += plan_fact_metric_lines(
        "Выручка",
        year_actual["revenue"],
        year_forecast["revenue"],
        year_plan["revenue"],
        year_remaining_days,
    )
    message += "\n"
    message += plan_fact_metric_lines(
        "Зрители",
        year_actual["viewers"],
        year_forecast["viewers"],
        year_plan["viewers"],
        year_remaining_days,
    )

    month_revenue_gap_per_day = plan_fact_daily_gap(
        month_actual["revenue"],
        month_forecast["revenue"],
        month_plan["revenue"],
        month_remaining_days,
    )
    month_viewers_gap_per_day = plan_fact_daily_gap(
        month_actual["viewers"],
        month_forecast["viewers"],
        month_plan["viewers"],
        month_remaining_days,
    )

    message += "\n\nКороткий вывод\n"
    if month_revenue_gap_per_day > 0 or month_viewers_gap_per_day > 0:
        message += (
            f"• Чтобы выйти на план месяца, текущий прогноз нужно усилить примерно на "
            f"{ru_money(month_revenue_gap_per_day)} и {ru_num(month_viewers_gap_per_day)} зрителей в день.\n"
        )
    else:
        message += "• По месяцу текущий прогноз закрывает план.\n"

    message += (
        f"• По году прогноз закрывает {ru_pct(year_forecast['revenue'] / year_plan['revenue'] if year_plan['revenue'] else None)} "
        f"плана по выручке и {ru_pct(year_forecast['viewers'] / year_plan['viewers'] if year_plan['viewers'] else None)} по зрителям."
    )

    return message.strip(), True


def turnover_days_for_rows(rows):
    weighted_total = 0
    weight = 0
    values = []

    for row in rows:
        value = row.get("turnover_days")

        if value is None:
            continue

        value = float(value or 0)

        if value <= 0:
            continue

        bar_revenue = float(row.get("revenue_bar") or 0)
        values.append(value)

        if bar_revenue > 0:
            weighted_total += value * bar_revenue
            weight += bar_revenue

    if weight:
        return weighted_total / weight

    return average(values)


def build_turnover_month_payload():
    init = api_fetch("init")
    report_date = datetime.now().date()
    target_date = parse_date(init["max_dt"])
    month_start = target_date.replace(day=1)
    period_data = read_period(month_start, target_date)
    rows = period_data.get("rows", [])

    if not rows:
        return (
            build_unavailable_report(
                report_date,
                month_start,
                target_date,
                "За текущий месяц API вернул 0 строк.",
            ),
            False,
        )

    rows_by_day = defaultdict(list)
    rows_by_city = defaultdict(list)

    for row in rows:
        rows_by_day[row.get("dt")].append(row)
        rows_by_city[int(row.get("cinema_id"))].append(row)

    data_days = sorted(day for day in rows_by_day if day)
    daily = [
        (day, turnover_days_for_rows(rows_by_day[day]))
        for day in data_days
    ]
    daily = [(day, value) for day, value in daily if value is not None]
    current_value = daily[-1][1] if daily else None
    first_value = daily[0][1] if daily else None
    average_value = average([value for _, value in daily])
    best_day = min(daily, key=lambda item: item[1]) if daily else None
    worst_day = max(daily, key=lambda item: item[1]) if daily else None

    city_rows = []
    for cinema_id, city_rows_source in rows_by_city.items():
        city_value = turnover_days_for_rows(city_rows_source)

        if city_value is None:
            continue

        last_day_rows = [
            row for row in city_rows_source
            if row.get("dt") == date_str(target_date)
        ]
        last_value = turnover_days_for_rows(last_day_rows)
        city_rows.append((
            city_value,
            last_value,
            CINEMAS.get(cinema_id, str(cinema_id)),
        ))

    city_rows.sort(key=lambda item: item[0])
    best_cities = city_rows[:5]
    worst_cities = sorted(city_rows, key=lambda item: item[0], reverse=True)[:5]

    message = f"📦 ЛЮМЕН — ОБОРАЧИВАЕМОСТЬ ТОВАРА\n{display_date(report_date)}\n"
    message += f"Месяц: {month_year_label(month_start)}\n"
    message += f"Данные участвуют за дни: {compact_date_ranges(data_days)}\n"
    message += f"Последний доступный день: {display_date(target_date)}\n\n"

    message += "Сеть\n"
    message += f"• Последний день: {ru_num(current_value)} дней\n"
    message += f"• Среднее месяца: {ru_num(average_value)} дней\n"
    message += f"• К началу месяца: {delta_plain(current_value, first_value, lower_is_better=True)}\n"

    if best_day:
        message += f"• Лучший день: {display_date(parse_date(best_day[0]))} — {ru_num(best_day[1])} дней\n"

    if worst_day:
        message += f"• Худший день: {display_date(parse_date(worst_day[0]))} — {ru_num(worst_day[1])} дней\n"

    message += "\nДинамика по дням\n"
    for day, value in daily[-10:]:
        message += f"• {display_date(parse_date(day))}: {ru_num(value)} дней\n"

    if best_cities:
        message += "\nБыстрее оборачиваются\n"
        for index, (value, last_value, name) in enumerate(best_cities, 1):
            message += f"{index}. {name}: {ru_num(value)} дней"

            if last_value is not None:
                message += f" | последний день {ru_num(last_value)}"

            message += "\n"

    if worst_cities:
        message += "\nЗоны внимания\n"
        for value, last_value, name in worst_cities:
            message += f"• {name}: {ru_num(value)} дней"

            if last_value is not None:
                message += f" | последний день {ru_num(last_value)}"

            message += "\n"

    message += "\nЧем меньше дней, тем быстрее товар превращается в продажи."
    return message.strip(), True


def comparable_average(day_metrics, target_date, key):
    value = month_average(
        day_metrics,
        target_date,
        key,
        same_day_type=True,
        exclude_target=False,
    )

    if value is not None:
        return value

    return month_average(day_metrics, target_date, key, exclude_target=False)


def compare_three_months_line(label, current, average_value, formatter, lower_is_better=False):
    return (
        f"• {label}: {formatter(current)} | среднее: {formatter(average_value)} | "
        f"{delta_text(current, average_value, lower_is_better=lower_is_better)}"
    )


def build_three_month_compare_payload():
    init = api_fetch("init")
    report_date = datetime.now().date()
    target_date = parse_date(init["max_dt"])
    period_start = shift_months(target_date, -3)
    period_end = target_date - timedelta(days=1)
    period_rows = read_period_rows(period_start, period_end)
    day_rows = read_period(target_date, target_date).get("rows", [])

    if not day_rows:
        return (
            build_unavailable_report(
                report_date,
                target_date,
                target_date,
                "Для сравнения API вернул 0 строк за последний день.",
            ),
            False,
        )

    if not period_rows:
        return (
            build_unavailable_report(
                report_date,
                period_start,
                target_date,
                "Для сравнения за 3 месяца API вернул 0 строк.",
            ),
            False,
        )

    current = aggregate_rows(day_rows)
    day_metrics = group_by_date(period_rows)
    staff_period_by_day = staff_by_date(period_start, period_end)

    for day_key, metrics in day_metrics.items():
        payroll = staff_period_by_day.get(day_key, {}).get("payroll", 0)
        metrics["payroll"] = payroll
        metrics["lc"] = payroll / metrics["revenue"] if metrics["revenue"] else None

    staff_day = staff_by_date(target_date, target_date).get(date_str(target_date), {"payroll": 0, "hours": 0})
    current["payroll"] = staff_day["payroll"]
    current["lc"] = staff_day["payroll"] / current["revenue"] if current["revenue"] else None
    current_turnover = turnover_days_for_rows(day_rows)

    rows_by_day = defaultdict(list)
    for row in period_rows:
        rows_by_day[row.get("dt")].append(row)

    turnover_metrics = {}
    for day_key, rows in rows_by_day.items():
        value = turnover_days_for_rows(rows)

        if value is not None:
            turnover_metrics[day_key] = {"turnover": value}

    averages = {
        "percap": comparable_average(day_metrics, target_date, "percap"),
        "avg_check": comparable_average(day_metrics, target_date, "avg_check"),
        "avg_ticket": comparable_average(day_metrics, target_date, "avg_ticket"),
        "viewers": comparable_average(day_metrics, target_date, "viewers"),
        "foodcost": comparable_average(day_metrics, target_date, "foodcost"),
        "turnover": comparable_average(turnover_metrics, target_date, "turnover"),
        "lc": comparable_average(day_metrics, target_date, "lc"),
        "payroll": comparable_average(day_metrics, target_date, "payroll"),
    }
    data_days = sorted(day_metrics)
    comparable_days_count = sum(
        1
        for day_key in data_days
        if comparison_day_type(parse_date(day_key)) == comparison_day_type(target_date)
    )

    message = f"📊 ЛЮМЕН — СРАВНЕНИЕ 3 МЕСЯЦА\n{display_date(report_date)}\n"
    message += f"Период: {display_date(target_date)}\n"
    message += f"База сравнения: {display_date(period_start)}–{display_date(period_end)}\n"
    message += f"Тип дня: {comparison_day_type_label(target_date)}"

    if comparable_days_count:
        message += f" · {comparable_days_count} дней в базе\n"
    else:
        message += " · база по всем дням\n"

    promo_note = student_promo_note(target_date)
    if promo_note:
        message += promo_note

    message += "\n"
    message += compare_three_months_line("Percap", current["percap"], averages["percap"], ru_rub) + "\n"
    message += compare_three_months_line("Средний чек", current["avg_check"], averages["avg_check"], ru_rub) + "\n"
    message += compare_three_months_line("Средняя цена билета", current["avg_ticket"], averages["avg_ticket"], ru_rub) + "\n"
    message += compare_three_months_line("Зрители", current["viewers"], averages["viewers"], ru_num) + "\n"
    message += compare_three_months_line("FC", current["foodcost"], averages["foodcost"], ru_pct, lower_is_better=True) + "\n"
    message += compare_three_months_line("Оборачиваемость", current_turnover, averages["turnover"], lambda value: f"{ru_num(value)} дней", lower_is_better=True) + "\n"
    message += compare_three_months_line("LC", current["lc"], averages["lc"], ru_pct, lower_is_better=True) + "\n"
    message += compare_three_months_line("ФОТ", current["payroll"], averages["payroll"], ru_money_compact, lower_is_better=True)

    return message.strip(), True


def build_report_payload():
    init = api_fetch("init")
    report_date = datetime.now().date()
    expected_date = expected_report_date(report_date)
    target_date = parse_date(init["max_dt"])

    if target_date < expected_date:
        return (
            build_unavailable_report(report_date, expected_date, target_date),
            False,
        )

    month_start = target_date.replace(day=1)
    days_in_month = calendar.monthrange(target_date.year, target_date.month)[1]
    last_year_month_start, last_year_month_end = last_year_month_bounds(month_start)

    period_data = read_period(month_start, target_date)
    last_year_period_data = read_period(last_year_month_start, last_year_month_end)
    month_rows = period_data.get("rows", [])
    last_year_rows = last_year_period_data.get("rows", [])
    day_rows = [row for row in month_rows if row.get("dt") == date_str(target_date)]

    if not day_rows:
        return (
            build_unavailable_report(
                report_date,
                expected_date,
                target_date,
                "За эту дату API вернул 0 строк.",
            ),
            False,
        )

    day = aggregate_rows(day_rows)
    day_metrics = group_by_date(month_rows)
    last_year_month = aggregate_rows(last_year_rows)
    staff_month_by_day = staff_by_date(month_start, target_date)
    turnover_current = turnover_days_for_rows(day_rows)
    previous_date = target_date - timedelta(days=1)
    previous_turnover_rows = [
        row for row in month_rows
        if row.get("dt") == date_str(previous_date)
    ]
    turnover_previous = turnover_days_for_rows(previous_turnover_rows)

    for day_key, metrics in day_metrics.items():
        payroll = staff_month_by_day.get(day_key, {}).get("payroll", 0)
        metrics["payroll"] = payroll
        metrics["lc"] = payroll / metrics["revenue"] if metrics["revenue"] else None

    month_avg = {
        "percap": month_average(
            day_metrics,
            target_date,
            "percap",
            same_day_type=True,
            exclude_target=True,
        ),
        "avg_check": month_average(
            day_metrics,
            target_date,
            "avg_check",
            same_day_type=True,
            exclude_target=True,
        ),
        "avg_ticket": month_average(
            day_metrics,
            target_date,
            "avg_ticket",
            same_day_type=True,
            exclude_target=True,
        ),
        "viewers_same_type": month_average(
            day_metrics,
            target_date,
            "viewers",
            same_day_type=True,
            exclude_target=True,
        ),
        "lc_same_type": month_average(
            day_metrics,
            target_date,
            "lc",
            same_day_type=True,
            exclude_target=True,
        ),
        "foodcost": month_average(
            day_metrics,
            target_date,
            "foodcost",
            same_day_type=True,
            exclude_target=True,
        ),
    }
    staff_day = staff_month_by_day.get(date_str(target_date), {"payroll": 0, "hours": 0})
    day["payroll"] = staff_day["payroll"]
    day["lc"] = staff_day["payroll"] / day["revenue"] if day["revenue"] else None

    month_revenue = sum(metrics["revenue"] for metrics in day_metrics.values())
    month_bar = sum(metrics["bar_revenue"] for metrics in day_metrics.values())
    month_viewers = sum(metrics["viewers"] for metrics in day_metrics.values())
    month_foodcost_amount = sum(metrics["foodcost_amount"] for metrics in day_metrics.values())
    days_with_data = max(1, len(day_metrics))

    projected_revenue_runrate = month_revenue / days_with_data * days_in_month
    projected_revenue = (
        projected_month_revenue_by_pnl(target_date, month_revenue)
        or projected_revenue_runrate
    )
    projected_bar = month_bar / days_with_data * days_in_month
    projected_viewers = month_viewers / days_with_data * days_in_month
    projected_payroll = payroll_projection_by_day_type(staff_month_by_day, month_start)
    projected_foodcost = month_foodcost_amount / days_with_data * days_in_month
    projected_ebitda = projected_revenue - projected_payroll - projected_foodcost
    projected_percap = projected_bar / projected_viewers if projected_viewers else None
    last_year_percap = (
        last_year_month["bar_revenue"] / last_year_month["viewers"]
        if last_year_month["viewers"] else None
    )
    last_year_label = month_year_label(last_year_month_start)
    message = f"📊 ЛЮМЕН — ЕЖЕДНЕВНЫЙ СРЕЗ\n{display_date(report_date)}\n"
    message += f"Период: {display_date(target_date)}\n\n"

    message += "Главные KPI\n"
    message += f"• Выручка: {ru_money(day['revenue'])}\n"
    message += forecast_comparison_line(target_date, day["revenue"])
    message += f"• Бар: {ru_money(day['bar_revenue'])}\n"
    message += f"• Доля бара: {ru_pct(day['bar_share'])}\n"
    message += f"• Билеты: {ru_money(day['tickets_revenue'])}\n"
    message += f"• Зрители: {ru_num(day['viewers'])}\n\n"

    message += "Сравнение со средним месяца\n"
    message += student_promo_note(target_date)
    message += f"• Percap: {ru_rub(day['percap'])} | среднее по {comparison_day_type_label(target_date)}: {ru_rub(month_avg['percap'])} | {delta_text(day['percap'], month_avg['percap'])}\n"
    message += f"• Средний чек: {ru_rub(day['avg_check'])} | среднее по {comparison_day_type_label(target_date)}: {ru_rub(month_avg['avg_check'])} | {delta_text(day['avg_check'], month_avg['avg_check'])}\n"
    message += f"• Средняя цена билета: {ru_rub(day['avg_ticket'])} | среднее по {comparison_day_type_label(target_date)}: {ru_rub(month_avg['avg_ticket'])} | {delta_text(day['avg_ticket'], month_avg['avg_ticket'])}\n"
    message += f"• Зрители: {ru_num(day['viewers'])} | среднее по {comparison_day_type_label(target_date)}: {ru_num(month_avg['viewers_same_type'])} | {delta_text(day['viewers'], month_avg['viewers_same_type'])}\n"
    message += f"• FC: {ru_pct(day['foodcost'])} | среднее по {comparison_day_type_label(target_date)}: {ru_pct(month_avg['foodcost'])} | {delta_text(day['foodcost'], month_avg['foodcost'], lower_is_better=True)}\n"
    message += f"• Оборачиваемость: {ru_num(turnover_current)} дней | вчера: {ru_num(turnover_previous)} дней | {delta_plain(turnover_current, turnover_previous, lower_is_better=True)}\n"
    message += f"• LC: {ru_pct(day['lc'])} | среднее по {comparison_day_type_label(target_date)}: {ru_pct(month_avg['lc_same_type'])} | {delta_text(day['lc'], month_avg['lc_same_type'], lower_is_better=True, good_word=True)}\n"
    message += f"• ФОТ: {ru_money_compact(day['payroll'])} | Прогноз: {ru_money_compact(projected_payroll)}\n\n"

    message += "📈 ПРОГНОЗ МЕСЯЦА\n"
    message += f"• Выручка: {ru_money_mln(projected_revenue)} | {last_year_label}: {ru_money_mln(last_year_month['revenue'])} | {delta_plain(projected_revenue, last_year_month['revenue'])}\n"
    message += f"• Бар: {ru_money_mln(projected_bar)} | {last_year_label}: {ru_money_mln(last_year_month['bar_revenue'])} | {delta_plain(projected_bar, last_year_month['bar_revenue'])}\n"
    message += f"• Percap: {ru_rub(projected_percap)} | {last_year_label}: {ru_rub(last_year_percap)} | {delta_plain(projected_percap, last_year_percap)}\n"
    message += f"• Зрители: {ru_num(projected_viewers)} | {last_year_label}: {ru_num(last_year_month['viewers'])} | {delta_plain(projected_viewers, last_year_month['viewers'])}\n\n"

    return message.strip(), True


def build_report():
    message, _ = build_report_payload()
    return message


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


def run_once(send=True, send_unavailable=True):
    message, has_fresh_data = build_report_payload()
    print(message)

    if send and (has_fresh_data or send_unavailable):
        for part in split_message(message):
            send_telegram_message(part)

    return has_fresh_data


def run_when_fresh(send=True):
    while True:
        if run_once(send=send, send_unavailable=False):
            return True

        print(
            f"Свежих данных пока нет. Следующая проверка через "
            f"{DATA_CHECK_INTERVAL_SECONDS // 60} мин.",
            flush=True,
        )
        time.sleep(DATA_CHECK_INTERVAL_SECONDS)


def send_start_message(chat_id, username=None, user_id=None):
    if is_owner(user_id):
        send_telegram_message(
            "Готов. Нажми кнопку, чтобы обновить ежедневный срез по Business360.",
            chat_id=chat_id,
            with_button=True,
        )
        return

    if allowed_city_ids(username, user_id):
        send_telegram_message(
            "Готово. Вы будете получать ежедневный срез по своему городу.",
            chat_id=chat_id,
            with_button=False,
        )
        return

    send_telegram_message(
        "Ваш Telegram-аккаунт пока не привязан к городу. Напишите Евгению, чтобы добавить доступ.",
        chat_id=chat_id,
        with_button=False,
    )


def send_city_choice(chat_id, username=None, user_id=None):
    if not is_owner(user_id):
        send_city_reports_for_user(chat_id, username, user_id)
        return

    city_ids = allowed_city_ids(username, user_id)

    if not city_ids:
        send_telegram_message(
            "Для вашего Telegram-аккаунта города не настроены. Напишите Евгению, чтобы добавить доступ.",
            chat_id=chat_id,
            with_button=True,
        )
        return

    send_telegram_message(
        "Выберите город:",
        chat_id=chat_id,
        with_button=False,
        reply_markup=city_choice_keyboard(username, user_id),
    )


def send_city_reports_for_user(chat_id, username=None, user_id=None):
    city_ids = allowed_city_ids(username, user_id)

    if not city_ids:
        send_telegram_message(
            "Для вашего Telegram-аккаунта города не настроены.",
            chat_id=chat_id,
            with_button=is_owner(user_id),
        )
        return

    for cinema_id in city_ids:
        message_text, _ = build_city_report_payload(cinema_id)

        for part in split_message(message_text):
            send_telegram_message(part, chat_id=chat_id, with_button=is_owner(user_id))


def send_refresh_report(chat_id, username=None, user_id=None):
    if is_owner(user_id):
        message_text, _ = build_report_payload()

        for part in split_message(message_text):
            send_telegram_message(part, chat_id=chat_id, with_button=True)

        return

    send_city_reports_for_user(chat_id, username, user_id)


def send_month_result(chat_id, user_id=None):
    if not is_owner(user_id):
        send_telegram_message(
            "Результат месяца доступен только владельцу сети.",
            chat_id=chat_id,
            with_button=False,
        )
        return

    message_text, _ = build_month_result_payload()

    for part in split_message(message_text):
        send_telegram_message(part, chat_id=chat_id, with_button=True)


def send_plan_fact(chat_id, user_id=None):
    if not is_owner(user_id):
        send_telegram_message(
            "План-факт доступен только владельцу сети.",
            chat_id=chat_id,
            with_button=False,
        )
        return

    message_text, _ = build_plan_fact_payload()

    for part in split_message(message_text):
        send_telegram_message(part, chat_id=chat_id, with_button=True)


def send_turnover_month(chat_id, user_id=None):
    if not is_owner(user_id):
        send_telegram_message(
            "Оборачиваемость товара доступна только владельцу сети.",
            chat_id=chat_id,
            with_button=False,
        )
        return

    message_text, _ = build_turnover_month_payload()

    for part in split_message(message_text):
        send_telegram_message(part, chat_id=chat_id, with_button=True)


def send_three_month_compare(chat_id, user_id=None):
    if not is_owner(user_id):
        send_telegram_message(
            "Сравнение за 3 месяца доступно только владельцу сети.",
            chat_id=chat_id,
            with_button=False,
        )
        return

    message_text, _ = build_three_month_compare_payload()

    for part in split_message(message_text):
        send_telegram_message(part, chat_id=chat_id, with_button=True)


def send_movie_potential(chat_id, user_id=None):
    if not is_owner(user_id):
        send_telegram_message(
            "Потенциал фильмов доступен только владельцу сети.",
            chat_id=chat_id,
            with_button=False,
        )
        return

    message_text = build_movie_potential_payload()

    for part in split_message(message_text):
        send_telegram_message(part, chat_id=chat_id, with_button=True)


def send_daily_reports():
    has_fresh_data = False
    owner_message, has_fresh_data = build_report_payload()

    if not has_fresh_data:
        print(owner_message, flush=True)
        return False

    for part in split_message(owner_message):
        send_telegram_message(part, chat_id=CHAT_ID, with_button=True)

    try:
        save_business360_history_snapshot()
    except Exception as error:
        print(f"Не удалось сохранить историю Business360: {error}", flush=True)

    registered = load_registered_chats()

    for username, chat in sorted(registered.items()):
        city_ids = MANAGER_CITY_IDS.get(normalize_username(username), [])

        if not city_ids:
            continue

        chat_id = chat.get("chat_id")

        if not chat_id:
            continue

        print(f"Отправляю городские отчеты @{username} в chat_id={chat_id}", flush=True)

        for cinema_id in city_ids:
            try:
                message_text, city_has_fresh_data = build_city_report_payload(cinema_id)

                if not city_has_fresh_data:
                    continue

                for part in split_message(message_text):
                    send_telegram_message(part, chat_id=chat_id, with_button=False)
            except Exception as error:
                print(f"Не удалось отправить отчет @{username} ({CINEMAS.get(cinema_id)}): {error}", flush=True)

    try:
        save_current_forecast_snapshots()
    except Exception as error:
        print(f"Не удалось сохранить прогноз на следующие дни: {error}", flush=True)

    return True


def answer_callback(callback_query_id, text="Обновляю результаты"):
    try:
        telegram_request("answerCallbackQuery", {
            "callback_query_id": callback_query_id,
            "text": text,
        })
    except Exception as error:
        print(f"Не удалось ответить на callback: {error}", flush=True)


def read_update_offset():
    try:
        return int(UPDATE_OFFSET_FILE.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return 0


def write_update_offset(offset):
    UPDATE_OFFSET_FILE.write_text(str(offset), encoding="utf-8")


def handle_update(update):
    message = update.get("message") or {}
    callback_query = update.get("callback_query") or {}

    if message:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        sender = message.get("from") or {}
        username = sender.get("username")
        user_id = sender.get("id")
        text = (message.get("text") or "").strip()

        register_chat(username, user_id, chat_id)

        command = text.split(maxsplit=1)[0].lower() if text.startswith("/") else text

        if chat_id and command in ("/start", "/menu", "start"):
            send_start_message(chat_id, username, user_id)
        elif chat_id and command in ("/report", "Обновить результаты", "🔄 Обновить результаты"):
            send_refresh_report(chat_id, username, user_id)
        elif chat_id and command == "/month":
            send_month_result(chat_id, user_id)
        elif chat_id and command == "/planfact":
            send_plan_fact(chat_id, user_id)
        elif chat_id and command == "/compare3":
            send_three_month_compare(chat_id, user_id)
        elif chat_id and command == "/movies":
            send_movie_potential(chat_id, user_id)
        elif chat_id and command == "/turnover":
            send_turnover_month(chat_id, user_id)
        elif chat_id and command in ("/cities", "/city"):
            send_city_choice(chat_id, username, user_id)
        elif chat_id and command == "/help":
            send_telegram_message(
                help_text(is_owner(user_id)),
                chat_id=chat_id,
                with_button=is_owner(user_id),
            )
        elif chat_id and command == "/week_forecast":
            if is_owner(user_id):
                answer = revenue_forecast_fact_week_answer()
                for part in split_message(answer):
                    send_telegram_message(part, chat_id=chat_id, with_button=True)
            else:
                send_telegram_message(
                    "Эта команда доступна только владельцу сети.",
                    chat_id=chat_id,
                    with_button=False,
                )
        elif chat_id and command == "/occupancy":
            if is_owner(user_id):
                try:
                    send_lumen_occupancy_current_report(chat_id)
                except Exception as error:
                    send_telegram_message(
                        f"Не смог собрать заполняемость: {error}",
                        chat_id=chat_id,
                        with_button=True,
                    )
            else:
                send_telegram_message(
                    "Отчёт по заполняемости доступен только владельцу сети.",
                    chat_id=chat_id,
                    with_button=False,
                )
        elif chat_id and text:
            if is_owner(user_id):
                try:
                    answer = analytics_question_answer(text)
                except Exception as error:
                    answer = f"Не смог собрать ответ по данным Business360: {error}"

                for part in split_message(answer):
                    send_telegram_message(part, chat_id=chat_id, with_button=True)
            elif allowed_city_ids(username, user_id):
                send_telegram_message(
                    "Вопросы к аналитике пока доступны только владельцу сети.",
                    chat_id=chat_id,
                    with_button=False,
                )

    if callback_query:
        callback_id = callback_query.get("id")
        data = callback_query.get("data")
        sender = callback_query.get("from") or {}
        username = sender.get("username")
        user_id = sender.get("id")
        message = callback_query.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")

        register_chat(username, user_id, chat_id)

        if callback_id:
            callback_text = "Обновляю результаты"
            if str(data).startswith("city_report:"):
                callback_text = "Готовлю отчет по городу"
            elif data == "month_result":
                callback_text = "Считаю результат месяца"
            elif data == "plan_fact":
                callback_text = "Считаю план-факт"
            elif data == "three_month_compare":
                callback_text = "Сравниваю с 3 месяцами"
            elif data == "movie_potential":
                callback_text = "Считаю потенциал фильмов"
            elif data == "turnover_month":
                callback_text = "Считаю оборачиваемость"
            elif data == "choose_city":
                callback_text = "Открываю список городов"

            answer_callback(
                callback_id,
                callback_text,
            )

        if chat_id and data == "refresh_report":
            send_refresh_report(chat_id, username, user_id)
        elif chat_id and data == "month_result":
            send_month_result(chat_id, user_id)
        elif chat_id and data == "plan_fact":
            send_plan_fact(chat_id, user_id)
        elif chat_id and data == "three_month_compare":
            send_three_month_compare(chat_id, user_id)
        elif chat_id and data == "movie_potential":
            send_movie_potential(chat_id, user_id)
        elif chat_id and data == "turnover_month":
            send_turnover_month(chat_id, user_id)
        elif chat_id and data == "choose_city":
            send_city_choice(chat_id, username, user_id)
        elif chat_id and data == "back_to_menu":
            send_start_message(chat_id, username, user_id)
        elif chat_id and str(data).startswith("city_report:"):
            try:
                cinema_id = int(str(data).split(":", 1)[1])
            except (IndexError, ValueError):
                send_telegram_message("Не понял, какой город открыть.", chat_id=chat_id, with_button=is_owner(user_id))
                return

            if cinema_id not in allowed_city_ids(username, user_id):
                send_telegram_message(
                    "Этот город недоступен для вашего Telegram-аккаунта.",
                    chat_id=chat_id,
                    with_button=is_owner(user_id),
                )
                return

            message_text, _ = build_city_report_payload(cinema_id)

            for part in split_message(message_text):
                send_telegram_message(part, chat_id=chat_id, with_button=is_owner(user_id))


def run_bot_listener():
    print("Business360 button listener started.", flush=True)

    while True:
        try:
            result = telegram_request("getUpdates", {
                "offset": read_update_offset(),
                "timeout": 25,
                "allowed_updates": ["message", "callback_query"],
            })

            for update in result.get("result", []):
                update_id = update.get("update_id")

                if update_id is not None:
                    write_update_offset(update_id + 1)

                handle_update(update)
        except Exception as error:
            print(f"Ошибка Business360 listener: {error}", flush=True)
            time.sleep(10)


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
    print("Business360 daily bot started.", flush=True)

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
            if send_daily_reports():
                mark_sent_today()
            else:
                time.sleep(DATA_CHECK_INTERVAL_SECONDS)
        except Exception as error:
            print(f"Ошибка при отправке отчета: {error}", flush=True)
            time.sleep(DATA_CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    if "--setup-menu" in sys.argv:
        setup_telegram_menu()
        print("Telegram menu configured.")
    elif "--occupancy-yesterday-once" in sys.argv:
        send_lumen_occupancy_yesterday_report()
    elif "--occupancy-yesterday-preview" in sys.argv:
        target_day = datetime.now().date() - timedelta(days=1)
        snapshot = load_latest_lumen_occupancy_snapshot(target_day)
        print(build_lumen_occupancy_report(target_day, snapshot))
    elif "--occupancy-collect-once" in sys.argv:
        save_lumen_occupancy_snapshot()
    elif "--occupancy-collect-today-once" in sys.argv:
        save_lumen_occupancy_today_snapshot()
    elif "--occupancy-collect-yesterday-once" in sys.argv:
        save_lumen_occupancy_yesterday_snapshot()
    elif "--occupancy-once" in sys.argv:
        send_lumen_occupancy_report()
    elif "--occupancy-preview" in sys.argv:
        print(build_lumen_occupancy_report())
    elif "--save-forecast" in sys.argv:
        forecasts = save_current_forecast_snapshots()
        network = forecasts.get("network", {})
        next_days = sorted(network.get("forecasts", {}).items())[:3]

        print(f"Прогноз сохранен. Последний факт: {network.get('source_max_dt')}")
        for day, value in next_days:
            print(f"{day}: {ru_money_compact(value)}")
    elif "--once" in sys.argv:
        if already_sent_today():
            print("Отчет за сегодня уже отправлен.", flush=True)
            sys.exit(0)

        while True:
            try:
                if send_daily_reports():
                    mark_sent_today()
                    break

                print(
                    f"Свежих данных пока нет. Следующая проверка через "
                    f"{DATA_CHECK_INTERVAL_SECONDS // 60} мин.",
                    flush=True,
                )
                time.sleep(DATA_CHECK_INTERVAL_SECONDS)
            except Exception as error:
                print(f"Ошибка при разовой отправке отчета: {error}", flush=True)
                time.sleep(DATA_CHECK_INTERVAL_SECONDS)
    elif "--preview" in sys.argv:
        run_once(send=False)
    elif "--bot" in sys.argv:
        run_bot_listener()
    else:
        run_daily()
