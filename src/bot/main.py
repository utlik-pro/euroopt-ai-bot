import asyncio
import structlog
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode

from src.config import settings
from src.pipeline import Pipeline

logger = structlog.get_logger()

dp = Dispatcher()
pipeline: Pipeline | None = None


WELCOME_MESSAGE = """👋 Здравствуйте! Я AI-помощник сетей «Евроопт», «Грошык» и «Хит Дискаунтер».

Чем могу помочь:
🔥 Актуальные акции и предложения
🍳 Рецепты с ингредиентами из наших магазинов
❓ Ответы на вопросы о магазинах, доставке, оплате

Просто напишите свой вопрос!

Примеры:
• «Какие сейчас акции?»
• «Что приготовить на ужин?»
• «Как оформить доставку?»"""

HELP_MESSAGE = """🤖 Команды:
/start — начать диалог
/help — эта справка
/акции — актуальные акции
/рецепты — популярные рецепты

Или просто напишите вопрос в свободной форме!"""


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    logger.info("cmd_start", user_id=message.from_user.id)
    await message.answer(WELCOME_MESSAGE)


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(HELP_MESSAGE)


@dp.message(Command("акции"))
async def cmd_promotions(message: types.Message):
    response = await pipeline.process("Покажи все актуальные акции", message.from_user.id)
    await message.answer(response)


@dp.message(Command("рецепты"))
async def cmd_recipes(message: types.Message):
    response = await pipeline.process("Покажи популярные рецепты", message.from_user.id)
    await message.answer(response)


@dp.message()
async def handle_message(message: types.Message):
    if not message.text:
        await message.answer("Пожалуйста, отправьте текстовое сообщение.")
        return

    logger.info("user_message", user_id=message.from_user.id, text=message.text[:100])

    # Show typing indicator
    await message.bot.send_chat_action(message.chat.id, "typing")

    response = await pipeline.process(message.text, message.from_user.id)
    await message.answer(response)


async def main():
    global pipeline

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            structlog.get_level_from_name(settings.log_level)
        ),
    )

    logger.info("bot_starting", model=settings.llm_model, provider=settings.llm_provider)

    pipeline = Pipeline()
    bot = Bot(token=settings.telegram_bot_token)

    logger.info("bot_started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
