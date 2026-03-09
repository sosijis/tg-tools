"""
TG Tools — Telegram Automation Suite
Красивое macOS приложение на CustomTkinter
"""

import asyncio
import glob
import json
import os
import re
import sys
import time
import threading
import queue
from datetime import datetime
from pathlib import Path
from io import StringIO

import customtkinter as ctk
from PIL import Image, ImageTk

# ── Попытка импорта Telethon ───────────────────────────────────────────────────
try:
    import qrcode
    from telethon import TelegramClient, events, errors
    from telethon.tl.types import Channel, Chat, User, KeyboardButtonCallback
    from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
    from telethon.tl.functions.contacts import BlockRequest
    from telethon.tl.functions.messages import (
        StartBotRequest, ImportChatInviteRequest,
        DeleteChatUserRequest, DeleteHistoryRequest,
    )
    from telethon.tl.functions.photos import UploadProfilePhotoRequest
    from telethon.errors import (
        UserAlreadyParticipantError, InviteHashExpiredError,
        FloodWaitError, SessionPasswordNeededError,
    )
    TELETHON_OK = True
except ImportError as e:
    TELETHON_OK = False
    TELETHON_ERR = str(e)

# ── Тема ──────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Цвета
C_BG       = "#0d0f14"
C_PANEL    = "#13161e"
C_CARD     = "#1a1d27"
C_BORDER   = "#252836"
C_ACCENT   = "#4f8ef7"
C_ACCENT2  = "#7c5af7"
C_GREEN    = "#3dd68c"
C_RED      = "#f7564f"
C_YELLOW   = "#f7c94f"
C_TEXT     = "#e8eaf0"
C_MUTED    = "#6b7080"
C_SIDEBAR  = "#10121a"

# ── Конфиг (изменяется через GUI) ─────────────────────────────────────────────
class Config:
    API_ID   = ""
    API_HASH = ""
    SESSION  = "session"

    # Referral
    BOT_REPLY_TIMEOUT      = 15
    BOT_EXTRA_TIMEOUT      = 10
    DELAY_BETWEEN_SPONSORS = 8
    MAX_SUBSCRIBE_ROUNDS   = 3

    # Cleaner
    SLEEP = 1.0
    EXCLUDE_IDS: set = set()

    # Toolbox
    KEYWORDS = ["оплата", "цена", "заказ", "документы", "срочно"]
    DOWNLOAD_DIR = "downloads"
    EXPORT_DIR   = "exports"
    LOG_DIR      = "logs"

cfg = Config()

CHECK_BUTTON_KEYWORDS = [
    "проверить", "check", "verify", "подписался", "я подписан",
    "готово", "done", "subscribed",
]


# ══════════════════════════════════════════════════════════════════════════════
#  ЛОГИКА (без GUI-зависимостей)
# ══════════════════════════════════════════════════════════════════════════════

def parse_tme_link(link: str) -> dict:
    link = link.strip()
    m = re.search(r"t\.me/(?:joinchat/|\+)([A-Za-z0-9_-]+)", link)
    if m:
        return {"type": "invite", "hash": m.group(1)}
    m = re.search(r"t\.me/([A-Za-z0-9_]+)(?:\?start=([A-Za-z0-9_\-]+))?", link)
    if m:
        return {"type": "public", "username": m.group(1), "start": m.group(2)}
    return {}


def is_check_button(label: str) -> bool:
    return "✅" in label or any(kw in label.lower() for kw in CHECK_BUTTON_KEYWORDS)


def extract_sponsor_links(msg) -> list:
    links = []
    markup = msg.reply_markup
    if markup:
        for row in (getattr(markup, "rows", None) or []):
            for btn in row.buttons:
                label = getattr(btn, "text", "") or ""
                if is_check_button(label):
                    continue
                url = getattr(btn, "url", None)
                if url and "t.me" in url:
                    links.append(url.strip())
    if msg.text:
        links += re.findall(r"https?://t\.me/[^\s\)\]\"'<>]+", msg.text)
    return links


def find_check_button(msg):
    markup = msg.reply_markup
    if not markup:
        return None
    for row in (getattr(markup, "rows", None) or []):
        for btn in row.buttons:
            label = getattr(btn, "text", "") or ""
            if is_check_button(label):
                return (msg, btn)
    return None


def fmt_entity(entity) -> str:
    if hasattr(entity, "username") and entity.username:
        return f"@{entity.username}"
    if hasattr(entity, "title") and entity.title:
        return entity.title
    return str(getattr(entity, "id", "?"))


def display_name(entity) -> str:
    return getattr(entity, "title",
           getattr(entity, "username",
           str(getattr(entity, "id", "unknown"))))


# ══════════════════════════════════════════════════════════════════════════════
#  RUNNER — запускает asyncio в отдельном потоке, шлёт логи в очередь
# ══════════════════════════════════════════════════════════════════════════════

class AsyncRunner:
    """Запускает корутины в фоне, пишет логи в queue."""

    def __init__(self, log_queue: queue.Queue):
        self.q      = log_queue
        self.loop   = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.client: TelegramClient | None = None
        self._stop_flag = False

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def log(self, msg: str, tag: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.q.put((ts, tag, msg))

    def stop(self):
        self._stop_flag = True

    # ── Авторизация ────────────────────────────────────────────────────────────
    async def ensure_client(self):
        if self.client and self.client.is_connected():
            return True
        self.client = TelegramClient(cfg.SESSION, int(cfg.API_ID), cfg.API_HASH)
        await self.client.connect()
        return True

    async def _login_qr(self, qr_callback, done_callback, password_callback):
        await self.ensure_client()
        if await self.client.is_user_authorized():
            me = await self.client.get_me()
            self.log(f"Уже авторизован: {me.first_name} (@{me.username or me.id})", "ok")
            done_callback(me)
            return

        while True:
            qr = await self.client.qr_login()
            # Генерируем QR изображение
            img = qrcode.make(qr.url)
            qr_callback(img)
            self.log("QR обновлён — отсканируй в Telegram → Устройства", "info")
            try:
                await qr.wait(timeout=120)
                break
            except TimeoutError:
                continue
            except SessionPasswordNeededError:
                pwd = await asyncio.get_event_loop().run_in_executor(None, password_callback)
                await self.client.sign_in(password=pwd)
                break

        me = await self.client.get_me()
        self.log(f"Вход выполнен: {me.first_name} (@{me.username or me.id})", "ok")
        done_callback(me)

    def login_qr(self, qr_callback, done_callback, password_callback):
        self.submit(self._login_qr(qr_callback, done_callback, password_callback))

    # ── REFERRAL ───────────────────────────────────────────────────────────────
    async def _safe_join(self, link_info):
        for _ in range(2):
            try:
                if link_info["type"] == "invite":
                    result = await self.client(ImportChatInviteRequest(link_info["hash"]))
                    self.log("✅ Вступил по инвайту", "ok")
                    return result.chats[0] if hasattr(result, "chats") else None
                else:
                    entity = await self.client.get_entity(link_info["username"])
                    await self.client(JoinChannelRequest(entity))
                    self.log(f"✅ Подписался на {fmt_entity(entity)}", "ok")
                    return entity
            except UserAlreadyParticipantError:
                self.log("ℹ️  Уже подписан", "info")
                try:
                    if link_info["type"] == "public":
                        return await self.client.get_entity(link_info["username"])
                except Exception:
                    pass
                return None
            except InviteHashExpiredError:
                self.log("⚠️  Инвайт устарел", "warn")
                return None
            except FloodWaitError as e:
                self.log(f"⏳ FloodWait {e.seconds}s — жду…", "warn")
                await asyncio.sleep(e.seconds + 3)
            except Exception as e:
                err = str(e).lower()
                if "successfully requested" in err or "requested to join" in err:
                    self.log("✅ Заявка подана (закрытый канал)", "ok")
                    try:
                        if link_info["type"] == "public":
                            return await self.client.get_entity(link_info["username"])
                    except Exception:
                        pass
                    return None
                self.log(f"⚠️  Ошибка подписки: {e}", "warn")
                return None
        return None

    async def _collect_bot_msgs(self, bot_entity, action_coro, timeout):
        collected = []
        async def _col(event):
            collected.append(event.message)
        self.client.add_event_handler(_col, events.NewMessage(from_users=bot_entity.id))
        try:
            await action_coro
            await asyncio.sleep(timeout)
        finally:
            self.client.remove_event_handler(_col)
        return collected

    async def _process_sponsors(self, sponsor_links, all_joined, processed):
        new = [l for l in sponsor_links if l not in processed]
        if not new:
            self.log("Новых спонсоров нет", "info")
            return
        self.log(f"Спонсоров: {len(new)}", "info")
        for url in new:
            self.log(f"  → {url}", "info")
            info = parse_tme_link(url)
            if not info:
                continue
            await asyncio.sleep(cfg.DELAY_BETWEEN_SPONSORS)
            if info["type"] == "public":
                try:
                    sp = await self.client.get_entity(info["username"])
                except Exception as e:
                    self.log(f"  ⚠️  {e}", "warn")
                    continue
                if isinstance(sp, User) and sp.bot:
                    self.log(f"  🤖 Бот-спонсор {fmt_entity(sp)}", "info")
                    try:
                        sp_start = info.get("start") or ""
                        if sp_start:
                            await self.client(StartBotRequest(bot=sp, peer=sp, start_param=sp_start))
                        else:
                            await self.client.send_message(sp, "/start")
                        all_joined.append((url, ("bot", sp)))
                    except Exception as e:
                        self.log(f"  ⚠️  {e}", "warn")
                    continue
            joined = await self._safe_join(info)
            if joined:
                all_joined.append((url, ("channel", joined)))
            processed.add(url)

    async def _handle_bot(self, link_info):
        username    = link_info["username"]
        start_param = link_info.get("start") or ""
        self.log(f"🤖 Бот @{username}  start={start_param or '—'}", "info")
        bot_entity = await self.client.get_entity(username)
        all_joined = []
        processed  = set()

        async def _start():
            if start_param:
                await self.client(StartBotRequest(bot=bot_entity, peer=bot_entity, start_param=start_param))
            else:
                await self.client.send_message(bot_entity, "/start")

        messages = await self._collect_bot_msgs(bot_entity, _start(), cfg.BOT_REPLY_TIMEOUT)
        if not messages:
            self.log("⚠️  Бот не ответил", "warn")
            return

        self.log(f"📨 Получено {len(messages)} сообщений", "info")

        for round_num in range(1, cfg.MAX_SUBSCRIBE_ROUNDS + 1):
            self.log(f"🔄 Раунд {round_num}/{cfg.MAX_SUBSCRIBE_ROUNDS}", "info")
            sponsor_links = []
            check_result  = None
            for msg in messages:
                sponsor_links += extract_sponsor_links(msg)
                if check_result is None:
                    check_result = find_check_button(msg)
            sponsor_links = list(dict.fromkeys(sponsor_links))

            await self._process_sponsors(sponsor_links, all_joined, processed)

            if not check_result:
                break

            check_msg, btn = check_result
            self.log(f"🖱️  Нажимаю «{btn.text}»", "info")
            await asyncio.sleep(3)

            try:
                async def _click():
                    if isinstance(btn, KeyboardButtonCallback):
                        await check_msg.click(data=btn.data)
                    else:
                        await self.client.send_message(bot_entity, btn.text)

                messages = await self._collect_bot_msgs(bot_entity, _click(), cfg.BOT_EXTRA_TIMEOUT)
            except Exception as e:
                self.log(f"⚠️  Ошибка нажатия: {e}", "warn")
                break

            if not messages:
                self.log("✅ Успех (бот не ответил после проверки)", "ok")
                break

            new_sp = []
            for msg in messages:
                new_sp += extract_sponsor_links(msg)
            new_sp = [s for s in dict.fromkeys(new_sp) if s not in processed]
            if not new_sp:
                self.log("✅ Новых спонсоров нет — завершаем", "ok")
                break
            self.log(f"➕ Ещё {len(new_sp)} спонсоров", "info")

        # Очистка
        if all_joined:
            self.log(f"🧹 Убираем {len(all_joined)} спонсоров…", "info")
            for _, (kind, entity) in all_joined:
                await asyncio.sleep(cfg.DELAY_BETWEEN_SPONSORS)
                if kind == "channel":
                    try:
                        await self.client(LeaveChannelRequest(entity))
                        self.log(f"🚪 Вышел из {fmt_entity(entity)}", "info")
                    except Exception as e:
                        self.log(f"⚠️  {e}", "warn")
                elif kind == "bot":
                    try:
                        await self.client(BlockRequest(entity))
                        self.log(f"🚫 Заблокирован {fmt_entity(entity)}", "info")
                    except Exception as e:
                        self.log(f"⚠️  {e}", "warn")

        try:
            await self.client(BlockRequest(bot_entity))
            self.log(f"🚫 Основной бот @{username} заблокирован", "ok")
        except Exception as e:
            self.log(f"⚠️  {e}", "warn")

    async def _run_referral(self, links: list, done_cb):
        total   = len(links)
        success = 0
        failed  = 0
        t0      = time.time()

        for i, link in enumerate(links, 1):
            if self._stop_flag:
                self.log("⛔ Остановлено", "warn")
                break

            link = link.strip()
            if not link or link.startswith("#"):
                continue

            self.log(f"\n[{i}/{total}] {link}", "header")
            info = parse_tme_link(link)

            if not info:
                self.log("❌ Нераспознанная ссылка", "error")
                failed += 1
                continue

            try:
                if info["type"] == "invite":
                    r = await self._safe_join(info)
                    if r is not None:
                        success += 1
                    else:
                        failed += 1
                else:
                    entity = await self.client.get_entity(info["username"])
                    if isinstance(entity, User) and entity.bot:
                        await self._handle_bot(info)
                        success += 1
                    else:
                        r = await self._safe_join(info)
                        success += 1
            except Exception as e:
                self.log(f"❌ {e}", "error")
                failed += 1

            await asyncio.sleep(5)

        elapsed = int(time.time() - t0)
        m, s = divmod(elapsed, 60)
        self.log(f"\n{'═'*40}", "info")
        self.log(f"✅ Готово! {success} успешно, {failed} ошибок, время: {m}м {s}с", "ok")
        done_cb()

    def run_referral(self, links, done_cb):
        self._stop_flag = False
        self.submit(self._run_referral(links, done_cb))

    # ── CLEANER ────────────────────────────────────────────────────────────────
    async def _run_cleaner(self, done_cb):
        self.log("🔍 Получаю диалоги…", "info")
        dialogs = await self.client.get_dialogs()

        leave_targets = []
        bot_users     = []

        for d in dialogs:
            e   = d.entity
            eid = getattr(e, "id", None)
            if eid in cfg.EXCLUDE_IDS:
                self.log(f"⏭️  Пропущено: {display_name(e)}", "info")
                continue
            if isinstance(e, User) and getattr(e, "bot", False):
                bot_users.append(e)
            elif isinstance(e, (Channel, Chat)):
                leave_targets.append(e)

        self.log(f"Каналов/групп: {len(leave_targets)}, ботов: {len(bot_users)}", "info")

        for ent in leave_targets:
            if self._stop_flag:
                break
            title = display_name(ent)
            try:
                if isinstance(ent, Channel):
                    await self.client(LeaveChannelRequest(ent))
                else:
                    await self.client(DeleteChatUserRequest(chat_id=ent.id, user_id="me"))
                self.log(f"🚪 Вышел: {title}", "ok")
                await asyncio.sleep(cfg.SLEEP)
            except FloodWaitError as fw:
                self.log(f"⏳ FloodWait {fw.seconds}s", "warn")
                await asyncio.sleep(fw.seconds + 1)
            except Exception as e:
                self.log(f"⚠️  [{title}]: {e}", "warn")

        for bot in bot_users:
            if self._stop_flag:
                break
            if bot.id in cfg.EXCLUDE_IDS:
                continue
            tag = f"@{bot.username}" if bot.username else str(bot.id)
            try:
                await self.client(BlockRequest(id=bot))
                await self.client(DeleteHistoryRequest(peer=bot, max_id=0, just_clear=False, revoke=False))
                self.log(f"🚫 Бот удалён: {tag}", "ok")
                await asyncio.sleep(cfg.SLEEP)
            except FloodWaitError as fw:
                self.log(f"⏳ FloodWait {fw.seconds}s", "warn")
                await asyncio.sleep(fw.seconds + 1)
            except Exception as e:
                self.log(f"⚠️  [{tag}]: {e}", "warn")

        self.log("✅ Очистка завершена!", "ok")
        done_cb()

    def run_cleaner(self, done_cb):
        self._stop_flag = False
        self.submit(self._run_cleaner(done_cb))

    # ── TOOLBOX ────────────────────────────────────────────────────────────────
    async def _run_download_media(self, chat, done_cb):
        target_dir = os.path.join(cfg.DOWNLOAD_DIR, f"media_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        os.makedirs(target_dir, exist_ok=True)
        count = 0
        async for msg in self.client.iter_messages(chat, reverse=True):
            if self._stop_flag:
                break
            if not msg.media:
                continue
            try:
                path = await self.client.download_media(msg, file=target_dir)
                if path:
                    count += 1
                    self.log(f"[{count}] {os.path.basename(path)}", "ok")
            except FloodWaitError as fw:
                await asyncio.sleep(fw.seconds + 2)
            except Exception as e:
                self.log(f"⚠️  {e}", "warn")
        self.log(f"✅ Скачано: {count} файлов → {target_dir}", "ok")
        done_cb()

    def run_download_media(self, chat, done_cb):
        self._stop_flag = False
        self.submit(self._run_download_media(chat, done_cb))

    async def _run_export_json(self, chat, done_cb):
        os.makedirs(cfg.EXPORT_DIR, exist_ok=True)
        out = os.path.join(cfg.EXPORT_DIR, f"chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        data  = []
        total = 0
        async for msg in self.client.iter_messages(chat, reverse=True):
            if self._stop_flag:
                break
            sender = None
            try:
                sender = await msg.get_sender()
            except Exception:
                pass
            data.append({
                "id": msg.id,
                "date": msg.date.isoformat() if msg.date else None,
                "sender_id": getattr(sender, "id", None),
                "sender_username": getattr(sender, "username", None),
                "text": msg.raw_text,
                "has_media": bool(msg.media),
            })
            total += 1
            if total % 500 == 0:
                self.log(f"Собрано: {total}", "info")

        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.log(f"✅ Экспорт: {total} сообщений → {out}", "ok")
        done_cb()

    def run_export_json(self, chat, done_cb):
        self._stop_flag = False
        self.submit(self._run_export_json(chat, done_cb))

    async def _run_dialog_stats(self, done_cb):
        os.makedirs(cfg.EXPORT_DIR, exist_ok=True)
        dialogs = await self.client.get_dialogs()
        rows = []
        for d in dialogs:
            e = d.entity
            rows.append({
                "id": getattr(e, "id", None),
                "name": display_name(e),
                "unread": getattr(d, "unread_count", 0),
                "type": e.__class__.__name__,
            })
        out = os.path.join(cfg.EXPORT_DIR, f"dialogs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        self.log(f"✅ {len(rows)} диалогов → {out}", "ok")
        done_cb()

    def run_dialog_stats(self, done_cb):
        self.submit(self._run_dialog_stats(done_cb))


# ══════════════════════════════════════════════════════════════════════════════
#  GUI КОМПОНЕНТЫ
# ══════════════════════════════════════════════════════════════════════════════

class LogBox(ctk.CTkFrame):
    """Цветной лог-вывод."""
    TAG_COLORS = {
        "ok":     C_GREEN,
        "warn":   C_YELLOW,
        "error":  C_RED,
        "header": C_ACCENT,
        "info":   C_TEXT,
    }

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=C_CARD, corner_radius=12, **kwargs)
        self._text = ctk.CTkTextbox(
            self,
            fg_color=C_CARD,
            text_color=C_TEXT,
            font=("JetBrains Mono", 12),
            wrap="word",
            state="disabled",
        )
        self._text.pack(fill="both", expand=True, padx=2, pady=2)

        # Настраиваем теги через внутренний tk widget
        tw = self._text._textbox
        for tag, color in self.TAG_COLORS.items():
            tw.tag_config(tag, foreground=color)
        tw.tag_config("header", foreground=C_ACCENT, font=("JetBrains Mono", 12, "bold"))

    def append(self, ts: str, tag: str, msg: str):
        tw = self._text._textbox
        self._text.configure(state="normal")
        tw.insert("end", f"{ts} ", "muted")
        tw.insert("end", msg + "\n", tag)
        self._text.configure(state="disabled")
        self._text._textbox.see("end")

    def clear(self):
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")


class SidebarBtn(ctk.CTkButton):
    def __init__(self, master, text, icon="", **kwargs):
        super().__init__(
            master,
            text=f"  {icon}  {text}",
            anchor="w",
            height=44,
            corner_radius=10,
            fg_color="transparent",
            hover_color=C_CARD,
            text_color=C_MUTED,
            font=("SF Pro Display", 14),
            **kwargs,
        )

    def set_active(self, active: bool):
        if active:
            self.configure(fg_color=C_CARD, text_color=C_TEXT)
        else:
            self.configure(fg_color="transparent", text_color=C_MUTED)


class StatusDot(ctk.CTkLabel):
    def __init__(self, master, **kwargs):
        super().__init__(master, text="●", font=("SF Pro", 14), **kwargs)
        self.set("idle")

    def set(self, state: str):
        colors = {"idle": C_MUTED, "running": C_YELLOW, "ok": C_GREEN, "error": C_RED}
        self.configure(text_color=colors.get(state, C_MUTED))


# ══════════════════════════════════════════════════════════════════════════════
#  СТРАНИЦЫ
# ══════════════════════════════════════════════════════════════════════════════

class BasePage(ctk.CTkFrame):
    def __init__(self, master, runner: AsyncRunner, log_q: queue.Queue, **kwargs):
        super().__init__(master, fg_color=C_BG, **kwargs)
        self.runner = runner
        self.log_q  = log_q

    def section_label(self, parent, text):
        return ctk.CTkLabel(
            parent, text=text,
            font=("SF Pro Display", 11, "bold"),
            text_color=C_MUTED,
        )

    def card(self, parent, **kwargs):
        return ctk.CTkFrame(parent, fg_color=C_CARD, corner_radius=12, **kwargs)

    def accent_btn(self, parent, text, cmd, icon=""):
        return ctk.CTkButton(
            parent,
            text=f"{icon}  {text}" if icon else text,
            command=cmd,
            height=40,
            corner_radius=10,
            fg_color=C_ACCENT,
            hover_color="#3a7ae0",
            font=("SF Pro Display", 13, "bold"),
        )

    def danger_btn(self, parent, text, cmd, icon=""):
        return ctk.CTkButton(
            parent,
            text=f"{icon}  {text}" if icon else text,
            command=cmd,
            height=40,
            corner_radius=10,
            fg_color=C_RED,
            hover_color="#d94040",
            font=("SF Pro Display", 13, "bold"),
        )

    def stop_btn(self, parent, cmd):
        return ctk.CTkButton(
            parent,
            text="⛔  Стоп",
            command=cmd,
            height=40,
            corner_radius=10,
            fg_color=C_BORDER,
            hover_color="#333",
            text_color=C_YELLOW,
            font=("SF Pro Display", 13, "bold"),
        )


# ── Авторизация ────────────────────────────────────────────────────────────────
class AuthPage(BasePage):
    def __init__(self, master, runner, log_q, **kwargs):
        super().__init__(master, runner, log_q, **kwargs)
        self._build()

    def _build(self):
        # Заголовок
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=28, pady=(28, 0))
        ctk.CTkLabel(hdr, text="Авторизация", font=("SF Pro Display", 22, "bold"), text_color=C_TEXT).pack(side="left")
        self._dot = StatusDot(hdr)
        self._dot.pack(side="left", padx=10)

        # API данные
        api_card = self.card(self)
        api_card.pack(fill="x", padx=28, pady=16)

        ctk.CTkLabel(api_card, text="API CREDENTIALS", font=("SF Pro Display", 10, "bold"),
                     text_color=C_MUTED).pack(anchor="w", padx=16, pady=(12, 4))

        row1 = ctk.CTkFrame(api_card, fg_color="transparent")
        row1.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(row1, text="API ID", width=80, text_color=C_MUTED, font=("SF Pro", 12)).pack(side="left")
        self._api_id = ctk.CTkEntry(row1, placeholder_text="12345678",
                                     fg_color=C_BORDER, border_color=C_BORDER,
                                     text_color=C_TEXT, font=("JetBrains Mono", 12))
        self._api_id.pack(side="left", fill="x", expand=True)

        row2 = ctk.CTkFrame(api_card, fg_color="transparent")
        row2.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(row2, text="API Hash", width=80, text_color=C_MUTED, font=("SF Pro", 12)).pack(side="left")
        self._api_hash = ctk.CTkEntry(row2, placeholder_text="abc123…",
                                       fg_color=C_BORDER, border_color=C_BORDER,
                                       text_color=C_TEXT, font=("JetBrains Mono", 12), show="●")
        self._api_hash.pack(side="left", fill="x", expand=True)

        row3 = ctk.CTkFrame(api_card, fg_color="transparent")
        row3.pack(fill="x", padx=16, pady=(4, 12))
        ctk.CTkLabel(row3, text="Сессия", width=80, text_color=C_MUTED, font=("SF Pro", 12)).pack(side="left")
        self._session = ctk.CTkEntry(row3, placeholder_text="session",
                                      fg_color=C_BORDER, border_color=C_BORDER,
                                      text_color=C_TEXT, font=("JetBrains Mono", 12))
        self._session.insert(0, "session")
        self._session.pack(side="left", fill="x", expand=True)

        # QR блок
        qr_card = self.card(self)
        qr_card.pack(fill="x", padx=28, pady=0)

        ctk.CTkLabel(qr_card, text="QR-ВХОД", font=("SF Pro Display", 10, "bold"),
                     text_color=C_MUTED).pack(anchor="w", padx=16, pady=(12, 4))

        inner = ctk.CTkFrame(qr_card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=(0, 12))

        self._qr_label = ctk.CTkLabel(inner, text="QR появится здесь",
                                       width=180, height=180,
                                       fg_color=C_BORDER, corner_radius=8,
                                       text_color=C_MUTED, font=("SF Pro", 11))
        self._qr_label.pack(side="left", padx=(0, 16))

        right = ctk.CTkFrame(inner, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(right, text="1. Введи API данные выше\n2. Нажми «Войти по QR»\n3. Открой Telegram → Настройки\n   → Устройства → Подключить\n4. Отсканируй QR",
                     text_color=C_MUTED, font=("SF Pro", 12), justify="left").pack(anchor="w")

        btn_row = ctk.CTkFrame(right, fg_color="transparent")
        btn_row.pack(anchor="w", pady=(12, 0))
        self.accent_btn(btn_row, "Войти по QR", self._start_qr, "🔐").pack(side="left", padx=(0, 8))
        self._status_label = ctk.CTkLabel(btn_row, text="", text_color=C_MUTED, font=("SF Pro", 11))
        self._status_label.pack(side="left")

        # Лог
        self._log = LogBox(self)
        self._log.pack(fill="both", expand=True, padx=28, pady=16)

        # Запускаем поллинг очереди
        self._poll_log()

    def _poll_log(self):
        try:
            while True:
                ts, tag, msg = self.log_q.get_nowait()
                self._log.append(ts, tag, msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    def _start_qr(self):
        cfg.API_ID   = self._api_id.get().strip()
        cfg.API_HASH = self._api_hash.get().strip()
        cfg.SESSION  = self._session.get().strip() or "session"

        if not cfg.API_ID or not cfg.API_HASH:
            self._status_label.configure(text="❌ Введи API данные!", text_color=C_RED)
            return

        self._status_label.configure(text="Подключаюсь…", text_color=C_YELLOW)
        self._dot.set("running")

        def on_qr(img):
            # Конвертируем PIL image → CTkImage
            img_rgb = img.convert("RGB").resize((180, 180))
            ctk_img = ctk.CTkImage(img_rgb, size=(180, 180))
            self.after(0, lambda: self._qr_label.configure(image=ctk_img, text=""))

        def on_done(me):
            self.after(0, lambda: self._dot.set("ok"))
            self.after(0, lambda: self._status_label.configure(
                text=f"✅ {me.first_name}", text_color=C_GREEN))

        def on_password():
            # Простой диалог для 2FA
            dlg = ctk.CTkInputDialog(text="Введи пароль 2FA:", title="2FA")
            return dlg.get_input()

        self.runner.login_qr(on_qr, on_done, on_password)


# ── Referral ───────────────────────────────────────────────────────────────────
class ReferralPage(BasePage):
    def __init__(self, master, runner, log_q, **kwargs):
        super().__init__(master, runner, log_q, **kwargs)
        self._running = False
        self._build()
        self._poll_log()

    def _build(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=28, pady=(28, 0))
        ctk.CTkLabel(hdr, text="Реферальные ссылки", font=("SF Pro Display", 22, "bold"), text_color=C_TEXT).pack(side="left")
        self._dot = StatusDot(hdr)
        self._dot.pack(side="left", padx=10)

        # Настройки
        s_card = self.card(self)
        s_card.pack(fill="x", padx=28, pady=16)
        ctk.CTkLabel(s_card, text="НАСТРОЙКИ", font=("SF Pro Display", 10, "bold"),
                     text_color=C_MUTED).pack(anchor="w", padx=16, pady=(12, 4))

        row = ctk.CTkFrame(s_card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 12))

        for label, attr, default in [
            ("Таймаут бота", "BOT_REPLY_TIMEOUT", "15"),
            ("Пауза (сек)", "DELAY_BETWEEN_SPONSORS", "8"),
            ("Макс. раундов", "MAX_SUBSCRIBE_ROUNDS", "3"),
        ]:
            f = ctk.CTkFrame(row, fg_color="transparent")
            f.pack(side="left", expand=True, padx=4)
            ctk.CTkLabel(f, text=label, text_color=C_MUTED, font=("SF Pro", 11)).pack(anchor="w")
            e = ctk.CTkEntry(f, width=80, fg_color=C_BORDER, border_color=C_BORDER,
                              text_color=C_TEXT, font=("JetBrains Mono", 12))
            e.insert(0, default)
            e.pack()
            setattr(self, f"_e_{attr}", e)

        # Ввод ссылок
        input_card = self.card(self)
        input_card.pack(fill="x", padx=28, pady=0)
        ctk.CTkLabel(input_card, text="ССЫЛКИ (по одной на строку)",
                     font=("SF Pro Display", 10, "bold"), text_color=C_MUTED).pack(anchor="w", padx=16, pady=(12, 4))

        self._links_box = ctk.CTkTextbox(
            input_card, height=100,
            fg_color=C_BORDER, text_color=C_TEXT,
            font=("JetBrains Mono", 12),
        )
        self._links_box.pack(fill="x", padx=16, pady=(0, 8))
        self._links_box.insert("1.0", "https://t.me/somebot?start=REF123\nhttps://t.me/somechannel")

        # Кнопки
        btn_row = ctk.CTkFrame(input_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 12))
        self._run_btn = self.accent_btn(btn_row, "Запустить", self._start, "▶️")
        self._run_btn.pack(side="left", padx=(0, 8))
        self._stop_btn = self.stop_btn(btn_row, self._stop)
        self._stop_btn.pack(side="left", padx=(0, 8))
        self._clear_btn = ctk.CTkButton(btn_row, text="🗑  Очистить лог", command=self._clear_log,
                                         height=40, corner_radius=10, fg_color=C_BORDER,
                                         hover_color="#333", text_color=C_MUTED,
                                         font=("SF Pro Display", 13))
        self._clear_btn.pack(side="left")
        self._counter = ctk.CTkLabel(btn_row, text="", text_color=C_MUTED, font=("SF Pro", 12))
        self._counter.pack(side="right")

        # Лог
        self._log = LogBox(self)
        self._log.pack(fill="both", expand=True, padx=28, pady=16)

    def _poll_log(self):
        try:
            while True:
                ts, tag, msg = self.log_q.get_nowait()
                self._log.append(ts, tag, msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    def _start(self):
        if self._running:
            return
        # Применяем настройки
        try:
            cfg.BOT_REPLY_TIMEOUT      = int(self._e_BOT_REPLY_TIMEOUT.get())
            cfg.DELAY_BETWEEN_SPONSORS = int(self._e_DELAY_BETWEEN_SPONSORS.get())
            cfg.MAX_SUBSCRIBE_ROUNDS   = int(self._e_MAX_SUBSCRIBE_ROUNDS.get())
        except ValueError:
            pass

        text  = self._links_box.get("1.0", "end")
        links = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]

        if not links:
            self._log.append("", "warn", "⚠️  Нет ссылок")
            return

        if not self.runner.client:
            self._log.append("", "error", "❌ Сначала авторизуйся!")
            return

        self._running = True
        self._dot.set("running")
        self._counter.configure(text=f"0/{len(links)}")

        def done():
            self._running = False
            self.after(0, lambda: self._dot.set("ok"))

        self.runner.run_referral(links, done)

    def _stop(self):
        self.runner.stop()
        self._running = False
        self._dot.set("idle")

    def _clear_log(self):
        self._log.clear()


# ── Cleaner ────────────────────────────────────────────────────────────────────
class CleanerPage(BasePage):
    def __init__(self, master, runner, log_q, **kwargs):
        super().__init__(master, runner, log_q, **kwargs)
        self._running = False
        self._build()
        self._poll_log()

    def _build(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=28, pady=(28, 0))
        ctk.CTkLabel(hdr, text="Очистка аккаунта", font=("SF Pro Display", 22, "bold"), text_color=C_TEXT).pack(side="left")
        self._dot = StatusDot(hdr)
        self._dot.pack(side="left", padx=10)

        warn_card = self.card(self)
        warn_card.pack(fill="x", padx=28, pady=16)
        ctk.CTkLabel(warn_card,
                     text="⚠️  Этот инструмент выйдет из ВСЕХ каналов/групп и заблокирует всех ботов,\n"
                          "кроме указанных в исключениях. Действие необратимо.",
                     text_color=C_YELLOW, font=("SF Pro", 12), justify="left").pack(padx=16, pady=12)

        # Исключения
        exc_card = self.card(self)
        exc_card.pack(fill="x", padx=28, pady=0)
        ctk.CTkLabel(exc_card, text="ИСКЛЮЧЕНИЯ (ID через запятую или перенос строки)",
                     font=("SF Pro Display", 10, "bold"), text_color=C_MUTED).pack(anchor="w", padx=16, pady=(12, 4))
        self._exc_box = ctk.CTkTextbox(exc_card, height=80, fg_color=C_BORDER,
                                        text_color=C_TEXT, font=("JetBrains Mono", 12))
        self._exc_box.pack(fill="x", padx=16, pady=(0, 8))
        self._exc_box.insert("1.0", "# Вставь ID которые НЕ нужно трогать")

        btn_row = ctk.CTkFrame(exc_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 12))
        self.danger_btn(btn_row, "Начать очистку", self._start, "🧹").pack(side="left", padx=(0, 8))
        self.stop_btn(btn_row, self._stop).pack(side="left")

        self._log = LogBox(self)
        self._log.pack(fill="both", expand=True, padx=28, pady=16)

    def _poll_log(self):
        try:
            while True:
                ts, tag, msg = self.log_q.get_nowait()
                self._log.append(ts, tag, msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    def _start(self):
        if self._running:
            return
        if not self.runner.client:
            self._log.append("", "error", "❌ Сначала авторизуйся!")
            return

        # Парсим исключения
        raw = self._exc_box.get("1.0", "end")
        ids = set()
        for part in re.split(r"[\s,]+", raw):
            part = part.strip().lstrip("#")
            if part.isdigit():
                ids.add(int(part))
        cfg.EXCLUDE_IDS = ids
        self._log.append("", "info", f"Исключения: {ids or 'нет'}")

        self._running = True
        self._dot.set("running")

        def done():
            self._running = False
            self.after(0, lambda: self._dot.set("ok"))

        self.runner.run_cleaner(done)

    def _stop(self):
        self.runner.stop()
        self._running = False
        self._dot.set("idle")


# ── Toolbox ────────────────────────────────────────────────────────────────────
class ToolboxPage(BasePage):
    def __init__(self, master, runner, log_q, **kwargs):
        super().__init__(master, runner, log_q, **kwargs)
        self._build()
        self._poll_log()

    def _build(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=28, pady=(28, 0))
        ctk.CTkLabel(hdr, text="Инструменты", font=("SF Pro Display", 22, "bold"), text_color=C_TEXT).pack(side="left")

        # Сетка инструментов
        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.pack(fill="x", padx=28, pady=16)
        grid.columnconfigure((0, 1), weight=1, uniform="col")

        tools = [
            ("📥  Скачать медиа", "Скачивает все файлы из чата/канала", self._download_media, C_ACCENT),
            ("📄  Экспорт в JSON", "Сохраняет историю чата в JSON файл", self._export_json, C_ACCENT2),
            ("📊  Статистика диалогов", "Сохраняет список всех диалогов", self._dialog_stats, C_GREEN),
        ]

        for i, (title, desc, cmd, color) in enumerate(tools):
            card = ctk.CTkFrame(grid, fg_color=C_CARD, corner_radius=12)
            card.grid(row=i // 2, column=i % 2, padx=6, pady=6, sticky="nsew")

            ctk.CTkLabel(card, text=title, font=("SF Pro Display", 14, "bold"),
                         text_color=C_TEXT).pack(anchor="w", padx=16, pady=(14, 2))
            ctk.CTkLabel(card, text=desc, font=("SF Pro", 11),
                         text_color=C_MUTED).pack(anchor="w", padx=16, pady=(0, 8))

            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=(0, 12))

            e = ctk.CTkEntry(row, placeholder_text="@username или ссылка",
                              fg_color=C_BORDER, border_color=C_BORDER,
                              text_color=C_TEXT, font=("JetBrains Mono", 11))
            e.pack(side="left", fill="x", expand=True, padx=(0, 8))

            ctk.CTkButton(row, text="▶", width=36, height=36, corner_radius=8,
                           fg_color=color, hover_color="#333",
                           font=("SF Pro", 14, "bold"),
                           command=lambda c=cmd, entry=e: c(entry)).pack(side="left")

        self._log = LogBox(self)
        self._log.pack(fill="both", expand=True, padx=28, pady=16)

    def _poll_log(self):
        try:
            while True:
                ts, tag, msg = self.log_q.get_nowait()
                self._log.append(ts, tag, msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    def _check_client(self):
        if not self.runner.client:
            self._log.append("", "error", "❌ Сначала авторизуйся!")
            return False
        return True

    def _download_media(self, entry):
        if not self._check_client():
            return
        chat = entry.get().strip()
        if not chat:
            return
        self._log.append("", "info", f"📥 Скачиваю медиа из {chat}…")
        self.runner.run_download_media(chat, lambda: self._log.append("", "ok", "✅ Готово"))

    def _export_json(self, entry):
        if not self._check_client():
            return
        chat = entry.get().strip()
        if not chat:
            return
        self._log.append("", "info", f"📄 Экспорт {chat}…")
        self.runner.run_export_json(chat, lambda: self._log.append("", "ok", "✅ Готово"))

    def _dialog_stats(self, entry):
        if not self._check_client():
            return
        self._log.append("", "info", "📊 Получаю статистику…")
        self.runner.run_dialog_stats(lambda: self._log.append("", "ok", "✅ Готово"))


# ── Настройки ──────────────────────────────────────────────────────────────────
class SettingsPage(BasePage):
    def __init__(self, master, runner, log_q, **kwargs):
        super().__init__(master, runner, log_q, **kwargs)
        self._build()

    def _build(self):
        ctk.CTkLabel(self, text="Настройки", font=("SF Pro Display", 22, "bold"),
                     text_color=C_TEXT).pack(anchor="w", padx=28, pady=(28, 16))

        card = self.card(self)
        card.pack(fill="x", padx=28, pady=0)

        items = [
            ("Пауза между спонсорами (сек)", "DELAY_BETWEEN_SPONSORS", "8"),
            ("Таймаут ответа бота (сек)",     "BOT_REPLY_TIMEOUT",      "15"),
            ("Доп. таймаут после проверки",   "BOT_EXTRA_TIMEOUT",      "10"),
            ("Макс. раундов подписки",         "MAX_SUBSCRIBE_ROUNDS",   "3"),
            ("Пауза очистки (сек)",            "SLEEP",                  "1"),
        ]

        for label, attr, default in items:
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=6)
            ctk.CTkLabel(row, text=label, width=260, text_color=C_TEXT,
                         font=("SF Pro", 12), anchor="w").pack(side="left")
            e = ctk.CTkEntry(row, width=80, fg_color=C_BORDER, border_color=C_BORDER,
                              text_color=C_TEXT, font=("JetBrains Mono", 12))
            e.insert(0, default)
            e.pack(side="left")
            setattr(self, f"_e_{attr}", e)

        ctk.CTkFrame(card, fg_color=C_BORDER, height=1).pack(fill="x", padx=16, pady=8)

        self.accent_btn(card, "Сохранить", self._save).pack(anchor="w", padx=16, pady=(0, 16))

        self._msg = ctk.CTkLabel(self, text="", text_color=C_GREEN, font=("SF Pro", 12))
        self._msg.pack(padx=28, anchor="w")

    def _save(self):
        try:
            cfg.DELAY_BETWEEN_SPONSORS = int(self._e_DELAY_BETWEEN_SPONSORS.get())
            cfg.BOT_REPLY_TIMEOUT      = int(self._e_BOT_REPLY_TIMEOUT.get())
            cfg.BOT_EXTRA_TIMEOUT      = int(self._e_BOT_EXTRA_TIMEOUT.get())
            cfg.MAX_SUBSCRIBE_ROUNDS   = int(self._e_MAX_SUBSCRIBE_ROUNDS.get())
            cfg.SLEEP                  = float(self._e_SLEEP.get())
            self._msg.configure(text="✅ Настройки сохранены", text_color=C_GREEN)
        except ValueError:
            self._msg.configure(text="❌ Некорректные значения", text_color=C_RED)


# ══════════════════════════════════════════════════════════════════════════════
#  ГЛАВНОЕ ОКНО
# ══════════════════════════════════════════════════════════════════════════════

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("TG Tools")
        self.geometry("1100x720")
        self.minsize(900, 600)
        self.configure(fg_color=C_BG)

        # Очередь логов — ОДНА общая
        self.log_q  = queue.Queue()
        self.runner = AsyncRunner(self.log_q)

        if not TELETHON_OK:
            self._show_install_error()
            return

        self._build()

    def _show_install_error(self):
        ctk.CTkLabel(
            self,
            text=f"❌ Telethon не установлен\n\npip install telethon qrcode pillow customtkinter\n\n{TELETHON_ERR}",
            font=("SF Pro", 14), text_color=C_RED, justify="left",
        ).pack(expand=True)

    def _build(self):
        # Sidebar
        sidebar = ctk.CTkFrame(self, fg_color=C_SIDEBAR, width=200, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # Лого
        logo = ctk.CTkFrame(sidebar, fg_color="transparent")
        logo.pack(fill="x", padx=16, pady=(24, 32))
        ctk.CTkLabel(logo, text="TG", font=("SF Pro Display", 28, "bold"),
                     text_color=C_ACCENT).pack(side="left")
        ctk.CTkLabel(logo, text=" Tools", font=("SF Pro Display", 28),
                     text_color=C_TEXT).pack(side="left")

        # Навигация
        self._pages: dict[str, BasePage] = {}
        self._nav_btns: dict[str, SidebarBtn] = {}
        self._current = None

        nav_items = [
            ("auth",     "🔐", "Авторизация"),
            ("referral", "🔗", "Рефералы"),
            ("cleaner",  "🧹", "Очистка"),
            ("toolbox",  "🛠", "Инструменты"),
            ("settings", "⚙️", "Настройки"),
        ]

        nav_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        nav_frame.pack(fill="x", padx=8)

        for key, icon, label in nav_items:
            btn = SidebarBtn(nav_frame, label, icon,
                              command=lambda k=key: self._switch(k))
            btn.pack(fill="x", pady=2)
            self._nav_btns[key] = btn

        # Версия внизу
        ctk.CTkLabel(sidebar, text="v3.0", text_color=C_MUTED,
                     font=("SF Pro", 10)).pack(side="bottom", pady=12)

        # Область страниц
        self._content = ctk.CTkFrame(self, fg_color=C_BG, corner_radius=0)
        self._content.pack(side="left", fill="both", expand=True)

        # Создаём страницы
        page_classes = {
            "auth":     AuthPage,
            "referral": ReferralPage,
            "cleaner":  CleanerPage,
            "toolbox":  ToolboxPage,
            "settings": SettingsPage,
        }

        for key, cls in page_classes.items():
            page = cls(self._content, self.runner, self.log_q)
            page.place(relwidth=1, relheight=1)
            self._pages[key] = page

        self._switch("auth")

    def _switch(self, key: str):
        if self._current:
            self._pages[self._current].lower()
            self._nav_btns[self._current].set_active(False)
        self._pages[key].lift()
        self._nav_btns[key].set_active(True)
        self._current = key


# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # Создаём нужные папки
    for d in ["logs", "downloads", "exports"]:
        Path(d).mkdir(exist_ok=True)

    app = App()
    app.mainloop()
