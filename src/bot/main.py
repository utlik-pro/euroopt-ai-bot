import asyncio
import re
import html
import urllib.parse
import structlog
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from src.config import settings
from src.pipeline import Pipeline

from src.filters.pii_filter import mask_pii
from src.bot.access import (
    log_access,
    log_message,
    is_admin,
    is_private,
    check_rate_limit,
    match_tester,
    notify_admins,
)
from src.bot import whitelist_store
import time

logger = structlog.get_logger()

dp = Dispatcher()
pipeline: Pipeline | None = None


WELCOME_MESSAGE = """👋 Здравствуйте! Я AI-ассистент сети «Евроторг» — магазинов «Евроопт», «Грошык» и «Хит Дискаунтер».

Напишите свой вопрос в свободной форме — расскажу про акции, карту Еплюс, магазины, рецепты и «Удачу в придачу».

Примеры:
• «Что такое Красная цена?»
• «Какие акции в Хит?»
• «Как получить карту Еплюс?»
• «Призы в 214 туре "Удачи в придачу"?»
• «Адрес ближайшего Евроопта»"""


def markdown_to_html(text: str) -> str:
    """Конвертирует Markdown от LLM в HTML для Telegram.

    Поддерживает:
    - **bold** → <b>bold</b>
    - *italic* → <i>italic</i>
    - ### Heading → <b>Heading</b>
    - `code` → <code>code</code>
    - [text](url) → <a href="url">text</a>
    - ~~strike~~ → <s>strike</s>

    Также экранирует HTML-спецсимволы в остальном тексте.
    """
    # Сначала извлекаем всё что нельзя экранировать (ссылки, код)
    placeholders = {}
    counter = [0]

    def save_placeholder(match, tag):
        counter[0] += 1
        key = f"\x00PH{counter[0]}\x00"
        if tag == "a":
            link_text = html.escape(match.group(1))
            link_url = html.escape(match.group(2), quote=True)
            placeholders[key] = f'<a href="{link_url}">{link_text}</a>'
        elif tag == "code":
            placeholders[key] = f"<code>{html.escape(match.group(1))}</code>"
        return key

    # Ссылки [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: save_placeholder(m, "a"), text)
    # Inline код `...`
    text = re.sub(r"`([^`]+)`", lambda m: save_placeholder(m, "code"), text)

    # Теперь экранируем HTML-символы
    text = html.escape(text, quote=False)

    # Заголовки: ### text или ## text или # text → <b>text</b>
    text = re.sub(r"^#{1,6}\s+(.+?)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # Bold **text** → <b>text</b>
    text = re.sub(r"\*\*([^*\n]+?)\*\*", r"<b>\1</b>", text)

    # Italic *text* → <i>text</i> (но не **)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", text)

    # Strike ~~text~~ → <s>text</s>
    text = re.sub(r"~~([^~\n]+?)~~", r"<s>\1</s>", text)

    # Возвращаем placeholders
    for key, val in placeholders.items():
        text = text.replace(key, val)

    return text


async def _gate(message: types.Message, incoming_text: str) -> bool:
    """Полный gate: private-only → whitelist → rate limit. Возвращает True если доступ разрешён."""
    user = message.from_user
    chat_type = message.chat.type
    # ДС №1 п. 2.1.1: входящий текст в лог пишем замаскированным.
    masked_incoming, _ = mask_pii(incoming_text)
    log_message(user, "in", masked_incoming, extra={"chat_type": chat_type, "chat_id": message.chat.id})

    # 1. Private only
    if settings.non_private_ignore and not is_private(message):
        log_access(user, "non_private", reason="group chat ignored", chat_type=chat_type)
        logger.warning("non_private_ignored", user_id=user.id, chat_type=chat_type, chat_id=message.chat.id)
        return False

    # 2. Admin всегда доступ
    if is_admin(user.id):
        log_access(user, "approved", reason="admin", chat_type=chat_type)
        return True

    if not settings.whitelist_enabled:
        log_access(user, "approved", reason="whitelist_disabled", chat_type=chat_type)
        return True

    # 3. Whitelist state
    if whitelist_store.is_denied(user.id):
        log_access(user, "denied", reason="persisted_denied", chat_type=chat_type)
        return False

    if not whitelist_store.is_approved(user.id):
        # Pre-approved по @username (внутренняя команда) — автоодобрение и залочка на user_id
        uname = (user.username or "").lower()
        if uname and uname in settings.pre_approved_usernames_set():
            whitelist_store.add_pending(user.id, user.username, user.first_name or "", user.last_name, incoming_text)
            whitelist_store.approve(user.id, 0, note=f"auto-approved by pre_approved_username @{uname}")
            log_access(user, "approved", reason=f"pre_approved_username @{uname}", chat_type=chat_type)
            return True

        # Первый контакт — в pending, уведомить админов
        is_new = whitelist_store.add_pending(
            user.id, user.username, user.first_name or "", user.last_name, incoming_text
        )
        log_access(user, "pending" if is_new else "pending_repeat", reason="awaiting_approval", chat_type=chat_type)
        if is_new:
            await notify_admins(message.bot, user, incoming_text)
        await message.answer(settings.access_pending_message)
        log_message(user, "out", settings.access_pending_message, extra={"pending": True})
        return False

    # 4. Rate limit
    allowed, remaining, reset_in = check_rate_limit(user.id)
    if not allowed:
        # Формируем сообщение с точным временем когда освободится следующий слот.
        mins = max(1, reset_in // 60)
        secs = reset_in % 60
        if mins >= 2:
            wait_txt = f"через {mins} мин"
        elif mins == 1 and secs < 30:
            wait_txt = f"через {secs} сек"
        else:
            wait_txt = f"через 1 мин"
        limit_msg = (
            f"⏳ Достигнут лимит {settings.rate_limit_per_hour} сообщений в час. "
            f"Следующий вопрос можно задать {wait_txt}."
        )
        log_access(user, "rate_limited", reason=f"limit={settings.rate_limit_per_hour}/h,reset_in={reset_in}s", chat_type=chat_type)
        await message.answer(limit_msg)
        log_message(user, "out", limit_msg, extra={"rate_limited": True, "reset_in_sec": reset_in})
        return False

    log_access(user, "approved", reason=f"ok,remaining={remaining}", chat_type=chat_type)
    return True


# ── Админ-команды ──

@dp.message(Command("approve"))
async def cmd_approve(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/approve &lt;user_id&gt; [note]</code>")
        return
    uid = int(parts[1])
    note = parts[2] if len(parts) > 2 else ""
    rec = whitelist_store.approve(uid, message.from_user.id, note)
    await message.answer(f"✅ user_id <code>{uid}</code> approved. {rec.get('username') or ''} {rec.get('first_name') or ''}")
    try:
        await message.bot.send_message(uid, "✅ Ваш доступ к AI-помощнику подтверждён. Можете задавать вопросы.")
    except Exception:
        pass


@dp.message(Command("deny"))
async def cmd_deny(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/deny &lt;user_id&gt;</code>")
        return
    uid = int(parts[1])
    whitelist_store.deny(uid, message.from_user.id)
    await message.answer(f"🚫 user_id <code>{uid}</code> denied.")


@dp.message(Command("revoke"))
async def cmd_revoke(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/revoke &lt;user_id&gt;</code>")
        return
    uid = int(parts[1])
    ok = whitelist_store.revoke(uid, message.from_user.id)
    await message.answer(f"{'🚫 Revoked' if ok else '⚠️ Not found'}: <code>{uid}</code>")


@dp.message(Command("pending"))
async def cmd_pending(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    pend = whitelist_store.list_pending()
    if not pend:
        await message.answer("📭 Нет заявок на рассмотрении.")
        return
    lines = ["🕐 <b>Pending заявки:</b>\n"]
    for uid, rec in pend.items():
        hint = match_tester(rec.get("username"), rec.get("first_name") or "", rec.get("last_name"))
        hint_str = f" [✅ {hint['fio']}]" if hint else " [⚠️ нет в списке]"
        lines.append(
            f"• <code>{uid}</code> @{rec.get('username') or '—'} {rec.get('first_name') or ''}{hint_str}\n"
            f"  /approve {uid}   /deny {uid}"
        )
    await message.answer("\n".join(lines))


@dp.message(Command("whitelist"))
async def cmd_whitelist(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    appr = whitelist_store.list_approved()
    if not appr:
        await message.answer("📭 Whitelist пуст.")
        return
    lines = [f"✅ <b>Approved ({len(appr)}):</b>\n"]
    for uid, rec in appr.items():
        lines.append(f"• <code>{uid}</code> @{rec.get('username') or '—'} {rec.get('first_name') or ''}")
    await message.answer("\n".join(lines))


@dp.message(Command("myid"))
async def cmd_myid(message: types.Message):
    await message.answer(
        f"🆔 Ваш Telegram ID: <code>{message.from_user.id}</code>\n"
        f"📛 Username: @{message.from_user.username or '—'}"
    )


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    logger.info("cmd_start", user_id=message.from_user.id, username=message.from_user.username)
    if not await _gate(message, "/start"):
        return
    await message.answer(WELCOME_MESSAGE)
    log_message(message.from_user, "out", WELCOME_MESSAGE)


@dp.message()
async def handle_message(message: types.Message):
    if not message.text:
        await message.answer("Пожалуйста, отправьте текстовое сообщение.")
        return

    if not await _gate(message, message.text):
        return

    # structlog-превью тоже маскируем: сырой текст в stdout не должен течь.
    masked_preview, _ = mask_pii(message.text[:100])
    logger.info("user_message", user_id=message.from_user.id, username=message.from_user.username, text=masked_preview)

    # Show typing indicator
    await message.bot.send_chat_action(message.chat.id, "typing")

    t0 = time.time()
    response = await pipeline.process(message.text, message.from_user.id)
    await message.answer(markdown_to_html(response))
    log_message(message.from_user, "out", response, latency_ms=int((time.time() - t0) * 1000))


async def main():
    global pipeline

    import logging
    logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO))
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level, logging.INFO)
        ),
    )

    logger.info("bot_starting", model=settings.llm_model, provider=settings.llm_provider)

    # RAG предсобран в Docker image (избегаем OOM при runtime reindex).
    # При изменениях данных нужен новый git push → Render пересоберёт образ.
    try:
        from src.rag.engine import RAGEngine
        rag = RAGEngine()
        logger.info("rag_ready", docs=rag.collection.count())
    except Exception as e:
        logger.error("rag_init_error", error=str(e))

    pipeline = Pipeline()
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    logger.info("bot_started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
