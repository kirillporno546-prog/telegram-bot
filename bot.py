import os
import logging
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters
)

from database import init_db
from handlers import (
    # User
    start, balance, earn_stars, tasks_menu, invite_friends, withdraw_menu,
    user_callback,
    # Sponsor check
    check_sponsors_callback,
    # Channel
    channel_withdrawal_callback,
    # Admin
    admin_panel, admin_callback, stats_command, withdrawals_command,
    # Add task conversation
    admin_add_task_start, add_task_title, add_task_desc, add_task_link, add_task_reward,
    # Edit task conversation
    admin_edit_task_start, edit_task_value,
    # Broadcast conversation
    admin_broadcast_start, broadcast_send,
    # Settings conversation
    settings_value,
    # Add sponsor conversation
    admin_add_sponsor_start, add_sponsor_name, add_sponsor_channel,
    # Shared
    cancel_conv,
    # States
    ADD_TITLE, ADD_DESC, ADD_LINK, ADD_REWARD,
    EDIT_VALUE, BROADCAST_TEXT, SETTINGS_VALUE,
    ADD_SPONSOR_NAME, ADD_SPONSOR_CHANNEL,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(application):
    await init_db()
    logger.info("База данных инициализирована.")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Переменная окружения TELEGRAM_BOT_TOKEN не задана!")

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    # ── Add task conversation ──────────────────────────────────────────────────
    add_task_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_task_start, pattern="^admin_add_task$")],
        states={
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_title)],
            ADD_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_desc)],
            ADD_LINK:  [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_link)],
            ADD_REWARD:[MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_reward)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        per_message=False,
    )

    # ── Edit task conversation ─────────────────────────────────────────────────
    edit_task_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_edit_task_start, pattern="^adm_edit_")],
        states={
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_task_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        per_message=False,
    )

    # ── Broadcast conversation ─────────────────────────────────────────────────
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern="^admin_broadcast$")],
        states={
            BROADCAST_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        per_message=False,
    )

    # ── Settings conversation ──────────────────────────────────────────────────
    settings_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^adm_set_")],
        states={
            SETTINGS_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, settings_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        per_message=False,
    )

    # ── Add sponsor conversation ───────────────────────────────────────────────
    add_sponsor_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_sponsor_start, pattern="^admin_add_sponsor$")],
        states={
            ADD_SPONSOR_NAME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_sponsor_name)],
            ADD_SPONSOR_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_sponsor_channel)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        per_message=False,
    )

    # ── Register all handlers ──────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("withdrawals", withdrawals_command))

    # Conversations (must be before generic CallbackQueryHandler)
    app.add_handler(add_task_conv)
    app.add_handler(edit_task_conv)
    app.add_handler(broadcast_conv)
    app.add_handler(settings_conv)
    app.add_handler(add_sponsor_conv)

    # Menu buttons
    app.add_handler(MessageHandler(filters.Regex("^⭐ Баланс$"), balance))
    app.add_handler(MessageHandler(filters.Regex("^⭐ Заработать звёзды$"), earn_stars))
    app.add_handler(MessageHandler(filters.Regex("^📋 Задания$"), tasks_menu))
    app.add_handler(MessageHandler(filters.Regex("^👥 Пригласить друзей$"), invite_friends))
    app.add_handler(MessageHandler(filters.Regex("^💸 Вывод$"), withdraw_menu))

    # Sponsor subscription check
    app.add_handler(CallbackQueryHandler(check_sponsors_callback, pattern="^check_sponsors$"))

    # Admin inline callbacks (non-conversation)
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(admin_|adm_)"))

    # Channel withdrawal approval buttons
    app.add_handler(CallbackQueryHandler(channel_withdrawal_callback, pattern="^wd_"))

    # User inline callbacks (catch-all)
    app.add_handler(CallbackQueryHandler(user_callback))

    logger.info("Бот запускается...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
