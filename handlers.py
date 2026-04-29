import logging
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from database import (
    get_or_create_user, get_user, get_all_tasks, get_task_by_id,
    get_completed_tasks, get_completed_task_count, complete_task,
    get_referral_count, get_rewarded_referral_count,
    get_pending_referral, mark_referral_rewarded,
    create_withdrawal, get_withdrawal_by_id, store_withdrawal_channel_msg,
    update_withdrawal_status,
    add_task, update_task_field, delete_task, toggle_task,
    get_all_user_ids, get_stats, get_all_settings, set_setting, get_setting,
    get_all_withdrawals,
    get_active_sponsors, get_all_sponsors, add_sponsor, delete_sponsor, toggle_sponsor,
)
from keyboards import (
    main_menu_keyboard, sponsors_keyboard,
    tasks_inline_keyboard, task_detail_keyboard,
    withdraw_options_keyboard, withdraw_confirm_keyboard,
    withdrawal_channel_keyboard,
    admin_main_keyboard, admin_task_list_keyboard, admin_task_detail_keyboard,
    admin_task_delete_confirm_keyboard, admin_settings_keyboard,
    admin_sponsors_list_keyboard,
    PAYOUT_CHANNEL_NAME,
)

logger = logging.getLogger(__name__)

ADMIN_ID = 6820965428

# ConversationHandler states
(
    ADD_TITLE, ADD_DESC, ADD_LINK, ADD_REWARD,
    EDIT_VALUE,
    BROADCAST_TEXT,
    SETTINGS_VALUE,
    ADD_SPONSOR_NAME,
    ADD_SPONSOR_CHANNEL,
) = range(9)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize_channel(raw: str):
    """Normalize channel input to (channel_username, channel_link)."""
    raw = raw.strip()
    if raw.startswith("https://t.me/"):
        username = "@" + raw.split("https://t.me/")[1].rstrip("/")
    elif raw.startswith("t.me/"):
        username = "@" + raw.split("t.me/")[1].rstrip("/")
    elif raw.startswith("@"):
        username = raw
    else:
        username = "@" + raw
    link = f"https://t.me/{username[1:]}"
    return username, link


def _back_keyboard():
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_back")]])


async def _try_give_referral_reward(user_id: int, username: str, context: ContextTypes.DEFAULT_TYPE):
    """
    Check all anti-fraud conditions and give referral reward if eligible:
    - invited user has completed 3+ tasks
    - invited user is subscribed to all active sponsors
    - referral reward not yet given
    - referrer hasn't exceeded daily limit of 10
    """
    # Check task count
    task_count = await get_completed_task_count(user_id)
    if task_count < 3:
        return

    # Check all active sponsors
    sponsors = await get_active_sponsors()
    for sponsor in sponsors:
        try:
            member = await context.bot.get_chat_member(sponsor["channel_username"], user_id)
            if member.status not in ("member", "administrator", "creator"):
                return
        except Exception:
            return  # Can't verify membership → skip

    # Check if there's a pending referral
    pending = await get_pending_referral(user_id)
    if not pending:
        return

    referrer_id, reward_amount = pending
    await mark_referral_rewarded(user_id, referrer_id, reward_amount)

    # Notify referrer
    uname = f"@{username}" if username else "пользователь"
    try:
        await context.bot.send_message(
            chat_id=referrer_id,
            text=(
                f"Ваш друг стал активным!\n\n"
                f"Пользователь: {uname}\n"
                f"Начислено: +{reward_amount}⭐"
            )
        )
    except Exception as e:
        logger.warning(f"Не удалось уведомить реферера {referrer_id}: {e}")


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    ref_code = args[0] if args else None

    await get_or_create_user(
        user_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
        referred_by_code=ref_code,
    )

    # Check if there are active sponsors — show subscription gate if so
    sponsors = await get_active_sponsors()
    if sponsors:
        text = (
            f"Для продолжения подпишитесь на спонсоров:\n\n"
            f"После подписки нажмите кнопку ниже."
        )
        await update.message.reply_text(text, reply_markup=sponsors_keyboard(sponsors))
        return

    # No sponsors — show main menu directly
    db_user = await get_user(user.id)
    welcome = (
        f"Привет, {user.first_name}!\n\n"
        f"Добро пожаловать в бот вознаграждений!\n\n"
        f"Заработать звёзды — выполняй задания и копи звёзды\n"
        f"Пригласить друзей — получай 3.5 звезды за каждого приглашённого\n"
        f"Задания — зарабатывай по 0.35 звезды за каждое задание\n"
        f"Вывод — выводи накопленные звёзды\n\n"
        f"Твой баланс: {db_user['stars']:.2f}⭐\n\n"
        f"Используй меню ниже, чтобы начать!"
    )
    await update.message.reply_text(welcome, reply_markup=main_menu_keyboard())


# ── Sponsor subscription check callback ───────────────────────────────────────

async def check_sponsors_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    sponsors = await get_active_sponsors()
    not_subscribed = []

    for sponsor in sponsors:
        try:
            member = await context.bot.get_chat_member(sponsor["channel_username"], user.id)
            if member.status not in ("member", "administrator", "creator"):
                not_subscribed.append(sponsor)
        except Exception:
            not_subscribed.append(sponsor)

    if not_subscribed:
        await query.edit_message_text(
            "Вы подписались не на все каналы.\n\nПожалуйста, подпишитесь на всех спонсоров:",
            reply_markup=sponsors_keyboard(sponsors)
        )
        return

    # All subscribed — show welcome and trigger referral check
    await query.edit_message_text(
        "Подписка проверена!\n\nДобро пожаловать! Теперь ты можешь пользоваться ботом.",
    )

    db_user = await get_user(user.id)
    welcome = (
        f"Привет, {user.first_name}!\n\n"
        f"Твой баланс: {db_user['stars']:.2f}⭐\n\n"
        f"Используй меню ниже, чтобы начать!"
    )
    await context.bot.send_message(
        chat_id=user.id,
        text=welcome,
        reply_markup=main_menu_keyboard()
    )

    # Trigger anti-fraud referral reward check
    await _try_give_referral_reward(user.id, user.username or "", context)


# ── User menu handlers ────────────────────────────────────────────────────────

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = await get_or_create_user(user.id, user.username or "", user.first_name or "")
    ref_count = await get_referral_count(user.id)
    rewarded_count = await get_rewarded_referral_count(user.id)
    min_wd = await get_setting("min_withdrawal")
    ref_reward = await get_setting("referral_reward")

    text = (
        f"Твой баланс\n\n"
        f"Звёзды: {db_user['stars']:.2f}⭐\n"
        f"Приглашено друзей: {ref_count}\n"
        f"Активных рефералов: {rewarded_count}\n\n"
        f"Минимальный вывод: {min_wd}⭐\n"
        f"За каждого активного друга: +{ref_reward}⭐\n"
        f"За каждое задание: +0.35⭐"
    )
    await update.message.reply_text(text, reply_markup=main_menu_keyboard())


async def earn_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = await get_or_create_user(user.id, user.username or "", user.first_name or "")
    ref_reward = await get_setting("referral_reward")
    task_reward = await get_setting("default_task_reward")

    text = (
        f"Заработать звёзды\n\n"
        f"У тебя сейчас: {db_user['stars']:.2f}⭐\n\n"
        f"Способы заработка:\n\n"
        f"Выполнять задания — +{task_reward}⭐ за каждое\n"
        f"Приглашать друзей — +{ref_reward}⭐ когда друг выполнит 3 задания\n\n"
        f"Используй меню, чтобы начать зарабатывать!"
    )
    await update.message.reply_text(text, reply_markup=main_menu_keyboard())


async def tasks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await get_or_create_user(user.id, user.username or "", user.first_name or "")
    tasks = await get_all_tasks()
    completed = await get_completed_tasks(user.id)

    if not tasks:
        await update.message.reply_text("Заданий пока нет. Загляни позже!", reply_markup=main_menu_keyboard())
        return

    done_count = len([t for t in tasks if t["id"] in completed])
    text = f"Задания ({done_count}/{len(tasks)} выполнено)\n\nНажми на задание, чтобы увидеть подробности:"
    await update.message.reply_text(text, reply_markup=tasks_inline_keyboard(tasks, completed))


async def invite_friends(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = await get_or_create_user(user.id, user.username or "", user.first_name or "")
    ref_count = await get_referral_count(user.id)
    rewarded_count = await get_rewarded_referral_count(user.id)
    ref_reward = await get_setting("referral_reward")

    bot_info = await context.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={db_user['referral_code']}"

    text = (
        f"Пригласить друзей\n\n"
        f"Приглашай друзей и получай {ref_reward}⭐ за каждого активного!\n\n"
        f"Друг считается активным, когда:\n"
        f"  подпишется на всех спонсоров\n"
        f"  выполнит 3 задания\n\n"
        f"Твоя реферальная ссылка:\n{ref_link}\n\n"
        f"Приглашено друзей: {ref_count}\n"
        f"Активных рефералов: {rewarded_count}\n"
        f"Заработано с рефералов: {rewarded_count * float(ref_reward):.1f}⭐\n\n"
        f"Делись ссылкой с друзьями!"
    )
    await update.message.reply_text(text, reply_markup=main_menu_keyboard())


async def withdraw_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = await get_or_create_user(user.id, user.username or "", user.first_name or "")
    payout_ch = await get_setting("payout_channel")

    text = (
        f"Вывод звёзд\n\n"
        f"Заработано: {db_user['stars']:.2f}⭐\n\n"
        f"Выбери сумму для вывода\n\n"
        f"Канал с выводами: {payout_ch}"
    )
    await update.message.reply_text(text, reply_markup=withdraw_options_keyboard())


# ── Inline callbacks (user) ───────────────────────────────────────────────────

async def user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "noop":
        return

    if data == "back_tasks":
        user = query.from_user
        tasks = await get_all_tasks()
        completed = await get_completed_tasks(user.id)
        done_count = len([t for t in tasks if t["id"] in completed])
        text = f"Задания ({done_count}/{len(tasks)} выполнено)\n\nНажми на задание, чтобы увидеть подробности:"
        await query.edit_message_text(text, reply_markup=tasks_inline_keyboard(tasks, completed))
        return

    if data.startswith("task_"):
        task_id = int(data.split("_")[1])
        tasks = await get_all_tasks()
        task = next((t for t in tasks if t["id"] == task_id), None)
        if not task:
            await query.answer("Задание не найдено.", show_alert=True)
            return
        user = query.from_user
        completed = await get_completed_tasks(user.id)
        is_done = task_id in completed
        text = (
            f"Задание: {task['title']}\n\n"
            f"{task['description']}\n\n"
            f"Награда: {task['reward_stars']}⭐\n"
            f"Статус: {'Выполнено' if is_done else 'Не выполнено'}"
        )
        await query.edit_message_text(text, reply_markup=task_detail_keyboard(task, is_done))

    elif data.startswith("done_"):
        task_id = int(data.split("_")[1])
        user = query.from_user
        success, result = await complete_task(user.id, task_id)
        if success:
            task = result
            await query.answer(f"Задание выполнено! +{task['reward_stars']}⭐", show_alert=True)
            text = (
                f"Задание: {task['title']}\n\n"
                f"{task['description']}\n\n"
                f"Награда: {task['reward_stars']}⭐\n"
                f"Статус: Выполнено"
            )
            await query.edit_message_text(text, reply_markup=task_detail_keyboard(task, True))
            # Check if referral reward conditions are now met
            await _try_give_referral_reward(user.id, user.username or "", context)
        elif result == "already_done":
            await query.answer("Ты уже выполнил это задание!", show_alert=True)
        else:
            await query.answer("Задание не найдено.", show_alert=True)

    elif data.startswith("withdraw_"):
        amount = int(data.split("_")[1])
        user = query.from_user
        db_user = await get_user(user.id)
        payout_ch = await get_setting("payout_channel")
        if not db_user or db_user["stars"] < amount:
            stars = db_user["stars"] if db_user else 0
            await query.answer(
                f"Недостаточно звёзд. У тебя {stars:.2f}⭐, нужно {amount}⭐.",
                show_alert=True,
            )
            return
        text = (
            f"Подтверждение вывода\n\n"
            f"Сумма: {amount}⭐\n"
            f"Баланс после вывода: {db_user['stars'] - amount:.2f}⭐\n\n"
            f"Выплата придёт через канал {payout_ch}\n\n"
            f"Подтвердить?"
        )
        await query.edit_message_text(text, reply_markup=withdraw_confirm_keyboard(amount))

    elif data.startswith("confirm_withdraw_"):
        amount = int(data.split("_")[2])
        user = query.from_user
        payout_ch = await get_setting("payout_channel")
        success, result = await create_withdrawal(user.id, float(amount))
        if success:
            withdrawal_id = result
            await query.edit_message_text(
                f"Заявка на вывод принята!\n\n"
                f"Сумма: {amount}⭐\n\n"
                f"Выплата будет произведена через канал {payout_ch}\n"
                f"Ожидай зачисления!"
            )
            uname = f"@{user.username}" if user.username else user.first_name
            channel_text = (
                f"Новая заявка на вывод\n\n"
                f"Пользователь: {uname}\n"
                f"ID: {user.id}\n"
                f"Сумма: {amount}⭐\n\n"
                f"Статус: Ожидает отправки"
            )
            try:
                sent = await context.bot.send_message(
                    chat_id=payout_ch,
                    text=channel_text,
                    reply_markup=withdrawal_channel_keyboard(withdrawal_id),
                )
                await store_withdrawal_channel_msg(
                    withdrawal_id, str(payout_ch), sent.message_id, uname
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить в канал: {e}")
        elif result == "insufficient_stars":
            await query.answer("Недостаточно звёзд для вывода.", show_alert=True)
        else:
            await query.answer("Ошибка при создании заявки. Попробуй позже.", show_alert=True)

    elif data == "cancel_withdraw":
        await query.edit_message_text("Вывод отменён.")


# ── Channel withdrawal approval callbacks ─────────────────────────────────────

async def channel_withdrawal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if query.from_user.id != ADMIN_ID:
        await query.answer("Нет доступа", show_alert=True)
        return

    if data.startswith("wd_pending_"):
        await query.answer("Заявка ожидает отправки", show_alert=False)
        return

    if data.startswith("wd_sent_"):
        withdrawal_id = int(data.split("_")[2])
        wd = await get_withdrawal_by_id(withdrawal_id)
        if not wd:
            await query.answer("Заявка не найдена.", show_alert=True)
            return
        if wd.get("status") == "sent":
            await query.answer("Уже отмечено как отправлено.", show_alert=True)
            return

        await update_withdrawal_status(withdrawal_id, "sent")

        uname = wd.get("wd_username") or str(wd["user_id"])
        updated_text = (
            f"Выплата отправлена\n\n"
            f"Пользователь: {uname}\n"
            f"ID: {wd['user_id']}\n"
            f"Сумма: {int(wd['amount'])}⭐\n\n"
            f"Статус: Отправлено"
        )
        await query.edit_message_text(
            updated_text,
            reply_markup=withdrawal_channel_keyboard(withdrawal_id, is_sent=True),
        )
        await query.answer("Выплата отмечена как отправленная")

        try:
            await context.bot.send_message(
                chat_id=wd["user_id"],
                text=f"Ваша выплата отправлена!\n\nСумма: {int(wd['amount'])}⭐\n\nПроверь канал выплат."
            )
        except Exception as e:
            logger.warning(f"Не удалось уведомить пользователя {wd['user_id']}: {e}")


# ── Admin panel ───────────────────────────────────────────────────────────────

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    await update.message.reply_text("Админ панель", reply_markup=admin_main_keyboard())


# ── Admin callbacks ───────────────────────────────────────────────────────────

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    if user.id != ADMIN_ID:
        await query.answer("Нет доступа.", show_alert=True)
        return

    data = query.data

    if data == "admin_back":
        await query.edit_message_text("Админ панель", reply_markup=admin_main_keyboard())

    elif data == "admin_stats":
        s = await get_stats()
        text = (
            f"Статистика\n\n"
            f"Пользователей всего: {s['users_total']}\n"
            f"Новых сегодня: {s['users_today']}\n"
            f"Рефералов всего: {s['referrals_total']}\n"
            f"Заданий всего: {s['tasks_total']}\n"
            f"Заявок на вывод: {s['withdrawals_total']}\n"
            f"Активных спонсоров: {s['sponsors_total']}\n"
            f"Всего звёзд на балансах: {s['total_stars']:.2f}⭐"
        )
        await query.edit_message_text(text, reply_markup=_back_keyboard())

    elif data == "admin_tasks":
        tasks = await get_all_tasks(include_inactive=True)
        if not tasks:
            await query.edit_message_text("Нет заданий.", reply_markup=_back_keyboard())
            return
        await query.edit_message_text(
            "Управление заданиями\n\nВыбери задание:",
            reply_markup=admin_task_list_keyboard(tasks)
        )

    elif data.startswith("adm_task_"):
        task_id = int(data.split("_")[2])
        task = await get_task_by_id(task_id)
        if not task:
            await query.answer("Задание не найдено.", show_alert=True)
            return
        status = "Активно" if task["is_active"] else "Отключено"
        text = (
            f"Задание [{task['id']}]\n\n"
            f"Название: {task['title']}\n"
            f"Описание: {task['description']}\n"
            f"Ссылка: {task['url'] or '—'}\n"
            f"Награда: {task['reward_stars']}⭐\n"
            f"Статус: {status}"
        )
        await query.edit_message_text(text, reply_markup=admin_task_detail_keyboard(task))

    elif data.startswith("adm_toggle_"):
        task_id = int(data.split("_")[2])
        await toggle_task(task_id)
        task = await get_task_by_id(task_id)
        status = "Активно" if task["is_active"] else "Отключено"
        text = (
            f"Задание [{task['id']}]\n\n"
            f"Название: {task['title']}\n"
            f"Описание: {task['description']}\n"
            f"Ссылка: {task['url'] or '—'}\n"
            f"Награда: {task['reward_stars']}⭐\n"
            f"Статус: {status}"
        )
        await query.edit_message_text(text, reply_markup=admin_task_detail_keyboard(task))

    elif data.startswith("adm_delete_confirm_"):
        task_id = int(data.split("_")[3])
        await delete_task(task_id)
        tasks = await get_all_tasks(include_inactive=True)
        if not tasks:
            await query.edit_message_text("Задание удалено. Заданий больше нет.", reply_markup=_back_keyboard())
        else:
            await query.edit_message_text("Задание удалено.", reply_markup=admin_task_list_keyboard(tasks))

    elif data.startswith("adm_delete_"):
        task_id = int(data.split("_")[2])
        task = await get_task_by_id(task_id)
        await query.edit_message_text(
            f"Удалить задание [{task_id}] {task['title'] if task else ''}?\nЭто действие необратимо.",
            reply_markup=admin_task_delete_confirm_keyboard(task_id)
        )

    elif data == "admin_settings":
        settings = await get_all_settings()
        await query.edit_message_text(
            "Настройки\n\nНажми на параметр, чтобы изменить его:",
            reply_markup=admin_settings_keyboard(settings)
        )

    elif data.startswith("adm_set_"):
        setting_key = data.removeprefix("adm_set_")
        labels = {
            "referral_reward": "новое значение реферальной награды (например: 3.5)",
            "min_withdrawal": "новый минимальный вывод в звёздах (например: 15)",
            "default_task_reward": "новую награду за задание по умолчанию (например: 0.35)",
            "payout_channel": "новый канал выплат (например: @myChannel)",
        }
        label = labels.get(setting_key, "новое значение")
        context.user_data["settings_key"] = setting_key
        await query.edit_message_text(f"Введи {label}:\n\n(Отправь /cancel для отмены)")
        return SETTINGS_VALUE

    elif data == "admin_sponsors":
        sponsors = await get_all_sponsors()
        if not sponsors:
            await query.edit_message_text("Спонсоров нет.", reply_markup=_back_keyboard())
            return
        await query.edit_message_text(
            "Все спонсоры\n\nНажми на спонсора для управления:",
            reply_markup=admin_sponsors_list_keyboard(sponsors)
        )

    elif data.startswith("adm_sponsor_toggle_"):
        sponsor_id = int(data.split("_")[3])
        await toggle_sponsor(sponsor_id)
        sponsors = await get_all_sponsors()
        if not sponsors:
            await query.edit_message_text("Спонсоров нет.", reply_markup=_back_keyboard())
        else:
            await query.edit_message_text(
                "Все спонсоры",
                reply_markup=admin_sponsors_list_keyboard(sponsors)
            )

    elif data.startswith("adm_sponsor_del_"):
        sponsor_id = int(data.split("_")[3])
        await delete_sponsor(sponsor_id)
        sponsors = await get_all_sponsors()
        if not sponsors:
            await query.edit_message_text("Спонсор удалён. Спонсоров больше нет.", reply_markup=_back_keyboard())
        else:
            await query.edit_message_text(
                "Спонсор удалён.\n\nВсе спонсоры:",
                reply_markup=admin_sponsors_list_keyboard(sponsors)
            )

    return ConversationHandler.END


def _back_keyboard():
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_back")]])


# ── Admin: Add task conversation ──────────────────────────────────────────────

async def admin_add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("Нет доступа.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("Добавление задания\n\nШаг 1/4. Введи название задания:\n\n(Отправь /cancel для отмены)")
    return ADD_TITLE


async def add_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    context.user_data["new_task_title"] = update.message.text.strip()
    await update.message.reply_text("Шаг 2/4. Введи описание задания:")
    return ADD_DESC


async def add_task_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    context.user_data["new_task_desc"] = update.message.text.strip()
    await update.message.reply_text("Шаг 3/4. Введи ссылку для задания (URL):")
    return ADD_LINK


async def add_task_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    context.user_data["new_task_link"] = update.message.text.strip()
    default_reward = await get_setting("default_task_reward")
    await update.message.reply_text(f"Шаг 4/4. Введи награду в звёздах (например: {default_reward}):")
    return ADD_REWARD


async def add_task_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    try:
        reward = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("Некорректное число. Введи награду ещё раз (например: 0.35):")
        return ADD_REWARD

    title = context.user_data["new_task_title"]
    desc = context.user_data["new_task_desc"]
    link = context.user_data["new_task_link"]
    await add_task(title, desc, reward, link)
    context.user_data.clear()
    await update.message.reply_text("Задание добавлено!", reply_markup=admin_main_keyboard())
    return ConversationHandler.END


# ── Admin: Edit task conversation ─────────────────────────────────────────────

async def admin_edit_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("Нет доступа.", show_alert=True)
        return ConversationHandler.END
    await query.answer()

    parts = query.data.split("_")
    task_id = int(parts[2])
    field = "_".join(parts[3:])

    field_labels = {
        "title": "новое название задания",
        "description": "новое описание задания",
        "url": "новую ссылку (URL)",
        "reward_stars": "новую награду в звёздах (например: 0.60)",
    }
    label = field_labels.get(field, "новое значение")
    context.user_data["edit_task_id"] = task_id
    context.user_data["edit_task_field"] = field
    await query.edit_message_text(f"Введи {label}:\n\n(Отправь /cancel для отмены)")
    return EDIT_VALUE


async def edit_task_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    task_id = context.user_data.get("edit_task_id")
    field = context.user_data.get("edit_task_field")
    raw = update.message.text.strip()

    if field == "reward_stars":
        try:
            value = float(raw.replace(",", "."))
        except ValueError:
            await update.message.reply_text("Некорректное число. Введи число ещё раз (например: 0.60):")
            return EDIT_VALUE
        await update_task_field(task_id, field, value)
        await update.message.reply_text(
            f"Цена задания изменена на {value}⭐",
            reply_markup=admin_main_keyboard()
        )
    else:
        await update_task_field(task_id, field, raw)
        await update.message.reply_text("Изменение сохранено!", reply_markup=admin_main_keyboard())

    context.user_data.clear()
    return ConversationHandler.END


# ── Admin: Broadcast conversation ─────────────────────────────────────────────

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("Нет доступа.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await query.edit_message_text("Рассылка\n\nОтправь текст рассылки:\n\n(Отправь /cancel для отмены)")
    return BROADCAST_TEXT


async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    text = update.message.text.strip()
    user_ids = await get_all_user_ids()

    success = 0
    errors = 0
    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            success += 1
        except Exception:
            errors += 1

    await update.message.reply_text(
        f"Рассылка завершена\n\nУспешно: {success}\nОшибок: {errors}",
        reply_markup=admin_main_keyboard()
    )
    return ConversationHandler.END


# ── Admin: Settings conversation ──────────────────────────────────────────────

async def settings_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    key = context.user_data.get("settings_key")
    raw = update.message.text.strip()

    if key in ("referral_reward", "min_withdrawal", "default_task_reward"):
        try:
            float(raw.replace(",", "."))
        except ValueError:
            await update.message.reply_text("Некорректное число. Попробуй ещё раз:")
            return SETTINGS_VALUE
        raw = raw.replace(",", ".")

    await set_setting(key, raw)
    settings = await get_all_settings()
    context.user_data.clear()
    await update.message.reply_text(
        "Настройка сохранена!\n\nНастройки:",
        reply_markup=admin_settings_keyboard(settings)
    )
    return ConversationHandler.END


# ── Admin: Add sponsor conversation ───────────────────────────────────────────

async def admin_add_sponsor_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("Нет доступа.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text(
        "Добавление спонсора\n\nШаг 1/2. Введи название спонсора:\n\n(Отправь /cancel для отмены)"
    )
    return ADD_SPONSOR_NAME


async def add_sponsor_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    context.user_data["new_sponsor_name"] = update.message.text.strip()
    await update.message.reply_text(
        "Шаг 2/2. Введи username или ссылку на канал\n"
        "(например: @channelname или https://t.me/channelname):"
    )
    return ADD_SPONSOR_CHANNEL


async def add_sponsor_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    raw = update.message.text.strip()
    channel_username, channel_link = _normalize_channel(raw)
    name = context.user_data["new_sponsor_name"]

    await add_sponsor(name, channel_username, channel_link)
    context.user_data.clear()
    await update.message.reply_text(
        f"Спонсор добавлен!\n\n"
        f"Название: {name}\n"
        f"Канал: {channel_username}\n\n"
        f"Убедитесь, что бот является администратором этого канала.",
        reply_markup=admin_main_keyboard()
    )
    return ConversationHandler.END


# ── Shared cancel ─────────────────────────────────────────────────────────────

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Действие отменено.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# ── /stats command ────────────────────────────────────────────────────────────

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    s = await get_stats()
    text = (
        f"Статистика\n\n"
        f"Пользователей всего: {s['users_total']}\n"
        f"Новых сегодня: {s['users_today']}\n"
        f"Рефералов всего: {s['referrals_total']}\n"
        f"Заданий всего: {s['tasks_total']}\n"
        f"Заявок на вывод: {s['withdrawals_total']}\n"
        f"Активных спонсоров: {s['sponsors_total']}\n"
        f"Всего звёзд на балансах: {s['total_stars']:.2f}⭐"
    )
    await update.message.reply_text(text)


# ── /withdrawals command ──────────────────────────────────────────────────────

async def withdrawals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    rows = await get_all_withdrawals()
    if not rows:
        await update.message.reply_text("Заявок на вывод нет.")
        return
    lines = ["Последние заявки на вывод:\n"]
    for r in rows:
        name = f"@{r['username']}" if r["username"] else r["first_name"] or str(r["user_id"])
        icon = {"pending": "⏳", "sent": "✅", "rejected": "❌"}.get(r["status"], "❓")
        lines.append(f"{icon} {name} — {r['amount']}⭐ ({r['requested_at'][:10]})")
    await update.message.reply_text("\n".join(lines))
