# telegram_bot_parser


1. Core idea of the bot

The bot connects to Gmail via IMAP and watches for new emails from specific freelancing platforms:

Kwork (news@kwork.ru)

Work-Zilla (info@work-zilla.com)

Freelance.ru (noreply@robot.freelance.ru)

When new emails arrive, the bot:

Parses them (subject, from, date, body)

Sends a nicely formatted notification into all Telegram chats that are subscribed and have that source enabled.

The bot also allows users to:

View recent emails from a platform

Configure which sources they want to receive

Turn notifications on/off per chat

All chat settings are stored in a JSON file (chat_settings.json), so they persist between restarts.

2. Settings & storage logic
load_chat_settings()

Loads chat configurations from chat_settings.json.

For each chat it restores:

enabled sources

notifications on/off

chat title

chat type

Populates:

chat_settings – per-chat config

chat_ids – set of chats with notifications enabled

save_chat_settings()

Serializes chat_settings into a JSON structure and writes it to chat_settings.json (via a temporary file to be safe).

Keeps only valid sources.

ensure_chat_config(chat_id, title, chat_type)

Makes sure that a given chat has a config.

If not, creates one with:

all sources enabled

notifications enabled

saved title and type

If it’s a newly registered chat, adds it to chat_ids.

set_chat_notifications(chat_id, enabled)

Enables or disables notifications for a specific chat.

Updates:

chat_settings[chat_id]["notifications"]

membership of chat_id in chat_ids

Saves settings to disk.

toggle_chat_source(chat_id, source)

Switches a single source on/off for a chat.

Returns:

True if the source is enabled after toggle

False if disabled

get_chat_config(chat_id)

Returns the config for a chat, creating it with defaults if missing.

3. Status / settings text builders
build_settings_text(chat_id)

Builds a human-readable text for /settings, including:

Whether notifications are ON/OFF

List of sources with markers:

✅ enabled

❌ disabled

build_status_text(chat_id)

Builds a status message for /status, including:

Gmail account being monitored

Notification state for this chat

Active sources with their email addresses

Total number of subscribed chats

Short explanation of what the bot is doing

make_settings_keyboard(chat_id)

Creates an inline keyboard for /settings:

One button per source to toggle it

One button to toggle notifications ON/OFF

4. Email utilities
decode_mime_header(raw_header)

Safely decodes MIME-encoded headers like Subject and From, handling different charsets.

html_to_text(html)

Converts HTML body to plain text:

Removes <script> and <style>

Converts <br>, </p>, etc. to new lines

Strips all tags

Unescapes HTML entities (&amp;, &lt;, etc.)

escape_html(text)

Escapes special characters so text is safe inside HTML: &, <, >.

get_imap_connection()

Connects to Gmail’s IMAP server (imap.gmail.com) with login/password from .env.

Returns an authenticated IMAP connection.

5. Fetching emails
list_last_emails(from_filter, limit=10)

Searches the INBOX for emails from a specific address (FROM "...").

Gets up to limit latest emails.

Returns a list of dicts:

uid

subject

from

date

Used when the user wants to see recent emails for a given platform.

get_email_by_uid(uid)

Fetches a full email by its UID.

Extracts:

subject

from

date

body text (prefers text/plain, falls back to text/html converted to text)

Returns a tuple: (subject, from, date, body_text).

6. WebApp link & pretty output
build_webapp_url(source, uid)

Builds a URL to your external WebApp to display the email:

WEBAPP_BASE_URL?source=...&uid=... or with & if ? already exists.

send_email_pretty(...)

Sends a pretty formatted message to a chat with:

platform icon + name

From / Subject / Date

Optional WebApp link

Optional "New email" indicator for notifications

Inline keyboard:

Open WebApp inside Telegram

Open in browser

Then sends the email body in chunks (max ~3500 chars per message) with <pre> formatting.

Appends code: @memes4u1337 to each message.

7. Source helpers
get_source_info(source)

Returns tuple (title, email_address) for a source.

make_source_keyboard()

Inline keyboard with one button per source (Kwork, Work-Zilla, Freelance.ru).

Used in /mails and /start.

show_mail_list(chat_id, source)

Fetches recent emails for a given source.

If none found, sends "no emails found" message.

If there are emails:

builds a numbered list

creates inline buttons, one per email

pressing a button triggers loading that email by UID

8. Background watcher
init_last_uids()

On startup, for each source:

Searches all emails from that sender

Stores the latest UID in last_uids[source]

This ensures the bot doesn’t notify about old emails, only new ones.

watcher_loop(poll_interval=5)

Runs in a separate thread.

Infinite loop:

Copies list of subscribed chats (chat_ids).

If no chats – sleeps and skips work.

Connects to IMAP, selects INBOX.

For each source:

Searches emails from that sender.

Determines which UIDs are new (greater than last_uids[source]).

Updates last_uids[source].

For each new UID:

Fetches the email with get_email_by_uid.

For each subscribed chat:

Checks if notifications are on and if this source is enabled in that chat.

Sends notification via send_email_pretty(as_notification=True).

Sleeps poll_interval seconds between cycles.

9. Command handlers
/start and /help → handle_start

Registers/configures the chat (if new).

Enables notifications for the chat.

Sends a welcome/help message:

describes what the bot does

lists sources and their emails

lists available commands

Shows a keyboard to choose a platform.

/mails and /lastmail → handle_mails

Asks the user to choose a platform.

Shows inline keyboard with platforms (Kwork, Work-Zilla, Freelance.ru).

/settings → handle_settings

Ensures chat has config.

Sends settings text (notifications + per-source status).

Attaches inline keyboard to toggle sources / notifications.

/status → handle_status

Sends the status of:

monitored Gmail account

notifications for this chat

active sources

total number of subscribed chats

/chatid → handle_chatid

Sends chat ID, chat type, and title/username.

Handy for configuration and debugging.

/testnotify → handle_testnotify

Sends a fake “new email” notification to the current chat.

Uses the same formatting as real watcher notifications.

If something goes wrong, reports the error to the chat.

/stop → handle_stop

Turns off notifications for this chat.

Instructs how to turn them back on (/settings or /start).

10. Callback handlers (inline buttons)
src:... → handle_source_choice

Triggered when user presses a source button (Kwork/Work-Zilla/Freelance).

Calls show_mail_list() to display latest emails from that source.

mail:source:uid → handle_mail_choice

Triggered when user picks a specific email from the list.

Fetches the email by UID.

Sends the email nicely formatted via send_email_pretty.

cfg:... → handle_settings_callback

Handles inline buttons in /settings:

cfg:src:<source> – toggles that source on/off for the chat.

cfg:notify – toggles notifications for this chat.

After any change:

Rebuilds settings text and keyboard.

Edits the original message with updated state.

11. Startup logic
set_bot_commands()

Registers bot commands with Telegram so they appear in the / command menu.

if __name__ == "__main__":

Prints “bot starting…”

Loads chat settings from file.

Registers bot commands.

Starts watcher thread (watcher_loop with interval 5 seconds).

Starts polling Telegram updates with bot.infinity_polling(skip_pending=True).

❤️ Credits code: @memes4u1337
