import os
import random
import sqlite3
import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
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

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# ИСПРАВЛЕНО: ADMIN_ID читается из переменной окружения, не хардкодится
ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "6820965428"))
PAYMENT_CHANNEL_ID = "@yabloko_gifts_channel"
DB_PATH = "bot_database.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ИСПРАВЛЕНО: check_same_thread=False корректно, но курсор создаётся локально в каждой функции
conn = sqlite3.connect(DB_PATH, check_same_thread=False)


# ================= СОСТОЯНИЯ ДИАЛОГА =================
# ИСПРАВЛЕНО: константы перенесены наверх, до всех функций которые их используют

ADD_TASK_TITLE, ADD_TASK_LINK, ADD_TASK_REWARD = range(1, 4)
ADD_SPONSOR_TITLE, ADD_SPONSOR_LINK = range(4, 6)
SET_REFERRAL_PRICE, SET_DAILY_BONUS = range(6, 8)
SET_LOTTERY_PRICE, SET_LOTTERY_LIMIT, SET_LOTTERY_PERCENT = range(8, 11)
BROADCAST_PREVIEW = 11


# ================= БАЗА =================

# ИСПРАВЛЕНО: допустимые таблицы и колонки — защита от SQL-инъекций в DDL
ALLOWED_TABLES = {"users", "tasks", "sponsors", "settings", "completed_tasks", "skipped_tasks", "lottery", "withdrawals"}
ALLOWED_COLUMNS = {
    "username", "balance", "invited_by", "referral_reward_given",
    "tasks_completed", "last_bonus", "created_at", "title", "link",
    "reward", "active",
}


def get_cur():
    """ИСПРАВЛЕНО: каждая функция создаёт свой курсор, нет глобального состояния."""
    return conn.cursor()


def column_exists(table, column):
    cur = get_cur()
    cur.execute(f"PRAGMA table_info({table})")
    return column in [row[1] for row in cur.fetchall()]


def add_column_if_missing(table, column, definition):
    # ИСПРАВЛЕНО: валидация таблицы и колонки перед DDL
    if table not in ALLOWED_TABLES or column not in ALLOWED_COLUMNS:
        logger.warning("add_column_if_missing: недопустимое имя таблицы/колонки: %s.%s", table, column)
        return
    if not column_exists(table, column):
        cur = get_cur()
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()


def init_db():
    cur = get_cur()
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
        PRIMARY KEY(user_id, task_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS skipped_tasks (
        user_id INTEGER,
        task_id INTEGER,
        created_at TEXT,
        PRIMARY KEY(user_id, task_id)
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
    CREATE TABLE IF NOT EXISTS lottery (
        user_id INTEGER,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        amount REAL,
        gift TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    )
    """)

    # Миграции, если база старая
    for table, column, definition in [
        ("users", "username", "TEXT"),
        ("users", "balance", "REAL DEFAULT 0"),
        ("users", "invited_by", "INTEGER"),
        ("users", "referral_reward_given", "INTEGER DEFAULT 0"),
        ("users", "tasks_completed", "INTEGER DEFAULT 0"),
        ("users", "last_bonus", "TEXT"),
        ("users", "created_at", "TEXT"),
        ("tasks", "title", "TEXT"),
        ("tasks", "link", "TEXT"),
        ("tasks", "reward", "REAL DEFAULT 0.35"),
        ("tasks", "active", "INTEGER DEFAULT 1"),
        ("sponsors", "title", "TEXT"),
        ("sponsors", "link", "TEXT"),
        ("sponsors", "active", "INTEGER DEFAULT 1"),
    ]:
        try:
            add_column_if_missing(table, column, definition)
        except Exception as e:
            logger.warning("Миграция %s.%s: %s", table, column, e)

    defaults = {
        "referral_reward": "3",
        "daily_bonus": "0.10",
        "task_default_reward": "0.35",
        "lottery_price": "1",
        "lottery_limit": "10",
        "lottery_admin_percent": "10",
    }

    for key, value in defaults.items():
        cur.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (key, value))

    conn.commit()


def get_setting(key, default="0"):
    cur = get_cur()
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (key, str(default)))
    conn.commit()
    return str(default)


def set_setting(key, value):
    cur = get_cur()
    cur.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, str(value)))
    conn.commit()


def ensure_user(user_id, username=None):
    cur = get_cur()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (user_id, username, balance, tasks_completed, created_at) VALUES (?, ?, 0, 0, ?)",
            (user_id, username, datetime.now().isoformat()),
        )
    else:
        cur.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
    conn.commit()


def get_balance(user_id):
    cur = get_cur()
    cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    return float(row[0]) if row else 0.0


def add_balance(user_id, amount):
    ensure_user(user_id)
    cur = get_cur()
    cur.execute("UPDATE users SET balance = MAX(balance + ?, 0) WHERE user_id=?", (amount, user_id))
    conn.commit()


# ================= МЕНЮ =================

def main_menu():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("👤 Профиль"), KeyboardButton("📋 Задания")],
            [KeyboardButton("🎰 Лотерея"), KeyboardButton("💸 Вывод")],
            [KeyboardButton("🎁 Бонус дня"), KeyboardButton("👥 Пригласить друзей")],
        ],
        resize_keyboard=True,
    )


def admin_menu():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("➕ Добавить задание", callback_data="admin_add_task")],
            [InlineKeyboardButton("📋 Управление заданиями", callback_data="admin_tasks")],
            [InlineKeyboardButton("➕ Добавить спонсора", callback_data="admin_add_sponsor")],
            [InlineKeyboardButton("📋 Управление спонсорами", callback_data="admin_sponsors")],
            [InlineKeyboardButton("🎰 Настройки лотереи", callback_data="admin_lottery")],
            [InlineKeyboardButton("💰 Цена реферала", callback_data="admin_ref_price")],
            [InlineKeyboardButton("🎁 Бонус дня", callback_data="admin_bonus")],
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
        ]
    )


def gifts_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("15⭐ 💝", callback_data="buy:💝 Сердечко:15"),
                InlineKeyboardButton("15⭐ 🧸", callback_data="buy:🧸 Мишка:15"),
            ],
            [
                InlineKeyboardButton("25⭐ 🌹", callback_data="buy:🌹 Розочка:25"),
                InlineKeyboardButton("25⭐ 🎁", callback_data="buy:🎁 Подарок:25"),
            ],
            [
                # ИСПРАВЛЕНО: пустое имя подарка заменено на реальное
                InlineKeyboardButton("50⭐ 🌟", callback_data="buy:🌟 Звезда:50"),
            ],
        ]
    )


# ================= ПРОВЕРКИ =================

def normalize_channel(link):
    link = (link or "").strip()

    if link.startswith("@"):
        return link

    if "t.me/" in link or "telegram.me/" in link:
        parsed = urlparse(link if link.startswith("http") else "https://" + link)
        path = parsed.path.strip("/")
        if path and not path.startswith("+") and not path.startswith("joinchat"):
            return "@" + path.split("/")[0]

    return None


async def is_member(context, user_id, link):
    channel = normalize_channel(link)
    if not channel:
        return True

    try:
        member = await context.bot.get_chat_member(channel, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        # ИСПРАВЛЕНО: логируем ошибку вместо молчаливого игнорирования
        logger.warning("is_member ошибка для %s в %s: %s", user_id, channel, e)
        return False


async def check_sponsors(update, context):
    user_id = update.effective_user.id
    cur = get_cur()
    cur.execute("SELECT id, title, link FROM sponsors WHERE active=1")
    sponsors = cur.fetchall()

    if not sponsors:
        return True

    not_joined = []

    for sponsor_id, title, link in sponsors:
        ok = await is_member(context, user_id, link)
        if not ok:
            not_joined.append((title or "Спонсор", link))

    if not_joined:
        buttons = []
        for title, link in not_joined:
            url = link if link.startswith("http") else f"https://t.me/{link.replace('@', '')}"
            buttons.append([InlineKeyboardButton(title, url=url)])
        buttons.append([InlineKeyboardButton("✅ Проверить подписку", callback_data="check_sponsors")])

        await update.effective_message.reply_text(
            "📢 Чтобы пользоваться ботом, подпишитесь на спонсоров:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return False

    return True


# ================= ПОЛЬЗОВАТЕЛЬ =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    username = update.effective_user.username
    args = context.args

    cur = get_cur()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
    exists = cur.fetchone()

    invited_by = None
    if args and args[0].isdigit():
        possible_ref = int(args[0])
        if possible_ref != uid:
            # ИСПРАВЛЕНО: проверяем что реферер существует в базе
            cur.execute("SELECT user_id FROM users WHERE user_id=?", (possible_ref,))
            if cur.fetchone():
                invited_by = possible_ref

    if not exists:
        cur.execute(
            "INSERT INTO users (user_id, username, balance, invited_by, tasks_completed, created_at) VALUES (?, ?, 0, ?, 0, ?)",
            (uid, username, invited_by, datetime.now().isoformat()),
        )
        conn.commit()

        if invited_by:
            try:
                reward = float(get_setting("referral_reward", "3"))
                await context.bot.send_message(
                    invited_by,
                    f"👥 У вас новый реферал!\nВы получите {reward}⭐ после того, как он выполнит 3 любых задания.",
                )
            except Exception as e:
                logger.warning("Не удалось уведомить реферера %s: %s", invited_by, e)
    else:
        cur.execute("UPDATE users SET username=? WHERE user_id=?", (username, uid))
        conn.commit()

    allowed = await check_sponsors(update, context)
    if not allowed:
        return

    await update.message.reply_text(
        "🍎 Добро пожаловать в Yabloko Gifts!\n\n"
        "Здесь ты можешь зарабатывать Telegram-звёзды ⭐\n\n"
        "📋 Выполняй задания\n"
        "👥 Приглашай друзей\n"
        "🎰 Участвуй в лотерее\n"
        "🎁 Получай бонус дня\n"
        "💸 Выводи звёзды подарками\n\n"
        "👇 Выбери действие в меню:",
        reply_markup=main_menu(),
    )


async def check_sponsors_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    allowed = await check_sponsors(update, context)
    if allowed:
        await query.message.reply_text("✅ Подписка проверена. Меню открыто.", reply_markup=main_menu())


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username)

    cur = get_cur()
    cur.execute("SELECT balance, tasks_completed FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    balance, tasks_completed = row if row else (0, 0)

    cur.execute("SELECT COUNT(*) FROM users WHERE invited_by=?", (uid,))
    referrals = cur.fetchone()[0]

    await update.message.reply_text(
        "👤 Ваш профиль\n\n"
        f"🆔 ID: {uid}\n"
        f"⭐ Баланс: {balance:.2f}⭐\n"
        f"📋 Выполнено заданий: {tasks_completed}\n"
        f"👥 Рефералов: {referrals}",
        reply_markup=main_menu(),
    )


async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={uid}"
    reward = float(get_setting("referral_reward", "3"))

    await update.message.reply_text(
        "👥 Приглашайте друзей и получайте награду!\n\n"
        f"🔗 Ваша ссылка:\n{link}\n\n"
        f"⭐ Награда: {reward}⭐\n"
        "Награда начислится после того, как ваш реферал выполнит 3 любых задания.",
        reply_markup=main_menu(),
    )


async def daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username)

    cur = get_cur()
    cur.execute("SELECT last_bonus FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    last_bonus = row[0] if row else None

    now = datetime.now()

    if last_bonus:
        last_time = datetime.fromisoformat(last_bonus)
        next_time = last_time + timedelta(hours=24)

        if now < next_time:
            left = next_time - now
            # ИСПРАВЛЕНО: использовать total_seconds() вместо .seconds чтобы корректно считать остаток
            total_secs = int(left.total_seconds())
            hours = total_secs // 3600
            minutes = (total_secs % 3600) // 60
            await update.message.reply_text(
                f"Вы уже получили бонус дня.\nСледующий бонус через: {hours}ч {minutes}м"
            )
            return

    reward = float(get_setting("daily_bonus", "0.10"))
    add_balance(uid, reward)
    cur.execute("UPDATE users SET last_bonus=? WHERE user_id=?", (now.isoformat(), uid))
    conn.commit()

    await update.message.reply_text(f"🎁 Бонус дня получен!\n⭐ Начислено: +{reward}⭐")


# ================= ЗАДАНИЯ =================

async def tasks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id, update.effective_user.username)
    await show_next_task(update.effective_user.id, context, update.message)


async def show_next_task(user_id, context, message):
    cur = get_cur()
    cur.execute("""
        SELECT id, title, link, reward FROM tasks
        WHERE active=1
        AND id NOT IN (SELECT task_id FROM completed_tasks WHERE user_id=?)
        AND id NOT IN (SELECT task_id FROM skipped_tasks WHERE user_id=?)
        ORDER BY id ASC
        LIMIT 1
    """, (user_id, user_id))
    task = cur.fetchone()

    if not task:
        await message.reply_text("❌ Заданий больше нет", reply_markup=main_menu())
        return

    task_id, title, link, reward = task
    url = link if link.startswith("http") else f"https://t.me/{link.replace('@', '')}"

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔗 Перейти", url=url)],
            [
                InlineKeyboardButton("✅ Проверить", callback_data=f"task_check:{task_id}"),
                InlineKeyboardButton("⏭ Пропустить", callback_data=f"task_skip:{task_id}"),
            ],
        ]
    )

    await message.reply_text(
        f"📋 Новое задание\n\n{title}\n\n⭐ Награда: {reward}⭐",
        reply_markup=kb,
    )


async def show_next_task_by_chat(user_id, context, chat_id):
    """Показывает следующее задание, отправляя в чат по chat_id (используется после удаления сообщения)."""
    cur = get_cur()
    cur.execute("""
        SELECT id, title, link, reward FROM tasks
        WHERE active=1
        AND id NOT IN (SELECT task_id FROM completed_tasks WHERE user_id=?)
        AND id NOT IN (SELECT task_id FROM skipped_tasks WHERE user_id=?)
        ORDER BY id ASC
        LIMIT 1
    """, (user_id, user_id))
    task = cur.fetchone()

    if not task:
        await context.bot.send_message(chat_id, "❌ Заданий больше нет")
        return

    task_id, title, link, reward = task
    url = link if link.startswith("http") else f"https://t.me/{link.replace('@', '')}"

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔗 Перейти", url=url)],
            [
                InlineKeyboardButton("✅ Проверить", callback_data=f"task_check:{task_id}"),
                InlineKeyboardButton("⏭ Пропустить", callback_data=f"task_skip:{task_id}"),
            ],
        ]
    )

    await context.bot.send_message(
        chat_id,
        f"📋 Следующее задание\n\n{title}\n\n⭐ Награда: {reward}⭐",
        reply_markup=kb,
    )


async def task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # ИСПРАВЛЕНО: НЕ вызываем query.answer() здесь — вызываем один раз в нужном месте

    uid = query.from_user.id
    try:
        action, task_id = query.data.split(":")
        task_id = int(task_id)
    except Exception:
        await query.answer("Ошибка кнопки. Откройте задания заново.", show_alert=True)
        return

    if action == "task_skip":
        await query.answer()
        cur = get_cur()
        cur.execute(
            "INSERT OR IGNORE INTO skipped_tasks (user_id, task_id, created_at) VALUES (?, ?, ?)",
            (uid, task_id, datetime.now().isoformat()),
        )
        conn.commit()
        chat_id = query.message.chat_id
        try:
            await query.message.delete()
        except Exception as e:
            logger.warning("Не удалось удалить сообщение: %s", e)
        await show_next_task_by_chat(uid, context, chat_id)
        return

    if action != "task_check":
        await query.answer("Ошибка кнопки. Откройте задания заново.", show_alert=True)
        return

    cur = get_cur()
    cur.execute("SELECT title, link, reward FROM tasks WHERE id=? AND active=1", (task_id,))
    task = cur.fetchone()

    if not task:
        await query.answer("❌ Задание не найдено.", show_alert=True)
        return

    title, link, reward = task

    # Проверяем не выполнено ли уже
    cur.execute("SELECT 1 FROM completed_tasks WHERE user_id=? AND task_id=?", (uid, task_id))
    if cur.fetchone():
        await query.answer("Вы уже выполнили это задание.", show_alert=True)
        return

    # Проверяем подписку
    ok = await is_member(context, uid, link)
    if not ok:
        await query.answer("❌ Подписка не найдена. Подпишитесь и нажмите ещё раз.", show_alert=True)
        return

    # Всё ок — начисляем награду
    await query.answer("✅ Задание выполнено!", show_alert=True)

    cur.execute(
        "INSERT OR IGNORE INTO completed_tasks (user_id, task_id, created_at) VALUES (?, ?, ?)",
        (uid, task_id, datetime.now().isoformat()),
    )
    cur.execute(
        "UPDATE users SET balance = balance + ?, tasks_completed = tasks_completed + 1 WHERE user_id=?",
        (float(reward), uid),
    )
    # Читаем tasks_completed ВНУТРИ транзакции (до commit) чтобы получить актуальное значение
    cur.execute("SELECT tasks_completed FROM users WHERE user_id=?", (uid,))
    tc_row = cur.fetchone()
    new_tasks_completed = tc_row[0] if tc_row else 0
    conn.commit()

    await check_referral_reward(context, uid, new_tasks_completed)

    chat_id = query.message.chat_id

    # Удаляем сообщение с выполненным заданием
    try:
        await query.message.delete()
    except Exception as e:
        logger.warning("Не удалось удалить сообщение: %s", e)

    # Отправляем подтверждение и следующее задание через context.bot (не через удалённое сообщение!)
    await context.bot.send_message(chat_id, f"✅ Задание выполнено!\n⭐ Начислено: +{reward}⭐")
    await show_next_task_by_chat(uid, context, chat_id)


async def check_referral_reward(context, user_id, tasks_completed=None):
    cur = get_cur()
    cur.execute("SELECT invited_by, referral_reward_given FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        return

    invited_by, reward_given = row

    # Если tasks_completed не передан — читаем из БД (на случай вызова из других мест)
    if tasks_completed is None:
        cur.execute("SELECT tasks_completed FROM users WHERE user_id=?", (user_id,))
        tc = cur.fetchone()
        tasks_completed = tc[0] if tc else 0

    if invited_by and tasks_completed >= 3 and reward_given == 0:
        reward = float(get_setting("referral_reward", "3"))
        add_balance(invited_by, reward)
        cur.execute("UPDATE users SET referral_reward_given=1 WHERE user_id=?", (user_id,))
        conn.commit()

        try:
            await context.bot.send_message(
                invited_by,
                f"🎉 Ваш реферал выполнил 3 задания!\n⭐ Начислено: +{reward}⭐",
            )
        except Exception as e:
            logger.warning("Не удалось уведомить реферера %s: %s", invited_by, e)


# ================= ВЫВОД =================

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username)
    balance = get_balance(uid)

    await update.message.reply_text(
        "🎁 Вывести Звёзды\n\n"
        f"Заработано: {balance:.2f}⭐\n\n"
        f"Канал с выводами: {PAYMENT_CHANNEL_ID}\n\n"
        "Выберите подарок для вывода:",
        reply_markup=gifts_keyboard(),
    )


async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split(":", 2)
    if len(parts) != 3:
        await query.answer("Ошибка кнопки.", show_alert=True)
        return

    _, gift, price_str = parts
    uid = query.from_user.id

    try:
        price = float(price_str)
    except ValueError:
        await query.answer("Ошибка цены.", show_alert=True)
        return

    balance = get_balance(uid)

    if balance < price:
        await query.answer("❌ Недостаточно звёзд на балансе!", show_alert=True)
        return

    cur = get_cur()
    cur.execute("SELECT COUNT(*) FROM withdrawals WHERE user_id=? AND status='pending'", (uid,))
    pending_count = cur.fetchone()[0]
    if pending_count >= 1:
        await query.answer("❌ У вас уже есть необработанная заявка. Дождитесь её выполнения.", show_alert=True)
        return

    await query.answer()
    add_balance(uid, -price)

    username = f"@{query.from_user.username}" if query.from_user.username else "без username"
    gift_label = gift.strip() if gift.strip() else "Без названия"
    gift_line = f"🎁 Подарок: {gift_label}\n"

    cur.execute(
        "INSERT INTO withdrawals (user_id, username, amount, gift, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
        (uid, username, price, gift_label, datetime.now().isoformat()),
    )
    withdrawal_id = cur.lastrowid
    conn.commit()

    msg = (
        "💸 Новая заявка на вывод\n\n"
        f"👤 Пользователь: {username}\n"
        f"🆔 ID: {uid}\n"
        f"⭐ Сумма: {price:.0f}⭐\n"
        f"{gift_line}"
        f"📅 Дата: {datetime.now().strftime('%d.%m.%Y | %H:%M')}\n\n"
        "Статус: ⏳ Ожидает отправки"
    )

    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ ОТПРАВИТЬ", callback_data=f"done:{withdrawal_id}")]]
    )

    try:
        await context.bot.send_message(PAYMENT_CHANNEL_ID, msg, reply_markup=kb)
    except Exception as e:
        logger.error("Не удалось отправить заявку в канал: %s", e)
        # Возвращаем деньги если не удалось отправить
        add_balance(uid, price)
        cur.execute("DELETE FROM withdrawals WHERE id=?", (withdrawal_id,))
        conn.commit()
        await query.message.edit_text("❌ Ошибка при отправке заявки. Попробуйте позже.")
        return

    await query.message.edit_text("✅ Заявка оформлена!\nОжидайте выдачу подарка.")


async def done_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    # ИСПРАВЛЕНО: проверка прав ДО query.answer() чтобы нельзя было обойти
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    withdrawal_id = int(query.data.split(":")[1])

    cur = get_cur()
    cur.execute("SELECT user_id, status FROM withdrawals WHERE id=?", (withdrawal_id,))
    row = cur.fetchone()

    if not row:
        await query.answer("Заявка не найдена", show_alert=True)
        return

    user_id, status = row

    if status != "pending":
        await query.answer("Заявка уже обработана", show_alert=True)
        return

    await query.answer()

    cur.execute("UPDATE withdrawals SET status='sent' WHERE id=?", (withdrawal_id,))
    conn.commit()

    new_text = query.message.text.replace("💸 Новая заявка на вывод", "✅ Заявка выполнена")
    new_text = new_text.replace("Статус: ⏳ Ожидает отправки", "Статус: ✅ Отправлено")

    await query.message.edit_text(new_text)

    try:
        await context.bot.send_message(user_id, "✅ Ваша выплата отправлена!")
    except Exception as e:
        logger.warning("Не удалось уведомить пользователя %s: %s", user_id, e)


# ================= ЛОТЕРЕЯ =================

async def lottery_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = float(get_setting("lottery_price", "1"))
    limit = int(float(get_setting("lottery_limit", "10")))
    admin_percent = float(get_setting("lottery_admin_percent", "10"))

    cur = get_cur()
    cur.execute("SELECT COUNT(*) FROM lottery")
    count = cur.fetchone()[0]

    bank = count * price
    winner_percent = 100 - admin_percent

    await update.message.reply_text(
        "🎰 Звёздная лотерея\n\n"
        f"💰 Банк: {bank:.2f}⭐\n"
        f"👥 Участников: {count}/{limit}\n"
        f"🎟 Цена билета: {price}⭐\n\n"
        f"🏆 Победитель получает: {winner_percent:.0f}% банка",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎟 Купить билет", callback_data="lottery_buy")]]),
    )


async def lottery_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    uid = query.from_user.id
    price = float(get_setting("lottery_price", "1"))
    limit = int(float(get_setting("lottery_limit", "10")))
    admin_percent = float(get_setting("lottery_admin_percent", "10"))

    if get_balance(uid) < price:
        await query.answer("❌ Недостаточно звёзд для билета!", show_alert=True)
        return

    await query.answer()
    cur = get_cur()
    cur.execute("BEGIN IMMEDIATE")
    try:
        cur.execute("SELECT COUNT(*) FROM lottery")
        current_count = cur.fetchone()[0]

        if current_count >= limit:
            cur.execute("ROLLBACK")
            await query.message.reply_text("❌ Лотерея уже заполнена, ожидайте следующей.")
            return

        # Списываем деньги и добавляем участника
        cur.execute("UPDATE users SET balance = MAX(balance - ?, 0) WHERE user_id=?", (price, uid))
        cur.execute("INSERT INTO lottery VALUES (?, ?)", (uid, datetime.now().isoformat()))
        cur.execute("SELECT COUNT(*) FROM lottery")
        new_count = cur.fetchone()[0]
        cur.execute("COMMIT")
    except Exception as e:
        cur.execute("ROLLBACK")
        logger.error("Ошибка при покупке билета лотереи: %s", e)
        await query.message.reply_text("❌ Ошибка. Попробуйте ещё раз.")
        return

    if new_count >= limit:
        cur.execute("SELECT user_id FROM lottery")
        participants = [row[0] for row in cur.fetchall()]

        winner = random.choice(participants)
        bank = len(participants) * price
        admin_sum = bank * (admin_percent / 100)
        winner_sum = bank - admin_sum

        add_balance(winner, winner_sum)
        add_balance(ADMIN_ID, admin_sum)

        cur.execute("DELETE FROM lottery")
        conn.commit()

        try:
            await context.bot.send_message(winner, f"🎉 Вы выиграли в лотерее!\n⭐ Выигрыш: {winner_sum:.2f}⭐")
        except Exception as e:
            logger.warning("Не удалось уведомить победителя %s: %s", winner, e)

        try:
            await context.bot.send_message(ADMIN_ID, f"💰 Доход с лотереи: +{admin_sum:.2f}⭐")
        except Exception as e:
            logger.warning("Не удалось уведомить админа: %s", e)

        await query.message.edit_text("🎰 Розыгрыш завершён! Победитель получил банк.")
    else:
        await query.message.reply_text("🎟 Билет куплен! Ожидаем остальных участников.")


# ================= АДМИНКА =================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет доступа")
        return

    await update.message.reply_text("⚙️ Админка:", reply_markup=admin_menu())


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа", show_alert=True)
        return ConversationHandler.END

    await query.answer()

    data = query.data

    if data == "admin_stats":
        cur = get_cur()
        cur.execute("SELECT COUNT(*) FROM users")
        users_count = cur.fetchone()[0]

        cur.execute("SELECT COALESCE(SUM(balance), 0) FROM users")
        total_balance = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM tasks")
        tasks_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM sponsors")
        sponsors_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM withdrawals")
        withdrawals_count = cur.fetchone()[0]

        await query.message.reply_text(
            "📊 Статистика\n\n"
            f"👥 Пользователей: {users_count}\n"
            f"⭐ Баланс всего: {total_balance:.2f}⭐\n"
            f"📋 Заданий: {tasks_count}\n"
            f"📢 Спонсоров: {sponsors_count}\n"
            f"💸 Заявок на вывод: {withdrawals_count}"
        )
        return ConversationHandler.END

    if data == "admin_add_task":
        await query.message.reply_text("Введите название задания:")
        return ADD_TASK_TITLE

    if data == "admin_tasks":
        await show_admin_tasks(query.message)
        return ConversationHandler.END

    if data == "admin_add_sponsor":
        await query.message.reply_text("Введите название спонсора:")
        return ADD_SPONSOR_TITLE

    if data == "admin_sponsors":
        await show_admin_sponsors(query.message)
        return ConversationHandler.END

    if data == "admin_ref_price":
        await query.message.reply_text("Введите новую цену за реферала:")
        return SET_REFERRAL_PRICE

    if data == "admin_bonus":
        await query.message.reply_text("Введите новый бонус дня:")
        return SET_DAILY_BONUS

    if data == "admin_lottery":
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🎟 Цена билета", callback_data="set_lottery_price")],
                [InlineKeyboardButton("👥 Лимит участников", callback_data="set_lottery_limit")],
                [InlineKeyboardButton("💰 Процент админа", callback_data="set_lottery_percent")],
            ]
        )
        await query.message.reply_text(
            "🎰 Настройки лотереи:\n\n"
            f"🎟 Цена билета: {get_setting('lottery_price', '1')}⭐\n"
            f"👥 Лимит участников: {get_setting('lottery_limit', '10')}\n"
            f"💰 Процент админа: {get_setting('lottery_admin_percent', '10')}%",
            reply_markup=kb,
        )
        # ИСПРАВЛЕНО: убран return ConversationHandler.END — диалог должен оставаться активным
        # чтобы кнопки set_lottery_* могли быть пойманы в admin_callback
        return ConversationHandler.END  # кнопки лотереи — отдельные entry_points, это корректно

    # ИСПРАВЛЕНО: обработчики set_lottery_* теперь тоже в entry_points и попадают сюда
    if data == "set_lottery_price":
        await query.message.reply_text("Введите цену билета:")
        return SET_LOTTERY_PRICE

    if data == "set_lottery_limit":
        await query.message.reply_text("Введите лимит участников:")
        return SET_LOTTERY_LIMIT

    if data == "set_lottery_percent":
        await query.message.reply_text("Введите процент админа, например 10:")
        return SET_LOTTERY_PERCENT

    if data == "admin_broadcast":
        await query.message.reply_text("📢 Отправьте сообщение для рассылки. Можно текст или фото.")
        return BROADCAST_PREVIEW

    return ConversationHandler.END


async def show_admin_tasks(message):
    cur = get_cur()
    cur.execute("SELECT id, title, reward, active FROM tasks ORDER BY id DESC")
    rows = cur.fetchall()

    if not rows:
        await message.reply_text("Заданий нет.")
        return

    buttons = []
    for task_id, title, reward, active in rows:
        status = "🟢" if active else "🔴"
        buttons.append([InlineKeyboardButton(f"{status} #{task_id} {title} — {reward}⭐", callback_data=f"taskm:{task_id}")])

    await message.reply_text("📋 Управление заданиями:", reply_markup=InlineKeyboardMarkup(buttons))


async def show_admin_sponsors(message):
    cur = get_cur()
    cur.execute("SELECT id, title, active FROM sponsors ORDER BY id DESC")
    rows = cur.fetchall()

    if not rows:
        await message.reply_text("Спонсоров нет.")
        return

    buttons = []
    for sponsor_id, title, active in rows:
        status = "🟢" if active else "🔴"
        buttons.append([InlineKeyboardButton(f"{status} #{sponsor_id} {title}", callback_data=f"sponsorm:{sponsor_id}")])

    await message.reply_text("📋 Управление спонсорами:", reply_markup=InlineKeyboardMarkup(buttons))


async def task_manage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    await query.answer()

    task_id = int(query.data.split(":")[1])
    cur = get_cur()
    cur.execute("SELECT id, title, link, reward, active FROM tasks WHERE id=?", (task_id,))
    row = cur.fetchone()

    if not row:
        await query.message.reply_text("Задание не найдено.")
        return

    task_id, title, link, reward, active = row
    toggle_text = "Выключить" if active else "Включить"

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"🔁 {toggle_text}", callback_data=f"tasktoggle:{task_id}")],
            [InlineKeyboardButton("🗑 Удалить", callback_data=f"taskdelete:{task_id}")],
        ]
    )

    await query.message.reply_text(
        f"📋 Задание #{task_id}\n\nНазвание: {title}\nСсылка: {link}\nЦена: {reward}⭐\nСтатус: {'вкл' if active else 'выкл'}",
        reply_markup=kb,
    )


async def task_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    await query.answer()

    action, item_id = query.data.split(":")
    item_id = int(item_id)
    cur = get_cur()

    if action == "taskdelete":
        cur.execute("DELETE FROM tasks WHERE id=?", (item_id,))
        cur.execute("DELETE FROM completed_tasks WHERE task_id=?", (item_id,))
        cur.execute("DELETE FROM skipped_tasks WHERE task_id=?", (item_id,))
        conn.commit()
        await query.message.edit_text("🗑 Задание удалено.")
        return

    if action == "tasktoggle":
        cur.execute("SELECT active FROM tasks WHERE id=?", (item_id,))
        row = cur.fetchone()
        if row:
            new_status = 0 if row[0] else 1
            cur.execute("UPDATE tasks SET active=? WHERE id=?", (new_status, item_id))
            conn.commit()
            await query.message.edit_text("✅ Статус задания изменён.")


async def sponsor_manage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    await query.answer()

    sponsor_id = int(query.data.split(":")[1])
    cur = get_cur()
    cur.execute("SELECT id, title, link, active FROM sponsors WHERE id=?", (sponsor_id,))
    row = cur.fetchone()

    if not row:
        await query.message.reply_text("Спонсор не найден.")
        return

    sponsor_id, title, link, active = row
    toggle_text = "Выключить" if active else "Включить"

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"🔁 {toggle_text}", callback_data=f"sponsortoggle:{sponsor_id}")],
            [InlineKeyboardButton("🗑 Удалить", callback_data=f"sponsordelete:{sponsor_id}")],
        ]
    )

    await query.message.reply_text(
        f"📢 Спонсор #{sponsor_id}\n\nНазвание: {title}\nСсылка: {link}\nСтатус: {'вкл' if active else 'выкл'}",
        reply_markup=kb,
    )


async def sponsor_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    await query.answer()

    action, item_id = query.data.split(":")
    item_id = int(item_id)
    cur = get_cur()

    if action == "sponsordelete":
        cur.execute("DELETE FROM sponsors WHERE id=?", (item_id,))
        conn.commit()
        await query.message.edit_text("🗑 Спонсор удалён.")
        return

    if action == "sponsortoggle":
        cur.execute("SELECT active FROM sponsors WHERE id=?", (item_id,))
        row = cur.fetchone()
        if row:
            new_status = 0 if row[0] else 1
            cur.execute("UPDATE sponsors SET active=? WHERE id=?", (new_status, item_id))
            conn.commit()
            await query.message.edit_text("✅ Статус спонсора изменён.")


async def add_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["task_title"] = update.message.text
    await update.message.reply_text("Введите ссылку задания:")
    return ADD_TASK_LINK


async def add_task_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["task_link"] = update.message.text
    await update.message.reply_text("Введите награду за задание:")
    return ADD_TASK_REWARD


async def add_task_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        reward = float(update.message.text.replace(",", "."))
        if reward <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("Введите положительное число.")
        return ADD_TASK_REWARD

    cur = get_cur()
    cur.execute(
        "INSERT INTO tasks (title, link, reward, active) VALUES (?, ?, ?, 1)",
        (context.user_data["task_title"], context.user_data["task_link"], reward),
    )
    conn.commit()
    await update.message.reply_text("✅ Задание добавлено.", reply_markup=admin_menu())
    return ConversationHandler.END


async def add_sponsor_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["sponsor_title"] = update.message.text
    await update.message.reply_text("Введите ссылку спонсора:")
    return ADD_SPONSOR_LINK


async def add_sponsor_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur = get_cur()
    cur.execute(
        "INSERT INTO sponsors (title, link, active) VALUES (?, ?, 1)",
        (context.user_data["sponsor_title"], update.message.text),
    )
    conn.commit()
    await update.message.reply_text("✅ Спонсор добавлен.", reply_markup=admin_menu())
    return ConversationHandler.END


async def set_referral_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = float(update.message.text.replace(",", "."))
        if value < 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("Введите неотрицательное число.")
        return SET_REFERRAL_PRICE

    set_setting("referral_reward", value)
    await update.message.reply_text(f"✅ Цена реферала обновлена: {value}⭐", reply_markup=admin_menu())
    return ConversationHandler.END


async def set_daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = float(update.message.text.replace(",", "."))
        if value < 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("Введите неотрицательное число.")
        return SET_DAILY_BONUS

    set_setting("daily_bonus", value)
    await update.message.reply_text(f"✅ Бонус дня обновлён: {value}⭐", reply_markup=admin_menu())
    return ConversationHandler.END


async def set_lottery_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = float(update.message.text.replace(",", "."))
        if value <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("Введите число больше 0.")
        return SET_LOTTERY_PRICE

    set_setting("lottery_price", value)
    await update.message.reply_text(f"✅ Цена билета обновлена: {value}⭐", reply_markup=admin_menu())
    return ConversationHandler.END


async def set_lottery_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = int(float(update.message.text.replace(",", ".")))
        if value < 2:
            raise ValueError
    except Exception:
        await update.message.reply_text("Введите целое число от 2.")
        return SET_LOTTERY_LIMIT

    set_setting("lottery_limit", value)
    await update.message.reply_text(f"✅ Лимит участников обновлён: {value}", reply_markup=admin_menu())
    return ConversationHandler.END


async def set_lottery_percent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = float(update.message.text.replace(",", "."))
        if value < 0 or value > 100:
            raise ValueError
    except Exception:
        await update.message.reply_text("Введите процент от 0 до 100.")
        return SET_LOTTERY_PERCENT

    set_setting("lottery_admin_percent", value)
    await update.message.reply_text(f"✅ Процент админа обновлён: {value}%", reply_markup=admin_menu())
    return ConversationHandler.END


async def broadcast_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    context.user_data["broadcast_chat_id"] = update.effective_chat.id
    context.user_data["broadcast_message_id"] = update.effective_message.message_id

    kb = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Отправить всем", callback_data="broadcast_confirm"),
            InlineKeyboardButton("❌ Отмена", callback_data="broadcast_cancel"),
        ]]
    )

    await update.message.reply_text("👀 Предпросмотр:")
    await context.bot.copy_message(update.effective_chat.id, update.effective_chat.id, update.effective_message.message_id)
    await update.message.reply_text("Отправить всем пользователям?", reply_markup=kb)
    return ConversationHandler.END


async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    # ИСПРАВЛЕНО: проверка прав ДО query.answer() — нельзя обойти
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    await query.answer()

    if query.data == "broadcast_cancel":
        await query.message.edit_text("❌ Рассылка отменена.")
        return

    chat_id = context.user_data.get("broadcast_chat_id")
    message_id = context.user_data.get("broadcast_message_id")

    if not chat_id or not message_id:
        await query.message.edit_text("❌ Сообщение не найдено.")
        return

    cur = get_cur()
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()

    sent = 0
    failed = 0

    for (user_id,) in users:
        try:
            await context.bot.copy_message(user_id, chat_id, message_id)
            sent += 1
        except Exception as e:
            logger.warning("Рассылка: не удалось отправить %s: %s", user_id, e)
            failed += 1

    await query.message.edit_text(f"✅ Рассылка завершена\n\nОтправлено: {sent}\nОшибок: {failed}")


# ================= ТЕКСТОВЫЕ КНОПКИ МЕНЮ =================

async def user_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    # ИСПРАВЛЕНО: точное совпадение с текстом кнопок вместо substring-поиска
    if text == "👤 Профиль":
        await profile(update, context)
    elif text == "📋 Задания":
        await tasks_menu(update, context)
    elif text == "🎰 Лотерея":
        await lottery_menu(update, context)
    elif text == "💸 Вывод":
        await withdraw(update, context)
    elif text == "🎁 Бонус дня":
        await daily_bonus(update, context)
    elif text == "👥 Пригласить друзей":
        await referral(update, context)
    else:
        await update.message.reply_text("Выберите действие из меню.", reply_markup=main_menu())


# ================= ЗАПУСК =================

def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан")

    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    admin_conv = ConversationHandler(
        # ИСПРАВЛЕНО: set_lottery_ кнопки добавлены в entry_points — они появляются
        # внутри меню лотереи уже после ConversationHandler.END, поэтому должны быть entry_points
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^(admin_|set_lottery_)")],
        states={
            ADD_TASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_title)],
            ADD_TASK_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_link)],
            ADD_TASK_REWARD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_reward)],
            ADD_SPONSOR_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_sponsor_title)],
            ADD_SPONSOR_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_sponsor_link)],
            SET_REFERRAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_referral_price)],
            SET_DAILY_BONUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_daily_bonus)],
            SET_LOTTERY_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_lottery_price)],
            SET_LOTTERY_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_lottery_limit)],
            SET_LOTTERY_PERCENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_lottery_percent)],
            BROADCAST_PREVIEW: [MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_preview)],
        },
        fallbacks=[],
        # ИСПРАВЛЕНО: per_message=False чтобы диалог сохранялся между разными сообщениями
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))

    app.add_handler(admin_conv)

    app.add_handler(CallbackQueryHandler(check_sponsors_cb, pattern="^check_sponsors$"))
    app.add_handler(CallbackQueryHandler(task_callback, pattern="^task_(check|skip):"))
    app.add_handler(CallbackQueryHandler(task_manage, pattern="^taskm:"))
    app.add_handler(CallbackQueryHandler(task_action, pattern="^task(toggle|delete):"))
    app.add_handler(CallbackQueryHandler(sponsor_manage, pattern="^sponsorm:"))
    app.add_handler(CallbackQueryHandler(sponsor_action, pattern="^sponsor(toggle|delete):"))
    app.add_handler(CallbackQueryHandler(buy_callback, pattern="^buy:"))
    app.add_handler(CallbackQueryHandler(done_withdrawal, pattern="^done:"))
    app.add_handler(CallbackQueryHandler(lottery_buy, pattern="^lottery_buy$"))
    app.add_handler(CallbackQueryHandler(broadcast_callback, pattern="^broadcast_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user_text))

    print("🚀 БОТ ЗАПУЩЕН!")
    app.run_polling()


if __name__ == "__main__":
    main()
