# telegram_bot_parser
## 🧩 Core Functions

### 📥 Gmail / IMAP

#### `get_imap_connection()`
Creates and returns an authenticated IMAP connection to Gmail (`imap.gmail.com`) using:
- `GMAIL_USER`
- `GMAIL_APP_PASSWORD`

---

#### `list_last_emails(from_filter: str, limit: int = 10) -> List[dict]`
Returns a list of the latest emails from a specific sender.

- Searches: `FROM "<from_filter>"` in `INBOX`
- For each email collects:
  - `uid`
  - `subject`
  - `from`
  - `date`

Used to show recent emails for Kwork / Work-Zilla / Freelance.ru.

---

#### `get_email_by_uid(uid: str) -> (subject, from, date, body_text)`
Fetches full email by UID and extracts:

- Subject
- From
- Date
- Text body (prefers `text/plain`, falls back to `text/html` → converted to text)

Returns a tuple: `(subject, from_, date, body_text)`.

---

### 🧠 Chat Settings

#### `load_chat_settings()`
Loads per-chat settings from JSON file (`SETTINGS_FILE`):

- Enabled sources per chat
- Notifications on/off
- Chat title and type

Populates:

- `chat_settings`
- `chat_ids` (chats with notifications enabled)

---

#### `save_chat_settings()`
Saves current `chat_settings` into JSON (`SETTINGS_FILE`) so configs persist between restarts.

---

#### `ensure_chat_config(chat_id: int, title: str = "", chat_type: str = "")`
Ensures the chat has a config. If not:

- Creates default:
  - all sources enabled
  - notifications enabled
- Adds chat to `chat_ids`
- Saves settings

---

#### `set_chat_notifications(chat_id: int, enabled: bool)`
Turns notifications ON/OFF for a chat:

- Updates `chat_settings[chat_id]["notifications"]`
- Adds/removes chat from `chat_ids`
- Calls `save_chat_settings()`

---

#### `toggle_chat_source(chat_id: int, source: str) -> bool`
Enables/disables a specific source (e.g. `kwork`) for a chat.

- Updates `chat_settings[chat_id]["sources"]`
- Saves settings
- Returns:
  - `True` if source is enabled after toggle
  - `False` if disabled

---

### 🧾 UI / Text / Keyboards

#### `build_settings_text(chat_id: int) -> str`
Builds text for `/settings`:

- Notification status (ON/OFF)
- List of sources with:
  - ✅ enabled
  - ❌ disabled

---

#### `build_status_text(chat_id: int) -> str`
Builds text for `/status`:

- Monitored Gmail account
- Notification status for this chat
- Active sources + their emails
- Total subscribed chats

---

#### `make_settings_keyboard(chat_id: int) -> InlineKeyboardMarkup`
Inline keyboard for `/settings`:

- Buttons to toggle each source
- Button to toggle notifications for this chat

---

#### `make_source_keyboard() -> InlineKeyboardMarkup`
Inline keyboard with source selection:

- 🟧 Kwork
- 🟦 Work-Zilla
- 🟪 Freelance.ru

Used in `/start`, `/mails`.

---

### ✉️ Output / Formatting

#### `build_webapp_url(source: str, uid: str) -> str`
Builds link to external WebApp:

- `WEBAPP_BASE_URL?source=<source>&uid=<uid>` (or `&` if `?` already present)

---

#### `send_email_pretty(...)`
Sends a nicely formatted email into a Telegram chat:

- Shows:
  - Platform icon + name
  - From
  - Subject
  - Date
  - Optional WebApp URL
- Attaches inline buttons:
  - Open WebApp in Telegram
  - Open in browser
- Sends body in `<pre>` chunks (up to ~3500 chars)
- Appends `code: @memes4u1337` to each message

---

### 📡 Watcher (Background Loop)

#### `init_last_uids()`
On startup, for each source:

- Finds last email UID from that sender
- Saves to `last_uids[source]`

So the bot only notifies about **new** emails.

---

#### `watcher_loop(poll_interval: int = 5)`
Runs in a background thread:

1. Initializes `last_uids()`.
2. In a loop:
   - Takes list of subscribed chats (`chat_ids`).
   - If no chats → sleep.
   - For each source:
     - Looks for new UIDs greater than `last_uids[source]`.
     - Updates `last_uids[source]`.
     - For each new email:
       - Fetches email via `get_email_by_uid()`.
       - For each chat:
         - Checks notifications and enabled sources.
         - Sends notification via `send_email_pretty(..., as_notification=True)`.
   - Sleeps `poll_interval` seconds.

---

### 💬 Main Commands (Handlers)

(Для README обычно достаточно просто упомянуть, без детализации кода.)

- `/start`, `/help` – show help, register chat, enable notifications.
- `/mails` – choose source and show recent emails.
- `/settings` – open per-chat settings (sources + notifications).
- `/status` – show current monitoring status.
- `/chatid` – show chat ID and meta.
- `/testnotify` – send test notification to current chat.
- `/stop` – disable notifications in current chat.


❤️ Credits code: @memes4u1337
