import os
import re
import time
import threading
import imaplib
import email
import json
from email.header import decode_header
from html import unescape
from typing import Optional, Tuple, List, Dict, Set, Any

from dotenv import load_dotenv
import telebot
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    BotCommand,
)

# ======================= ЗАГРУЗКА НАСТРОЕК =======================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

KWORK_FROM = os.getenv("KWORK_FROM", "news@kwork.ru")
WORKZILLA_FROM = os.getenv("WORKZILLA_FROM", "info@work-zilla.com")
FREELANCEJOB_FROM = os.getenv("FREELANCEJOB_FROM", "noreply@robot.freelance.ru")

MAILS_LIMIT = int(os.getenv("MAILS_LIMIT", "10"))
WEBAPP_BASE_URL = os.getenv(
    "WEBAPP_BASE_URL",
    "https://komplektofflabs.xyz/webapp/index.php",
).rstrip("?")

SETTINGS_FILE = os.getenv("SETTINGS_FILE", "chat_settings.json")

if not BOT_TOKEN or not GMAIL_USER or not GMAIL_APP_PASSWORD:
    raise RuntimeError("Не заданы BOT_TOKEN / GMAIL_USER / GMAIL_APP_PASSWORD в .env")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ======================= ИСТОЧНИКИ =======================

SOURCES: Dict[str, str] = {
    "kwork": KWORK_FROM,
    "workzilla": WORKZILLA_FROM,
    "freelancejob": FREELANCEJOB_FROM,
}

SOURCE_META: Dict[str, Dict[str, str]] = {
    "kwork": {"name": "Kwork", "icon": "🟧"},
    "workzilla": {"name": "Work-Zilla", "icon": "🟦"},
    "freelancejob": {"name": "Freelance.ru", "icon": "🟪"},
}

SOURCE_ORDER: List[str] = ["kwork", "workzilla", "freelancejob"]

# Последний UID по каждому источнику (для вотчера)
last_uids: Dict[str, Optional[int]] = {src: None for src in SOURCES}

# ======================= СОСТОЯНИЕ ЧАТОВ =======================

chat_ids: Set[int] = set()
chat_ids_lock = threading.Lock()

chat_settings: Dict[int, Dict[str, Any]] = {}
chat_settings_lock = threading.Lock()


# ======================= РАБОТА С НАСТРОЙКАМИ ЧАТОВ =======================

def load_chat_settings() -> None:
    """Загрузка настроек чатов из JSON-файла."""
    global chat_settings, chat_ids
    if not os.path.exists(SETTINGS_FILE):
        print("[settings] файла настроек нет, старт с нуля")
        return

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        print("load_chat_settings error:", e)
        return

    loaded: Dict[int, Dict[str, Any]] = {}

    for chat_id_str, cfg in raw.items():
        try:
            chat_id = int(chat_id_str)
        except ValueError:
            continue

        sources = cfg.get("sources")
        if not isinstance(sources, list):
            sources = list(SOURCES.keys())

        notifications = bool(cfg.get("notifications", True))
        title = cfg.get("title") or ""
        chat_type = cfg.get("type") or ""

        loaded[chat_id] = {
            "sources": [s for s in sources if s in SOURCES],
            "notifications": notifications,
            "title": title,
            "type": chat_type,
        }

    with chat_settings_lock:
        chat_settings = loaded

    with chat_ids_lock:
        chat_ids = {cid for cid, cfg in loaded.items() if cfg.get("notifications", True)}

    print(f"[settings] loaded {len(chat_settings)} chats from {SETTINGS_FILE}")
    print(f"[settings] chats with notifications ON: {chat_ids}")


def save_chat_settings() -> None:
    """Сохранение настроек чатов в JSON-файл."""
    try:
        with chat_settings_lock:
            to_save: Dict[str, Any] = {}
            for chat_id, cfg in chat_settings.items():
                to_save[str(chat_id)] = {
                    "sources": [
                        s for s in cfg.get("sources", list(SOURCES.keys()))
                        if s in SOURCES
                    ],
                    "notifications": bool(cfg.get("notifications", True)),
                    "title": cfg.get("title", ""),
                    "type": cfg.get("type", ""),
                }

        tmp_file = SETTINGS_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(to_save, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, SETTINGS_FILE)
        print(f"[settings] saved to {SETTINGS_FILE}")
    except Exception as e:
        print("save_chat_settings error:", e)


def ensure_chat_config(chat_id: int, title: str = "", chat_type: str = "") -> None:
    """Убедиться, что для чата есть конфиг. Если нет — создать с дефолтами."""
    created = False
    with chat_settings_lock:
        cfg = chat_settings.get(chat_id)
        if cfg is None:
            cfg = {
                "sources": list(SOURCES.keys()),
                "notifications": True,
                "title": title,
                "type": chat_type,
            }
            chat_settings[chat_id] = cfg
            created = True
        else:
            if title and not cfg.get("title"):
                cfg["title"] = title
            if chat_type and not cfg.get("type"):
                cfg["type"] = chat_type

    if created:
        with chat_ids_lock:
            chat_ids.add(chat_id)
        print(f"[settings] new chat registered: {chat_id}")
        save_chat_settings()


def set_chat_notifications(chat_id: int, enabled: bool) -> None:
    """Включить/выключить уведомления в чате."""
    with chat_settings_lock:
        cfg = chat_settings.get(chat_id)
        if cfg is None:
            cfg = {
                "sources": list(SOURCES.keys()),
                "notifications": enabled,
                "title": "",
                "type": "",
            }
            chat_settings[chat_id] = cfg
        else:
            cfg["notifications"] = enabled

    with chat_ids_lock:
        if enabled:
            chat_ids.add(chat_id)
        else:
            chat_ids.discard(chat_id)

    print(f"[settings] chat {chat_id} notifications -> {enabled}")
    save_chat_settings()


def toggle_chat_source(chat_id: int, source: str) -> bool:
    """
    Переключить источник для чата.
    Возвращает True, если источник включён после переключения, иначе False.
    """
    if source not in SOURCES:
        return False

    with chat_settings_lock:
        cfg = chat_settings.get(chat_id)
        if cfg is None:
            cfg = {
                "sources": list(SOURCES.keys()),
                "notifications": True,
                "title": "",
                "type": "",
            }
            chat_settings[chat_id] = cfg

        sources: List[str] = cfg.get("sources", list(SOURCES.keys()))
        if source in sources:
            sources.remove(source)
            enabled = False
        else:
            sources.append(source)
            enabled = True
        cfg["sources"] = [s for s in sources if s in SOURCES]

    save_chat_settings()
    print(f"[settings] chat {chat_id} source {source} -> {enabled}")
    return enabled


def get_chat_config(chat_id: int) -> Dict[str, Any]:
    """Получить конфиг чата (с дефолтами)."""
    with chat_settings_lock:
        cfg = chat_settings.get(chat_id)
        if cfg is None:
            cfg = {
                "sources": list(SOURCES.keys()),
                "notifications": True,
                "title": "",
                "type": "",
            }
            chat_settings[chat_id] = cfg
    return cfg


def build_settings_text(chat_id: int) -> str:
    """Текст настроек чата для /settings."""
    cfg = get_chat_config(chat_id)
    enabled_sources = set(cfg.get("sources", list(SOURCES.keys())))
    notif = bool(cfg.get("notifications", True))

    lines: List[str] = [
        "⚙️ <b>Настройки этого чата</b>\n",
        f"🔔 Уведомления: <b>{'ВКЛ' if notif else 'ВЫКЛ'}</b>\n",
        "<b>Источники:</b>",
    ]

    for src in SOURCE_ORDER:
        meta = SOURCE_META[src]
        icon = meta["icon"]
        name = meta["name"]
        mark = "✅" if src in enabled_sources else "❌"
        lines.append(f"{mark} {icon} {name}")

    lines.append(
        "\nНажимай на кнопки ниже, чтобы включать/выключать источники "
        "и общие уведомления для этого чата."
    )

    return "\n".join(lines)


def build_status_text(chat_id: int) -> str:
    """Текст статуса мониторинга для /status."""
    cfg = get_chat_config(chat_id)
    enabled_sources = set(cfg.get("sources", list(SOURCES.keys())))
    notif = bool(cfg.get("notifications", True))

    lines: List[str] = [
        "📡 <b>Статус мониторинга почты</b>\n",
        f"👤 Gmail: <code>{escape_html(GMAIL_USER)}</code>\n",
        f"🔔 Уведомления для этого чата: <b>{'ВКЛ' if notif else 'ВЫКЛ'}</b>\n",
        "<b>Активные источники:</b>",
    ]

    if enabled_sources:
        for src in SOURCE_ORDER:
            if src not in enabled_sources:
                continue
            meta = SOURCE_META[src]
            icon = meta["icon"]
            name = meta["name"]
            addr = SOURCES[src]
            lines.append(f"• {icon} {name} — <code>{escape_html(addr)}</code>")
    else:
        lines.append("• ❌ Нет включённых источников")

    with chat_ids_lock:
        subs = list(chat_ids)
    lines.append(f"\n🧩 Подписанных чатов всего: <b>{len(subs)}</b>")

    lines.append(
        "\n✅ Всё включено. Как только на этот Gmail придёт новое письмо с одного из источников — "
        "я скину уведомление прямо в этот чат."
    )

    return "\n".join(lines)


def make_settings_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура с настройками для /settings."""
    cfg = get_chat_config(chat_id)
    enabled_sources = set(cfg.get("sources", list(SOURCES.keys())))
    notif = bool(cfg.get("notifications", True))

    kb = InlineKeyboardMarkup(row_width=1)

    # Источники
    for src in SOURCE_ORDER:
        meta = SOURCE_META[src]
        icon = meta["icon"]
        name = meta["name"]
        mark = "✅" if src in enabled_sources else "❌"
        kb.add(
            InlineKeyboardButton(
                text=f"{mark} {icon} {name}",
                callback_data=f"cfg:src:{src}",
            )
        )

    # Общие уведомления
    notif_text = "🔔 Уведомления: ВКЛ" if notif else "🔕 Уведомления: ВЫКЛ"
    kb.add(
        InlineKeyboardButton(
            text=notif_text,
            callback_data="cfg:notify",
        )
    )

    return kb


# ======================= УТИЛИТЫ ДЛЯ ПОЧТЫ =======================

def decode_mime_header(raw_header: Optional[str]) -> str:
    """Декод MIME-заголовков (Subject, From и т.д.)."""
    if not raw_header:
        return ""
    decoded_parts = decode_header(raw_header)
    header = ""
    for part, enc in decoded_parts:
        if isinstance(part, bytes):
            try:
                header += part.decode(enc or "utf-8", errors="ignore")
            except LookupError:
                header += part.decode("utf-8", errors="ignore")
        else:
            header += part
    return header


def html_to_text(html: str) -> str:
    """Конвертация HTML в текст."""
    html = re.sub(r"(?is)<(script|style).*?>.*?(</\1>)", "", html)

    html = re.sub(r"(?is)<br\s*/?>", "\n", html)
    html = re.sub(r"(?is)</p>", "\n\n", html)
    html = re.sub(r"(?is)</div>", "\n", html)
    html = re.sub(r"(?is)</li>", "\n", html)
    html = re.sub(r"(?is)</h[1-6]>", "\n\n", html)
    html = re.sub(r"(?is)</tr>", "\n", html)

    html = re.sub(r"(?is)<t[dh][^>]*>", " ", html)

    text = re.sub(r"(?s)<.*?>", "", html)
    text = unescape(text)

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\r", "", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def get_imap_connection() -> imaplib.IMAP4_SSL:
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    return imap


def list_last_emails(from_filter: str, limit: int = 10) -> List[Dict[str, str]]:
    """Список последних писем от указанного отправителя."""
    imap = get_imap_connection()

    status, _ = imap.select("INBOX")
    if status != "OK":
        imap.logout()
        return []

    search_criteria = f'(FROM "{from_filter}")'
    status, data = imap.uid("search", None, search_criteria)
    if status != "OK" or not data or not data[0]:
        imap.close()
        imap.logout()
        return []

    uids = data[0].split()
    if not uids:
        imap.close()
        imap.logout()
        return []

    uids = uids[-limit:]  # последние N

    emails_list: List[Dict[str, str]] = []

    for uid in reversed(uids):  # новые сверху
        status, msg_data = imap.uid("fetch", uid, "(BODY.PEEK[HEADER])")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        subject = decode_mime_header(msg.get("Subject")) or "(без темы)"
        from_ = decode_mime_header(msg.get("From"))
        date = decode_mime_header(msg.get("Date"))

        emails_list.append(
            {
                "uid": uid.decode(),
                "subject": subject,
                "from": from_,
                "date": date,
            }
        )

    imap.close()
    imap.logout()
    return emails_list


def get_email_by_uid(uid: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Достаёт полное письмо по UID."""
    imap = get_imap_connection()

    status, _ = imap.select("INBOX")
    if status != "OK":
        imap.logout()
        return None, None, None, None

    status, msg_data = imap.uid("fetch", uid, "(RFC822)")
    if status != "OK" or not msg_data or not msg_data[0]:
        imap.close()
        imap.logout()
        return None, None, None, None

    raw_email = msg_data[0][1]
    msg = email.message_from_bytes(raw_email)

    subject = decode_mime_header(msg.get("Subject"))
    from_ = decode_mime_header(msg.get("From"))
    date = decode_mime_header(msg.get("Date"))

    body_text = ""

    if msg.is_multipart():
        text_part = None
        html_part = None

        for part in msg.walk():
            ctype = part.get_content_type()
            dispo = str(part.get("Content-Disposition", "")).lower()

            if "attachment" in dispo:
                continue

            if ctype == "text/plain" and text_part is None:
                text_part = part
            elif ctype == "text/html" and html_part is None:
                html_part = part

        part = text_part or html_part

        if part is not None:
            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True) or b""
            try:
                body = payload.decode(charset, errors="ignore")
            except Exception:
                body = payload.decode(errors="ignore")

            if part.get_content_type() == "text/html":
                body_text = html_to_text(body)
            else:
                body_text = body
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        try:
            body = payload.decode(charset, errors="ignore")
        except Exception:
            body = payload.decode(errors="ignore")

        if msg.get_content_type() == "text/html":
            body_text = html_to_text(body)
        else:
            body_text = body

    imap.close()
    imap.logout()

    body_text = (body_text or "").strip()
    if not body_text:
        body_text = "[Письмо без текста]"

    return subject, from_, date, body_text


def build_webapp_url(source: str, uid: str) -> str:
    sep = "&" if "?" in WEBAPP_BASE_URL else "?"
    return f"{WEBAPP_BASE_URL}{sep}source={source}&uid={uid}"


def send_email_pretty(
    chat_id: int,
    source: str,
    subject: str,
    from_: str,
    date: str,
    body_text: str,
    uid: Optional[str] = None,
    as_notification: bool = False,
) -> None:
    """Красивый вывод письма + кнопки WebApp."""
    meta = SOURCE_META.get(source, {"name": source.upper(), "icon": "✉️"})
    source_label = meta["name"]
    source_icon = meta["icon"]

    extra_lines: List[str] = []

    webapp_url = None
    if uid:
        webapp_url = build_webapp_url(source, uid)
        extra_lines.append(
            f"<b>WebApp URL:</b> <a href=\"{escape_html(webapp_url)}\">открыть письмо</a>"
        )

    if as_notification:
        extra_lines.insert(0, "🔔 <b>Новое письмо!</b>")

    extra_text = ("\n" + "\n".join(extra_lines)) if extra_lines else ""

    header = (
        f"{source_icon} <b>{escape_html(source_label)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>От:</b> {escape_html(from_)}\n"
        f"<b>Тема:</b> {escape_html(subject)}\n"
        f"<b>Дата:</b> {escape_html(date)}\n"
        f"{extra_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )

    reply_markup = None
    if webapp_url:
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton(
                "🧩 Открыть WebApp (в Telegram)",
                web_app=WebAppInfo(url=webapp_url),
            )
        )
        kb.row(
            InlineKeyboardButton(
                "🌐 Открыть в браузере",
                url=webapp_url,
            )
        )
        reply_markup = kb

    bot.send_message(chat_id, header, reply_markup=reply_markup)

    max_chunk = 3500
    text = body_text if body_text else "[Письмо без текста]"

    start = 0
    while start < len(text):
        chunk = text[start:start + max_chunk]
        start += max_chunk
        chunk_html = "<pre>" + escape_html(chunk) + "</pre>"
        bot.send_message(chat_id, chunk_html)


def get_source_info(source: str) -> Tuple[str, str]:
    if source not in SOURCES:
        raise ValueError(f"Unknown source: {source}")
    title = SOURCE_META.get(source, {"name": source})["name"]
    return title, SOURCES[source]


def make_source_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)

    for src in SOURCE_ORDER:
        email_addr = SOURCES[src]
        meta = SOURCE_META[src]
        kb.add(
            InlineKeyboardButton(
                f"{meta['icon']} {meta['name']} — {email_addr}",
                callback_data=f"src:{src}",
            )
        )

    return kb


def show_mail_list(chat_id: int, source: str) -> None:
    title, from_email = get_source_info(source)

    bot.send_chat_action(chat_id, "typing")

    mails = list_last_emails(from_email, limit=MAILS_LIMIT)
    if not mails:
        bot.send_message(chat_id, f"Писем от <b>{escape_html(from_email)}</b> не найдено.")
        return

    kb = InlineKeyboardMarkup(row_width=1)
    lines = [f"<b>Последние письма с {escape_html(title)}:</b>\n"]

    for idx, m in enumerate(mails, start=1):
        short_subject = m["subject"]
        if len(short_subject) > 70:
            short_subject = short_subject[:67] + "…"

        btn_text = f"{idx}. {short_subject}"
        kb.add(
            InlineKeyboardButton(
                text=f"✉️ {btn_text}",
                callback_data=f"mail:{source}:{m['uid']}",
            )
        )

        lines.append(f"{idx}. {escape_html(short_subject)}")

    text = "\n".join(lines)
    bot.send_message(chat_id, text, reply_markup=kb)


# ======================= ВОТЧЕР ПОЧТЫ =======================

def init_last_uids() -> None:
    """При старте просто запоминаем последнюю почту по каждому источнику."""
    global last_uids
    try:
        imap = get_imap_connection()
        status, _ = imap.select("INBOX")
        if status != "OK":
            imap.logout()
            return

        for source, sender in SOURCES.items():
            search_criteria = f'(FROM "{sender}")'
            status, data = imap.uid("search", None, search_criteria)
            if status == "OK" and data and data[0]:
                uids = data[0].split()
                if uids:
                    last_uids[source] = int(uids[-1])
                    print(f"[watcher] init {source} last_uid = {last_uids[source]}")

        imap.close()
        imap.logout()
    except Exception as e:
        print("init_last_uids error:", e)


def watcher_loop(poll_interval: int = 5) -> None:
    """Фоновый цикл: проверяет новые письма и раскидывает уведомления по чатам."""
    global last_uids

    init_last_uids()

    while True:
        try:
            with chat_ids_lock:
                targets = list(chat_ids)

            if not targets:
                # Никто не подписан — можно не дергать Gmail
                time.sleep(poll_interval)
                continue

            print(f"[watcher] check, targets={targets}")

            imap = get_imap_connection()
            status, _ = imap.select("INBOX")
            if status != "OK":
                imap.logout()
                time.sleep(poll_interval)
                continue

            for source, sender in SOURCES.items():
                search_criteria = f'(FROM "{sender}")'
                status, data = imap.uid("search", None, search_criteria)
                if status != "OK" or not data or not data[0]:
                    continue

                uids = data[0].split()
                if not uids:
                    continue

                uids_int = sorted(int(u) for u in uids)
                last_saved = last_uids.get(source)
                new_uids: List[int] = []

                if last_saved is None:
                    last_uids[source] = uids_int[-1]
                    print(f"[watcher] {source}: last_saved=None, set to {last_uids[source]}")
                else:
                    for u in uids_int:
                        if u > last_saved:
                            new_uids.append(u)
                    if uids_int:
                        last_uids[source] = uids_int[-1]

                if new_uids:
                    print(f"[watcher] {source} new_uids: {new_uids}")
                    for u in new_uids:
                        uid_str = str(u)
                        subject, from_, date, body = get_email_by_uid(uid_str)
                        if not subject:
                            continue
                        try:
                            for chat_id in targets:
                                cfg = get_chat_config(chat_id)
                                if not cfg.get("notifications", True):
                                    continue
                                enabled_sources = set(
                                    cfg.get("sources", list(SOURCES.keys()))
                                )
                                if source not in enabled_sources:
                                    continue

                                try:
                                    send_email_pretty(
                                        chat_id=chat_id,
                                        source=source,
                                        subject=subject or "",
                                        from_=from_ or sender,
                                        date=date or "",
                                        body_text=body or "",
                                        uid=uid_str,
                                        as_notification=True,
                                    )
                                except Exception as send_err:
                                    print(
                                        f"[watcher] Error sending notification to chat {chat_id}:",
                                        send_err,
                                    )
                        except Exception as e:
                            print("[watcher] Error sending notification:", e)

            imap.close()
            imap.logout()

        except Exception as e:
            print("watcher_loop error:", e)

        time.sleep(poll_interval)


# ======================= ХЕНДЛЕРЫ КОМАНД =======================

@bot.message_handler(commands=["start", "help"])
def handle_start(message):
    chat = message.chat
    chat_id = chat.id
    title = chat.title or chat.username or ""
    chat_type = chat.type  # private / group / supergroup / channel

    ensure_chat_config(chat_id, title=title, chat_type=chat_type)
    set_chat_notifications(chat_id, True)

    text = (
        "👋 <b>Привет!</b>\n\n"
        "Я бот <b>memes4u1337</b>. Слежу за почтой Gmail "
        "и отправляю письма/уведомления сюда — в этот чат (может быть и группа).\n\n"
        "<b>Источники:</b>\n"
        f"• 🟧 Kwork: <code>{escape_html(KWORK_FROM)}</code>\n"
        f"• 🟦 Work-Zilla: <code>{escape_html(WORKZILLA_FROM)}</code>\n"
        f"• 🟪 Freelance.ru: <code>{escape_html(FREELANCEJOB_FROM)}</code>\n\n"
        "✅ Для этого чата уведомления уже <b>включены</b>, мониторю новые письма.\n\n"
        "➤ <b>/mails</b> — выбрать площадку и посмотреть последние письма.\n"
        "➤ <b>/settings</b> — настройки источников и уведомлений для этого чата.\n"
        "➤ <b>/status</b> — текущий статус мониторинга.\n"
        "➤ <b>/chatid</b> — показать ID этого чата.\n"
        "➤ <b>/testnotify</b> — тестовое уведомление в этот чат.\n"
        "➤ <b>/stop</b> — быстро выключить уведомления в чате.\n"
    )

    bot.reply_to(message, text, reply_markup=make_source_keyboard())


@bot.message_handler(commands=["mails", "lastmail"])
def handle_mails(message):
    bot.reply_to(
        message,
        "Выбери площадку, откуда смотреть письма:",
        reply_markup=make_source_keyboard(),
    )


@bot.message_handler(commands=["settings"])
def handle_settings(message):
    chat = message.chat
    chat_id = message.chat.id
    ensure_chat_config(
        chat_id,
        title=chat.title or chat.username or "",
        chat_type=chat.type,
    )
    txt = build_settings_text(chat_id)
    kb = make_settings_keyboard(chat_id)
    bot.reply_to(message, txt, reply_markup=kb)


@bot.message_handler(commands=["status"])
def handle_status(message):
    chat_id = message.chat.id
    ensure_chat_config(
        chat_id,
        title=message.chat.title or message.chat.username or "",
        chat_type=message.chat.type,
    )
    txt = build_status_text(chat_id)
    bot.reply_to(message, txt)


@bot.message_handler(commands=["chatid"])
def handle_chatid(message):
    chat = message.chat
    chat_id = chat.id
    text = (
        f"🧾 <b>ID этого чата:</b>\n"
        f"<code>{chat_id}</code>\n\n"
        f"Тип: <b>{chat.type}</b>\n"
        f"Название / username: <code>{escape_html(chat.title or chat.username or '')}</code>"
    )
    bot.reply_to(message, text)


@bot.message_handler(commands=["testnotify"])
def handle_testnotify(message):
    """Тестовое уведомление в ТЕКУЩИЙ чат, тем же механизмом, что и вотчер."""
    chat_id = message.chat.id
    try:
        send_email_pretty(
            chat_id=chat_id,
            source="kwork",
            subject="Тестовое уведомление",
            from_="test@example.com",
            date=time.strftime("%Y-%m-%d %H:%M:%S"),
            body_text="Это тестовое уведомление, отправленное командой /testnotify.",
            uid=None,
            as_notification=True,
        )
    except Exception as e:
        print(f"[testnotify] error sending to chat {chat_id}:", e)
        bot.reply_to(message, f"Ошибка при отправке тестового уведомления: <code>{escape_html(str(e))}</code>")


@bot.message_handler(commands=["stop"])
def handle_stop(message):
    chat_id = message.chat.id
    set_chat_notifications(chat_id, False)
    bot.reply_to(
        message,
        "🔕 Уведомления для этого чата выключены.\n"
        "Включить обратно — /settings (кнопка «Уведомления») или снова /start.",
    )


# ======================= CALLBACK-КНОПКИ =======================

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("src:"))
def handle_source_choice(call):
    source = call.data.split(":", 1)[1]
    bot.answer_callback_query(call.id)
    show_mail_list(call.message.chat.id, source)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("mail:"))
def handle_mail_choice(call):
    _, source, uid = call.data.split(":", 2)
    title, _ = get_source_info(source)

    bot.answer_callback_query(call.id, f"Открываю письмо с {title}…")
    bot.send_chat_action(call.message.chat.id, "typing")

    subject, from_, date, body = get_email_by_uid(uid)
    if not subject:
        bot.send_message(call.message.chat.id, "Не удалось прочитать письмо.")
        return

    send_email_pretty(
        chat_id=call.message.chat.id,
        source=source,
        subject=subject or "",
        from_=from_ or "",
        date=date or "",
        body_text=body or "",
        uid=uid,
    )


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("cfg:"))
def handle_settings_callback(call):
    chat_id = call.message.chat.id
    data = call.data.split(":")

    if len(data) < 2:
        bot.answer_callback_query(call.id, "Неизвестное действие.")
        return

    action = data[1]

    if action == "src" and len(data) == 3:
        source = data[2]
        enabled = toggle_chat_source(chat_id, source)
        meta = SOURCE_META.get(source, {"name": source})
        name = meta["name"]
        bot.answer_callback_query(
            call.id,
            f"{'Включено' if enabled else 'Выключено'}: {name}",
            show_alert=False,
        )
    elif action == "notify":
        cfg = get_chat_config(chat_id)
        now = bool(cfg.get("notifications", True))
        new_state = not now
        set_chat_notifications(chat_id, new_state)
        bot.answer_callback_query(
            call.id,
            f"Уведомления: {'ВКЛ' if new_state else 'ВЫКЛ'}",
            show_alert=False,
        )
    else:
        bot.answer_callback_query(call.id, "Неизвестное действие.")
        return

    # Перерисовываем сообщение с актуальными настройками
    try:
        new_text = build_settings_text(chat_id)
        new_kb = make_settings_keyboard(chat_id)
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=new_text,
            reply_markup=new_kb,
            parse_mode="HTML",
        )
    except Exception as e:
        print("edit settings message error:", e)


# ======================= ЗАПУСК БОТА =======================

def set_bot_commands() -> None:
    """Регистрируем команды, чтобы при вводе '/' Telegram их показывал."""
    commands = [
        BotCommand("start", "Запуск бота и справка"),
        BotCommand("help", "Справка по боту"),
        BotCommand("mails", "Выбрать площадку и посмотреть письма"),
        BotCommand("settings", "Настройки уведомлений и источников"),
        BotCommand("status", "Статус мониторинга почты"),
        BotCommand("chatid", "Показать ID этого чата"),
        BotCommand("testnotify", "Тестовое уведомление в этот чат"),
        BotCommand("stop", "Выключить уведомления в этом чате"),
    ]
    try:
        bot.set_my_commands(commands)
        print("[bot] команды зарегистрированы")
    except Exception as e:
        print("set_my_commands error:", e)


if __name__ == "__main__":
    print("Бот запускается...")

    load_chat_settings()
    set_bot_commands()

    watcher_thread = threading.Thread(
        target=watcher_loop,
        kwargs={"poll_interval": 5},
        daemon=True,
    )
    watcher_thread.start()

    print("Бот запущен, начинаем polling...")
    bot.infinity_polling(skip_pending=True)
