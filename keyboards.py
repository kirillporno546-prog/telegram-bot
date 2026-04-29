from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

PAYOUT_CHANNEL_URL = "https://t.me/yabloko_gifts_channel"
PAYOUT_CHANNEL_NAME = "@yabloko_gifts_channel"


# ── User keyboards ────────────────────────────────────────────────────────────

def main_menu_keyboard():
    keyboard = [
        [KeyboardButton("⭐ Баланс"), KeyboardButton("⭐ Заработать звёзды")],
        [KeyboardButton("📋 Задания"), KeyboardButton("👥 Пригласить друзей")],
        [KeyboardButton("💸 Вывод")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


def sponsors_keyboard(sponsors: list):
    buttons = []
    for sponsor in sponsors:
        buttons.append([InlineKeyboardButton(sponsor["name"], url=sponsor["channel_link"])])
    buttons.append([InlineKeyboardButton("✅ Проверить подписку", callback_data="check_sponsors")])
    return InlineKeyboardMarkup(buttons)


def tasks_inline_keyboard(tasks, completed_ids):
    buttons = []
    for task in tasks:
        status = "✅" if task["id"] in completed_ids else "🔵"
        label = f"{status} {task['title']} (+{task['reward_stars']}⭐)"
        buttons.append([InlineKeyboardButton(label, callback_data=f"task_{task['id']}")])
    return InlineKeyboardMarkup(buttons)


def task_detail_keyboard(task, is_done: bool):
    buttons = []
    if task.get("url"):
        buttons.append([InlineKeyboardButton("🔗 Перейти к заданию", url=task["url"])])
    if not is_done:
        buttons.append([InlineKeyboardButton("✅ Отметить как выполненное", callback_data=f"done_{task['id']}")])
    else:
        buttons.append([InlineKeyboardButton("✅ Уже выполнено", callback_data="noop")])
    buttons.append([InlineKeyboardButton("⬅️ Назад к заданиям", callback_data="back_tasks")])
    return InlineKeyboardMarkup(buttons)


def withdraw_options_keyboard(options=(15, 25, 50, 100)):
    buttons = [
        [
            InlineKeyboardButton(f"{options[0]}⭐", callback_data=f"withdraw_{options[0]}"),
            InlineKeyboardButton(f"{options[1]}⭐", callback_data=f"withdraw_{options[1]}"),
        ],
        [
            InlineKeyboardButton(f"{options[2]}⭐", callback_data=f"withdraw_{options[2]}"),
            InlineKeyboardButton(f"{options[3]}⭐", callback_data=f"withdraw_{options[3]}"),
        ],
        [InlineKeyboardButton("📢 Канал выплат", url=PAYOUT_CHANNEL_URL)],
    ]
    return InlineKeyboardMarkup(buttons)


def withdraw_confirm_keyboard(amount: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Подтвердить вывод {amount}⭐", callback_data=f"confirm_withdraw_{amount}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_withdraw")],
    ])


# ── Channel withdrawal keyboard ───────────────────────────────────────────────

def withdrawal_channel_keyboard(withdrawal_id: int, is_sent: bool = False):
    if is_sent:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Отправлено", callback_data=f"wd_sent_{withdrawal_id}")],
        ])
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏳ Ожидает отправки", callback_data=f"wd_pending_{withdrawal_id}"),
            InlineKeyboardButton("✅ Отправлено", callback_data=f"wd_sent_{withdrawal_id}"),
        ],
    ])


# ── Admin keyboards ───────────────────────────────────────────────────────────

def admin_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить задание", callback_data="admin_add_task")],
        [InlineKeyboardButton("📋 Управление заданиями", callback_data="admin_tasks")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="admin_settings")],
        [InlineKeyboardButton("➕ Добавить спонсора", callback_data="admin_add_sponsor")],
        [InlineKeyboardButton("📋 Все спонсоры", callback_data="admin_sponsors")],
    ])


def admin_task_list_keyboard(tasks):
    buttons = []
    for task in tasks:
        status = "✅" if task["is_active"] else "❌"
        buttons.append([InlineKeyboardButton(
            f"{status} [{task['id']}] {task['title'][:30]}",
            callback_data=f"adm_task_{task['id']}"
        )])
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_back")])
    return InlineKeyboardMarkup(buttons)


def admin_task_detail_keyboard(task):
    status_label = "🔴 Отключить" if task["is_active"] else "🟢 Включить"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Изменить название", callback_data=f"adm_edit_{task['id']}_title")],
        [InlineKeyboardButton("📝 Изменить описание", callback_data=f"adm_edit_{task['id']}_description")],
        [InlineKeyboardButton("🔗 Изменить ссылку", callback_data=f"adm_edit_{task['id']}_url")],
        [InlineKeyboardButton("⭐ Изменить цену", callback_data=f"adm_edit_{task['id']}_reward_stars")],
        [InlineKeyboardButton(status_label, callback_data=f"adm_toggle_{task['id']}")],
        [InlineKeyboardButton("🗑 Удалить задание", callback_data=f"adm_delete_{task['id']}")],
        [InlineKeyboardButton("⬅️ Назад к заданиям", callback_data="admin_tasks")],
    ])


def admin_task_delete_confirm_keyboard(task_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, удалить", callback_data=f"adm_delete_confirm_{task_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"adm_task_{task_id}")],
    ])


def admin_sponsors_list_keyboard(sponsors: list):
    buttons = []
    for sponsor in sponsors:
        status = "✅" if sponsor["is_active"] else "❌"
        buttons.append([InlineKeyboardButton(
            f"{status} {sponsor['name']} ({sponsor['channel_username']})",
            callback_data=f"adm_sponsor_toggle_{sponsor['id']}"
        )])
        buttons.append([InlineKeyboardButton(
            f"🗑 Удалить {sponsor['name']}",
            callback_data=f"adm_sponsor_del_{sponsor['id']}"
        )])
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_back")])
    return InlineKeyboardMarkup(buttons)


def admin_settings_keyboard(settings: dict):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"Реферал: {settings.get('referral_reward', '3.5')}⭐",
            callback_data="adm_set_referral_reward"
        )],
        [InlineKeyboardButton(
            f"Мин. вывод: {settings.get('min_withdrawal', '15')}⭐",
            callback_data="adm_set_min_withdrawal"
        )],
        [InlineKeyboardButton(
            f"Награда за задание: {settings.get('default_task_reward', '0.35')}⭐",
            callback_data="adm_set_default_task_reward"
        )],
        [InlineKeyboardButton(
            f"Канал выплат: {settings.get('payout_channel', PAYOUT_CHANNEL_NAME)}",
            callback_data="adm_set_payout_channel"
        )],
        [InlineKeyboardButton("⬅️ Назад", callback_data="admin_back")],
    ])
