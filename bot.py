import os
import sqlite3
import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ===== НАСТРОЙКИ =====
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

ADMIN_ID = 6820965428
WITHDRAW_CHANNEL = "@yabloko_gifts_channel"

DB_PATH = "bot_data.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# ===== БАЗА =====
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()


def init_db():
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance REAL DEFAULT 0,
        invited_by INTEGER,
        referral_reward_given INTEGER DEFAULT 0,
        tasks_completed INTEGER DEFAULT 0,
        last_bonus TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        link TEXT,
        reward REAL DEFAULT 0.35,
        active INTEGER DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS completed_tasks (
        user_id INTEGER,
        task_id INTEGER,
        created_at TEXT,
        PRIMARY KEY (user_id, task_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sponsors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        link TEXT,
        active INTEGER DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sponsor_checks (
        user_id INTEGER,
        sponsor_id INTEGER,
        subscribed INTEGER DEFAULT 0,
        penalty_given INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, sponsor_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        amount REAL,
        status TEXT DEFAULT 'pending',
        created_at TEXT,
        channel_message_id INTEGER
    )
    """)

    set_default("daily_bonus_reward", "0.10")
    set_default("referral_reward", "3.5")
    set_default("task_reward", "0.35")
    conn.commit()


def set_default(key: str, value: str):
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))


def get_setting(key: str, default: str = "0"):
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    if row:
        return row[0]
    set_default(key, default)
    conn.commit()
    return default


def set_setting(key: str, value: str):
    cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def get_or_create_user(user_id: int, username: str | None = None, invited_by: int | None = None):
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    exists = cur.fetchone()

    if not exists:
        cur.execute(
            """
            INSERT INTO users (user_id, username, invited_by, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, username, invited_by, datetime.utcnow().isoformat()),
        )
        conn.commit()
        return True

    cur.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
    conn.commit()
    return False


def add_balance(user_id: int, amount: float):
    cur.execute(
        "UPDATE users SET balance = MAX(balance + ?, 0) WHERE user_id=?",
        (amount, user_id),
    )
    conn.commit()


def get_balance(user_id: int) -> float:
    cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    return float(row[0]) if row else 0.0


# ===== КЛАВИАТУРЫ =====
def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["⭐ Баланс", "⭐ Заработать звёзды"],
            ["📋 Задания", "👥 Пригласить друзей"],
            ["💸 Вывод", "🎁 Бонус дня"],
        ],
        resize_keyboard=True,
    )


def admin_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Добавить задание", callback_data="admin_add_task")],
            [InlineKeyboardButton("📋 Управление заданиями", callback_data="admin_tasks")],
            [InlineKeyboardButton("➕ Добавить спонсора", callback_data="admin_add_sponsor")],
            [InlineKeyboardButton("📋 Управление спонсорами", callback_data="admin_sponsors")],
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("🎁 Бонус дня", callback_data="admin_set_bonus")],
            [InlineKeyboardButton("💰 Цена реферала", callback_data="admin_set_ref")],
        ]
    )


# ===== ВСПОМОГАТЕЛЬНОЕ =====
def parse_channel_from_link(link: str) -> str | None:
    link = link.strip()

    if link.startswith("@"):
        return link

    if "t.me/" in link or "telegram.me/" in link:
        parsed = urlparse(link if link.startswith("http") else "https://" + link)
        path = parsed.path.strip("/")
        if path and not path.startswith("+") and not path.startswith("joinchat"):
            channel = path.split("/")[0]
            return "@" + channel

    return None


async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int, channel: str) -> bool | None:
    try:
        member = await context.bot.get_chat_member(channel, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return None


async def check_sponsors(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id

    cur.execute("SELECT id, title, link FROM sponsors WHERE active=1")
    sponsors = cur.fetchall()

    if not sponsors:
        return True

    not_joined = []

    for sponsor_id, title, link in sponsors:
        channel = parse_channel_from_link(link)
        subscribed = True

        if channel:
            result = await is_member(context, user_id, channel)
            subscribed = bool(result)

        cur.execute(
            "SELECT subscribed, penalty_given FROM sponsor_checks WHERE user_id=? AND sponsor_id=?",
            (user_id, sponsor_id),
        )
        old = cur.fetchone()

        if old and old[0] == 1 and not subscribed and old[1] == 0:
            add_balance(user_id, -0.50)
            cur.execute(
                """
                INSERT OR REPLACE INTO sponsor_checks
                (user_id, sponsor_id, subscribed, penalty_given)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, sponsor_id, 0, 1),
            )
            conn.commit()
            await update.effective_message.reply_text(
                "⚠️ Вы отписались от спонсора.\nС баланса списано 0.50⭐"
            )

        if subscribed:
            cur.execute(
                """
                INSERT OR REPLACE INTO sponsor_checks
                (user_id, sponsor_id, subscribed, penalty_given)
                VALUES (?, ?, ?, COALESCE((SELECT penalty_given FROM sponsor_checks WHERE user_id=? AND sponsor_id=?), 0))
                """,
                (user_id, sponsor_id, 1, user_id, sponsor_id),
            )
            conn.commit()
        else:
            not_joined.append((title, link))

    if not_joined:
        buttons = []
        for title, link in not_joined:
            buttons.append([InlineKeyboardButton(title, url=link)])
        buttons.append([InlineKeyboardButton("✅ Проверить подписку", callback_data="check_sponsors")])

        await update.effective_message.reply_text(
            "Для доступа к боту подпишитесь на спонсоров:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return False

    return True


async def give_referral_reward_if_needed(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    cur.execute(
        "SELECT invited_by, tasks_completed, referral_reward_given FROM users WHERE user_id=?",
        (user_id,),
    )
    row = cur.fetchone()

    if not row:
        return

    invited_by, tasks_completed, reward_given = row

    if invited_by and tasks_completed >= 3 and reward_given == 0:
        reward = float(get_setting("referral_reward", "3.5"))
        add_balance(invited_by, reward)

        cur.execute("UPDATE users SET referral_reward_given=1 WHERE user_id=?", (user_id,))
        conn.commit()

        try:
            await context.bot.send_message(
                invited_by,
                f"🎉 Ваш реферал выполнил 3 задания!\n⭐ Начислено: +{reward}⭐",
            )
        except Exception:
            pass


# ===== ПОЛЬЗОВАТЕЛЬ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    invited_by = None

    if context.args:
        try:
            invited_by = int(context.args[0])
            if invited_by == user.id:
                invited_by = None
        except Exception:
            invited_by = None

    is_new = get_or_create_user(user.id, user.username, invited_by)

    if is_new and invited_by:
        reward = float(get_setting("referral_reward", "3.5"))
        try:
            await context.bot.send_message(
                invited_by,
                f"👥 У вас новый реферал!\nВы получите {reward}⭐ после того, как он выполнит 3 любых задания.",
            )
        except Exception:
            pass

    allowed = await check_sponsors(update, context)
    if not allowed:
        return

    await update.message.reply_text(
        "Главное меню",
        reply_markup=main_keyboard(),
    )


async def check_sponsors_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    allowed = await check_sponsors(update, context)

    if allowed:
        await query.message.reply_text("✅ Подписка проверена. Главное меню открыто.", reply_markup=main_keyboard())


async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_or_create_user(update.effective_user.id, update.effective_user.username)
    bal = get_balance(update.effective_user.id)
    await update.effective_message.reply_text(f"⭐ Ваш баланс: {bal:.2f}⭐")


async def daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_or_create_user(update.effective_user.id, update.effective_user.username)

    user_id = update.effective_user.id
    cur.execute("SELECT last_bonus FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    last_bonus = row[0] if row else None

    now = datetime.utcnow()

    if last_bonus:
        last_time = datetime.fromisoformat(last_bonus)
        next_time = last_time + timedelta(hours=24)

        if now < next_time:
            remaining = next_time - now
            hours = remaining.seconds // 3600
            minutes = (remaining.seconds % 3600) // 60

            await update.effective_message.reply_text(
                f"Вы уже получили бонус дня\nСледующий бонус через: {hours}ч {minutes}м"
            )
            return

    reward = float(get_setting("daily_bonus_reward", "0.10"))
    add_balance(user_id, reward)

    cur.execute("UPDATE users SET last_bonus=? WHERE user_id=?", (now.isoformat(), user_id))
    conn.commit()

    await update.effective_message.reply_text(f"🎁 Бонус дня получен: +{reward}⭐")


async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_or_create_user(update.effective_user.id, update.effective_user.username)

    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={update.effective_user.id}"
    reward = float(get_setting("referral_reward", "3.5"))

    await update.effective_message.reply_text(
        f"👥 Приглашайте друзей:\n{link}\n\nЗа активного реферала: {reward}⭐\nНаграда начисляется после 3 заданий."
    )


async def tasks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_or_create_user(update.effective_user.id, update.effective_user.username)

    cur.execute("SELECT id, title, link, reward FROM tasks WHERE active=1")
    tasks = cur.fetchall()

    if not tasks:
        await update.effective_message.reply_text("Пока нет доступных заданий.")
        return

    for task_id, title, link, reward in tasks:
        buttons = [
            [InlineKeyboardButton("🔗 Перейти", url=link)],
            [InlineKeyboardButton("✅ Проверить", callback_data=f"check_task:{task_id}")],
        ]

        await update.effective_message.reply_text(
            f"📋 Задание: {title}\n⭐ Награда: {reward}⭐",
            reply_markup=InlineKeyboardMarkup(buttons),
        )


async def check_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    get_or_create_user(user_id, query.from_user.username)

    task_id = int(query.data.split(":")[1])

    cur.execute("SELECT title, link, reward FROM tasks WHERE id=? AND active=1", (task_id,))
    task = cur.fetchone()

    if not task:
        await query.message.reply_text("Задание не найдено.")
        return

    title, link, reward = task

    cur.execute("SELECT 1 FROM completed_tasks WHERE user_id=? AND task_id=?", (user_id, task_id))
    if cur.fetchone():
        await query.message.reply_text("Вы уже выполнили это задание.")
        return

    channel = parse_channel_from_link(link)

    if channel:
        result = await is_member(context, user_id, channel)

        if result is False:
            await query.message.reply_text(
                "❌ Вы не подписались на канал.\nСначала подпишитесь, потом нажмите проверить."
            )
            return

        if result is None:
            await query.message.reply_text(
                "Не удалось проверить подписку. Убедитесь, что бот является админом канала."
            )
            return

    cur.execute(
        "INSERT INTO completed_tasks (user_id, task_id, created_at) VALUES (?, ?, ?)",
        (user_id, task_id, datetime.utcnow().isoformat()),
    )
    cur.execute(
        "UPDATE users SET balance = balance + ?, tasks_completed = tasks_completed + 1 WHERE user_id=?",
        (float(reward), user_id),
    )
    conn.commit()

    await query.message.reply_text(f"✅ Задание выполнено!\n⭐ Начислено: +{reward}⭐")

    await give_referral_reward_if_needed(context, user_id)


# ===== ВЫВОД =====
WAIT_WITHDRAW_AMOUNT = 1


async def withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_or_create_user(update.effective_user.id, update.effective_user.username)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("15⭐", callback_data="withdraw_amount:15"),
                InlineKeyboardButton("25⭐", callback_data="withdraw_amount:25"),
            ],
            [
                InlineKeyboardButton("50⭐", callback_data="withdraw_amount:50"),
                InlineKeyboardButton("100⭐", callback_data="withdraw_amount:100"),
            ],
        ]
    )

    await update.effective_message.reply_text("Выберите сумму для вывода:", reply_markup=keyboard)
    return ConversationHandler.END


async def create_withdrawal_request(user, context: ContextTypes.DEFAULT_TYPE, amount: float, reply_target):
    get_or_create_user(user.id, user.username)

    if amount not in (15, 25, 50, 100):
        await reply_target.reply_text("❌ Выберите сумму кнопкой: 15, 25, 50 или 100⭐")
        return

    balance = get_balance(user.id)

    if balance < amount:
        await reply_target.reply_text("❌ Недостаточно звёзд для вывода")
        return

    add_balance(user.id, -amount)

    cur.execute(
        """
        INSERT INTO withdrawals (user_id, username, amount, status, created_at)
        VALUES (?, ?, ?, 'pending', ?)
        """,
        (user.id, user.username or "", amount, datetime.utcnow().isoformat()),
    )
    withdrawal_id = cur.lastrowid
    conn.commit()

    username = f"@{user.username}" if user.username else "без username"

    text = (
        "💸 Новая заявка на вывод\n\n"
        f"👤 Пользователь: {username}\n"
        f"🆔 ID: {user.id}\n"
        f"⭐ Сумма: {amount}⭐\n\n"
        "Статус: ⏳ Ожидает отправки"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Отправлено", callback_data=f"withdraw_sent:{withdrawal_id}"),
                InlineKeyboardButton("❌ Отклонить и вернуть", callback_data=f"withdraw_refund:{withdrawal_id}"),
            ]
        ]
    )

    msg = await context.bot.send_message(WITHDRAW_CHANNEL, text, reply_markup=keyboard)

    cur.execute(
        "UPDATE withdrawals SET channel_message_id=? WHERE id=?",
        (msg.message_id, withdrawal_id),
    )
    conn.commit()

    await reply_target.reply_text(f"✅ Заявка на вывод {amount}⭐ отправлена.")


async def withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Выберите сумму вывода кнопкой.")
    return ConversationHandler.END


async def withdraw_amount_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    amount = float(query.data.split(":")[1])
    await create_withdrawal_request(query.from_user, context, amount, query.message)


async def withdraw_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа", show_alert=True)
        return

    action, wid = query.data.split(":")
    wid = int(wid)

    cur.execute("SELECT user_id, username, amount, status FROM withdrawals WHERE id=?", (wid,))
    row = cur.fetchone()

    if not row:
        await query.message.reply_text("Заявка не найдена.")
        return

    user_id, username, amount, status = row

    if status != "pending":
        await query.answer("Заявка уже обработана", show_alert=True)
        return

    username_text = f"@{username}" if username else "без username"

    if action == "withdraw_sent":
        cur.execute("UPDATE withdrawals SET status='sent' WHERE id=?", (wid,))
        conn.commit()

        text = (
            "✅ Выплата отправлена\n\n"
            f"👤 Пользователь: {username_text}\n"
            f"🆔 ID: {user_id}\n"
            f"⭐ Сумма: {amount}⭐\n\n"
            "Статус: ✅ Отправлено"
        )

        await query.message.edit_text(text)
        await context.bot.send_message(user_id, "✅ Ваша выплата отправлена!")

    elif action == "withdraw_refund":
        add_balance(user_id, float(amount))
        cur.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,))
        conn.commit()

        text = (
            "❌ Заявка отклонена\n\n"
            f"👤 Пользователь: {username_text}\n"
            f"🆔 ID: {user_id}\n"
            f"⭐ Сумма: {amount}⭐\n\n"
            "Статус: ❌ Отклонено, звёзды возвращены"
        )

        await query.message.edit_text(text)
        await context.bot.send_message(
            user_id,
            "❌ Ваша заявка отклонена.\n⭐ Звёзды возвращены на баланс.",
        )


# ===== АДМИНКА =====
WAIT_TASK_TITLE, WAIT_TASK_LINK, WAIT_TASK_REWARD = range(10, 13)
WAIT_SPONSOR_TITLE, WAIT_SPONSOR_LINK = range(13, 15)
WAIT_SET_BONUS, WAIT_SET_REF, WAIT_EDIT_TASK_PRICE, WAIT_BROADCAST_PREVIEW = range(15, 19)


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Нет доступа")
        return

    await update.effective_message.reply_text("Админка", reply_markup=admin_keyboard())


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.answer("❌ Нет доступа", show_alert=True)
        return ConversationHandler.END

    data = query.data

    if data == "admin_stats":
        cur.execute("SELECT COUNT(*) FROM users")
        users_total = cur.fetchone()[0]

        today = datetime.utcnow().date().isoformat()
        cur.execute("SELECT COUNT(*) FROM users WHERE created_at LIKE ?", (today + "%",))
        users_today = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM users WHERE invited_by IS NOT NULL")
        refs_total = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM tasks")
        tasks_total = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM withdrawals")
        withdrawals_total = cur.fetchone()[0]

        cur.execute("SELECT COALESCE(SUM(balance), 0) FROM users")
        total_balance = cur.fetchone()[0]

        await query.message.reply_text(
            "📊 Статистика\n\n"
            f"👥 Пользователей всего: {users_total}\n"
            f"🔥 Новых сегодня: {users_today}\n"
            f"👥 Рефералов всего: {refs_total}\n"
            f"📋 Заданий всего: {tasks_total}\n"
            f"💸 Заявок на вывод: {withdrawals_total}\n"
            f"⭐ Всего звёзд на балансах: {total_balance:.2f}"
        )
        return ConversationHandler.END

    if data == "admin_broadcast":
        await query.message.reply_text(
            "📢 Отправьте сообщение для рассылки.\n\nМожно текст, фото или пост с оформлением."
        )
        return WAIT_BROADCAST_PREVIEW

    if data == "admin_add_task":
        await query.message.reply_text("Введите название задания:")
        return WAIT_TASK_TITLE

    if data == "admin_add_sponsor":
        await query.message.reply_text("Введите название спонсора:")
        return WAIT_SPONSOR_TITLE

    if data == "admin_tasks":
        await show_admin_tasks(query.message)
        return ConversationHandler.END

    if data == "admin_sponsors":
        await show_admin_sponsors(query.message)
        return ConversationHandler.END

    if data.startswith("task_manage:"):
        task_id = int(data.split(":")[1])
        await show_one_task(query.message, task_id)
        return ConversationHandler.END

    if data.startswith("task_delete:"):
        task_id = int(data.split(":")[1])
        cur.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        cur.execute("DELETE FROM completed_tasks WHERE task_id=?", (task_id,))
        conn.commit()
        await query.message.reply_text("🗑 Задание удалено.")
        await show_admin_tasks(query.message)
        return ConversationHandler.END

    if data.startswith("task_toggle:"):
        task_id = int(data.split(":")[1])
        cur.execute("SELECT active FROM tasks WHERE id=?", (task_id,))
        row = cur.fetchone()
        if not row:
            await query.message.reply_text("Задание не найдено.")
            return ConversationHandler.END
        new_status = 0 if row[0] == 1 else 1
        cur.execute("UPDATE tasks SET active=? WHERE id=?", (new_status, task_id))
        conn.commit()
        await query.message.reply_text("✅ Статус задания изменён.")
        await show_one_task(query.message, task_id)
        return ConversationHandler.END

    if data.startswith("task_price:"):
        task_id = int(data.split(":")[1])
        context.user_data["edit_task_id"] = task_id
        await query.message.reply_text("Введите новую цену задания:")
        return WAIT_EDIT_TASK_PRICE

    if data.startswith("sponsor_manage:"):
        sponsor_id = int(data.split(":")[1])
        await show_one_sponsor(query.message, sponsor_id)
        return ConversationHandler.END

    if data.startswith("sponsor_delete:"):
        sponsor_id = int(data.split(":")[1])
        cur.execute("DELETE FROM sponsors WHERE id=?", (sponsor_id,))
        cur.execute("DELETE FROM sponsor_checks WHERE sponsor_id=?", (sponsor_id,))
        conn.commit()
        await query.message.reply_text("🗑 Спонсор удалён.")
        await show_admin_sponsors(query.message)
        return ConversationHandler.END

    if data.startswith("sponsor_toggle:"):
        sponsor_id = int(data.split(":")[1])
        cur.execute("SELECT active FROM sponsors WHERE id=?", (sponsor_id,))
        row = cur.fetchone()
        if not row:
            await query.message.reply_text("Спонсор не найден.")
            return ConversationHandler.END
        new_status = 0 if row[0] == 1 else 1
        cur.execute("UPDATE sponsors SET active=? WHERE id=?", (new_status, sponsor_id))
        conn.commit()
        await query.message.reply_text("✅ Статус спонсора изменён.")
        await show_one_sponsor(query.message, sponsor_id)
        return ConversationHandler.END

    if data == "admin_set_bonus":
        await query.message.reply_text("Введите новую сумму бонуса дня:")
        return WAIT_SET_BONUS

    if data == "admin_set_ref":
        await query.message.reply_text("Введите новую цену за реферала:")
        return WAIT_SET_REF

    return ConversationHandler.END



async def show_admin_tasks(message):
    cur.execute("SELECT id, title, reward, active FROM tasks ORDER BY id DESC")
    rows = cur.fetchall()

    if not rows:
        await message.reply_text("Заданий нет.")
        return

    buttons = []
    for tid, title, reward, active in rows:
        status = "🟢" if active else "🔴"
        buttons.append([InlineKeyboardButton(f"{status} #{tid} {title} — {reward}⭐", callback_data=f"task_manage:{tid}")])

    await message.reply_text("📋 Управление заданиями:", reply_markup=InlineKeyboardMarkup(buttons))


async def show_one_task(message, task_id: int):
    cur.execute("SELECT id, title, link, reward, active FROM tasks WHERE id=?", (task_id,))
    row = cur.fetchone()

    if not row:
        await message.reply_text("Задание не найдено.")
        return

    tid, title, link, reward, active = row
    status = "🟢 Включено" if active else "🔴 Выключено"
    toggle_text = "🔴 Выключить" if active else "🟢 Включить"

    text = (
        f"📋 Задание #{tid}\\n\\n"
        f"Название: {title}\\n"
        f"Ссылка: {link}\\n"
        f"Цена: {reward}⭐\\n"
        f"Статус: {status}"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💰 Изменить цену", callback_data=f"task_price:{tid}")],
            [InlineKeyboardButton(toggle_text, callback_data=f"task_toggle:{tid}")],
            [InlineKeyboardButton("🗑 Удалить", callback_data=f"task_delete:{tid}")],
            [InlineKeyboardButton("⬅️ Назад к заданиям", callback_data="admin_tasks")],
        ]
    )

    await message.reply_text(text, reply_markup=keyboard)


async def show_admin_sponsors(message):
    cur.execute("SELECT id, title, link, active FROM sponsors ORDER BY id DESC")
    rows = cur.fetchall()

    if not rows:
        await message.reply_text("Спонсоров нет.")
        return

    buttons = []
    for sid, title, link, active in rows:
        status = "🟢" if active else "🔴"
        buttons.append([InlineKeyboardButton(f"{status} #{sid} {title}", callback_data=f"sponsor_manage:{sid}")])

    await message.reply_text("📋 Управление спонсорами:", reply_markup=InlineKeyboardMarkup(buttons))


async def show_one_sponsor(message, sponsor_id: int):
    cur.execute("SELECT id, title, link, active FROM sponsors WHERE id=?", (sponsor_id,))
    row = cur.fetchone()

    if not row:
        await message.reply_text("Спонсор не найден.")
        return

    sid, title, link, active = row
    status = "🟢 Включен" if active else "🔴 Выключен"
    toggle_text = "🔴 Выключить" if active else "🟢 Включить"

    text = (
        f"📢 Спонсор #{sid}\\n\\n"
        f"Название: {title}\\n"
        f"Ссылка: {link}\\n"
        f"Статус: {status}"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(toggle_text, callback_data=f"sponsor_toggle:{sid}")],
            [InlineKeyboardButton("🗑 Удалить", callback_data=f"sponsor_delete:{sid}")],
            [InlineKeyboardButton("⬅️ Назад к спонсорам", callback_data="admin_sponsors")],
        ]
    )

    await message.reply_text(text, reply_markup=keyboard)


async def edit_task_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = float(update.message.text.replace(",", "."))
    except Exception:
        await update.message.reply_text("Введите число.")
        return WAIT_EDIT_TASK_PRICE

    task_id = context.user_data.get("edit_task_id")
    if not task_id:
        await update.message.reply_text("Задание не найдено.")
        return ConversationHandler.END

    cur.execute("UPDATE tasks SET reward=? WHERE id=?", (value, task_id))
    conn.commit()

    await update.message.reply_text(f"✅ Цена задания обновлена: {value}⭐")
    return ConversationHandler.END



async def add_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["task_title"] = update.message.text
    await update.message.reply_text("Введите ссылку задания:")
    return WAIT_TASK_LINK


async def add_task_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["task_link"] = update.message.text
    await update.message.reply_text("Введите награду за задание:")
    return WAIT_TASK_REWARD


async def add_task_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        reward = float(update.message.text.replace(",", "."))
    except Exception:
        await update.message.reply_text("Введите число.")
        return WAIT_TASK_REWARD

    cur.execute(
        "INSERT INTO tasks (title, link, reward, active) VALUES (?, ?, ?, 1)",
        (context.user_data["task_title"], context.user_data["task_link"], reward),
    )
    conn.commit()

    await update.message.reply_text("✅ Задание добавлено.")
    return ConversationHandler.END


async def add_sponsor_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["sponsor_title"] = update.message.text
    await update.message.reply_text("Введите ссылку спонсора:")
    return WAIT_SPONSOR_LINK


async def add_sponsor_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute(
        "INSERT INTO sponsors (title, link, active) VALUES (?, ?, 1)",
        (context.user_data["sponsor_title"], update.message.text),
    )
    conn.commit()

    await update.message.reply_text("✅ Спонсор добавлен.")
    return ConversationHandler.END


async def set_bonus_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = float(update.message.text.replace(",", "."))
    except Exception:
        await update.message.reply_text("Введите число.")
        return WAIT_SET_BONUS

    set_setting("daily_bonus_reward", str(value))
    await update.message.reply_text(f"✅ Бонус дня обновлён: {value}⭐")
    return ConversationHandler.END


async def set_ref_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = float(update.message.text.replace(",", "."))
    except Exception:
        await update.message.reply_text("Введите число.")
        return WAIT_SET_REF

    set_setting("referral_reward", str(value))
    await update.message.reply_text(f"✅ Цена реферала обновлена: {value}⭐")
    return ConversationHandler.END


async def admin_setbonus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет доступа")
        return

    if not context.args:
        await update.message.reply_text("Пример: /setbonus 0.10")
        return

    set_setting("daily_bonus_reward", context.args[0])
    await update.message.reply_text(f"✅ Бонус дня обновлён: {context.args[0]}⭐")


async def admin_setref_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет доступа")
        return

    if not context.args:
        await update.message.reply_text("Пример: /setref 3.5")
        return

    set_setting("referral_reward", context.args[0])
    await update.message.reply_text(f"✅ Цена реферала обновлена: {context.args[0]}⭐")


async def user_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "⭐ Баланс":
        await balance_cmd(update, context)
    elif text == "🎁 Бонус дня":
        await daily_bonus(update, context)
    elif text in ("📋 Задания", "⭐ Заработать звёзды"):
        await tasks_menu(update, context)
    elif text == "👥 Пригласить друзей":
        await referral(update, context)
    elif text == "💸 Вывод":
        await withdraw_start(update, context)
    else:
        await update.message.reply_text("Выберите действие из меню.", reply_markup=main_keyboard())



async def broadcast_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    context.user_data["broadcast_chat_id"] = update.effective_chat.id
    context.user_data["broadcast_message_id"] = update.effective_message.message_id

    await update.effective_message.reply_text("👀 Предпросмотр рассылки:")

    await context.bot.copy_message(
        chat_id=update.effective_chat.id,
        from_chat_id=update.effective_chat.id,
        message_id=update.effective_message.message_id,
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Отправить всем", callback_data="broadcast_confirm"),
                InlineKeyboardButton("❌ Отмена", callback_data="broadcast_cancel"),
            ]
        ]
    )

    await update.effective_message.reply_text(
        "Отправить это сообщение всем пользователям?",
        reply_markup=keyboard,
    )

    return ConversationHandler.END


async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ Нет доступа", show_alert=True)
        return

    if query.data == "broadcast_cancel":
        context.user_data.pop("broadcast_chat_id", None)
        context.user_data.pop("broadcast_message_id", None)
        await query.message.edit_text("❌ Рассылка отменена.")
        return

    chat_id = context.user_data.get("broadcast_chat_id")
    message_id = context.user_data.get("broadcast_message_id")

    if not chat_id or not message_id:
        await query.message.edit_text("❌ Сообщение для рассылки не найдено.")
        return

    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()

    sent = 0
    failed = 0

    for (user_id,) in users:
        try:
            await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=chat_id,
                message_id=message_id,
            )
            sent += 1
        except Exception:
            failed += 1

    context.user_data.pop("broadcast_chat_id", None)
    context.user_data.pop("broadcast_message_id", None)

    await query.message.edit_text(
        f"✅ Рассылка завершена\n\nОтправлено: {sent}\nОшибок: {failed}"
    )


def main():
    if not TOKEN:
        raise ValueError("Переменная TELEGRAM_BOT_TOKEN не задана")

    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    admin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^admin_")],
        states={
            WAIT_TASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_title)],
            WAIT_TASK_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_link)],
            WAIT_TASK_REWARD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_reward)],
            WAIT_SPONSOR_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_sponsor_title)],
            WAIT_SPONSOR_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_sponsor_link)],
            WAIT_SET_BONUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_bonus_value)],
            WAIT_SET_REF: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_ref_value)],
            WAIT_EDIT_TASK_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_task_price)],
            WAIT_BROADCAST_PREVIEW: [MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_preview)],
        },
        fallbacks=[],
    )

    withdraw_conv = ConversationHandler(
        entry_points=[
            CommandHandler("withdraw", withdraw_start),
        ],
        states={
            WAIT_WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount)]
        },
        fallbacks=[],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("bonus", daily_bonus))
    app.add_handler(CommandHandler("tasks", tasks_menu))
    app.add_handler(CommandHandler("ref", referral))
    app.add_handler(CommandHandler("setbonus", admin_setbonus_command))
    app.add_handler(CommandHandler("setref", admin_setref_command))

    app.add_handler(admin_conv)
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(task_|sponsor_)"))
    app.add_handler(withdraw_conv)

    app.add_handler(CallbackQueryHandler(broadcast_callback, pattern="^broadcast_"))
    app.add_handler(CallbackQueryHandler(check_sponsors_callback, pattern="^check_sponsors$"))
    app.add_handler(CallbackQueryHandler(check_task_callback, pattern="^check_task:"))
    app.add_handler(CallbackQueryHandler(withdraw_amount_callback, pattern="^withdraw_amount:"))
    app.add_handler(CallbackQueryHandler(withdraw_callback, pattern="^withdraw_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user_text))

    print("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
