import aiosqlite
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "bot_data.db")

DEFAULT_SETTINGS = {
    "referral_reward": "3.5",
    "min_withdrawal": "15",
    "default_task_reward": "0.35",
    "payout_channel": "@yabloko_gifts_channel",
}


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                stars REAL DEFAULT 0.0,
                referral_code TEXT UNIQUE,
                referred_by INTEGER,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                reward_stars REAL DEFAULT 0.35,
                url TEXT,
                is_active INTEGER DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS completed_tasks (
                user_id INTEGER,
                task_id INTEGER,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, task_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                status TEXT DEFAULT 'pending',
                requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                referrer_id INTEGER,
                referred_id INTEGER,
                reward_given INTEGER DEFAULT 0,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                rewarded_at TIMESTAMP,
                PRIMARY KEY (referrer_id, referred_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sponsors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                channel_username TEXT NOT NULL,
                channel_link TEXT NOT NULL,
                is_active INTEGER DEFAULT 1
            )
        """)

        # Safe migrations for existing DBs
        for col_sql in [
            "ALTER TABLE withdrawals ADD COLUMN channel_message_id INTEGER",
            "ALTER TABLE withdrawals ADD COLUMN channel_chat_id TEXT",
            "ALTER TABLE withdrawals ADD COLUMN wd_username TEXT",
            "ALTER TABLE referrals ADD COLUMN rewarded_at TIMESTAMP",
        ]:
            try:
                await db.execute(col_sql)
            except Exception:
                pass

        await _seed_settings(db)
        await _seed_tasks(db)
        await db.commit()


async def _seed_settings(db):
    for key, value in DEFAULT_SETTINGS.items():
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )


async def _seed_tasks(db):
    count = await db.execute("SELECT COUNT(*) FROM tasks")
    row = await count.fetchone()
    if row[0] == 0:
        sample_tasks = [
            ("Подписаться на Telegram-канал", "Подпишись на наш официальный Telegram-канал", 0.35, "https://t.me/yabloko_gifts_channel"),
            ("Подписаться на Twitter/X", "Подпишись на наш официальный аккаунт в Twitter/X", 0.35, "https://twitter.com/example"),
            ("Подписаться на YouTube", "Подпишись на наш YouTube-канал", 0.35, "https://youtube.com/example"),
            ("Сделать репост", "Сделай репост нашего последнего поста", 0.35, "https://twitter.com/example"),
            ("Вступить в Discord", "Вступи в наш Discord-сервер", 0.35, "https://discord.gg/example"),
        ]
        await db.executemany(
            "INSERT INTO tasks (title, description, reward_stars, url) VALUES (?, ?, ?, ?)",
            sample_tasks
        )


# ── Settings ─────────────────────────────────────────────────────────────────

async def get_setting(key: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else DEFAULT_SETTINGS.get(key, "")


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        await db.commit()


async def get_all_settings() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT key, value FROM settings")
        rows = await cursor.fetchall()
        return {r[0]: r[1] for r in rows}


# ── Users ─────────────────────────────────────────────────────────────────────

async def get_or_create_user(user_id: int, username: str, first_name: str, referred_by_code: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = await cursor.fetchone()

        if user is None:
            import secrets
            ref_code = secrets.token_hex(4).upper()

            referrer_id = None
            if referred_by_code:
                ref_cursor = await db.execute(
                    "SELECT user_id FROM users WHERE referral_code = ?", (referred_by_code,)
                )
                referrer = await ref_cursor.fetchone()
                if referrer and referrer["user_id"] != user_id:
                    referrer_id = referrer["user_id"]

            await db.execute(
                "INSERT INTO users (user_id, username, first_name, referral_code, referred_by) VALUES (?, ?, ?, ?, ?)",
                (user_id, username, first_name, ref_code, referrer_id)
            )

            # Record referral without giving reward yet (anti-fraud: reward given later)
            if referrer_id:
                await db.execute(
                    "INSERT OR IGNORE INTO referrals (referrer_id, referred_id, reward_given) VALUES (?, ?, 0)",
                    (referrer_id, user_id)
                )

            await db.commit()
            cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user = await cursor.fetchone()
        else:
            await db.execute(
                "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                (username, first_name, user_id)
            )
            await db.commit()

        return dict(user)


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_all_user_ids() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id FROM users")
        rows = await cursor.fetchall()
        return [r[0] for r in rows]


# ── Tasks ─────────────────────────────────────────────────────────────────────

async def get_all_tasks(include_inactive: bool = False):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if include_inactive:
            cursor = await db.execute("SELECT * FROM tasks ORDER BY id")
        else:
            cursor = await db.execute("SELECT * FROM tasks WHERE is_active = 1 ORDER BY id")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_task_by_id(task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def add_task(title: str, description: str, reward_stars: float, url: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO tasks (title, description, reward_stars, url) VALUES (?, ?, ?, ?)",
            (title, description, reward_stars, url)
        )
        await db.commit()


async def update_task_field(task_id: int, field: str, value):
    allowed = {"title", "description", "url", "reward_stars", "is_active"}
    if field not in allowed:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE tasks SET {field} = ? WHERE id = ?",
            (value, task_id)
        )
        await db.commit()


async def delete_task(task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await db.commit()


async def toggle_task(task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?",
            (task_id,)
        )
        await db.commit()


# ── Completed tasks ───────────────────────────────────────────────────────────

async def get_completed_tasks(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT task_id FROM completed_tasks WHERE user_id = ?", (user_id,)
        )
        rows = await cursor.fetchall()
        return {row[0] for row in rows}


async def get_completed_task_count(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM completed_tasks WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0]


async def complete_task(user_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        already = await db.execute(
            "SELECT 1 FROM completed_tasks WHERE user_id = ? AND task_id = ?", (user_id, task_id)
        )
        if await already.fetchone():
            return False, "already_done"

        task_cursor = await db.execute("SELECT * FROM tasks WHERE id = ? AND is_active = 1", (task_id,))
        task = await task_cursor.fetchone()
        if not task:
            return False, "not_found"

        task = dict(task)
        await db.execute(
            "INSERT INTO completed_tasks (user_id, task_id) VALUES (?, ?)", (user_id, task_id)
        )
        await db.execute(
            "UPDATE users SET stars = stars + ? WHERE user_id = ?",
            (task["reward_stars"], user_id)
        )
        await db.commit()
        return True, task


# ── Referrals ─────────────────────────────────────────────────────────────────

async def get_referral_count(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0]


async def get_rewarded_referral_count(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND reward_given = 1", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0]


async def get_pending_referral(user_id: int):
    """
    Returns (referrer_id, reward_amount) if the user has an unrewarded referral
    and the referrer hasn't exceeded the daily limit of 10 rewards.
    Returns None if no pending referral or limit exceeded.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        ref_cursor = await db.execute(
            "SELECT referrer_id, reward_given FROM referrals WHERE referred_id = ?",
            (user_id,)
        )
        ref = await ref_cursor.fetchone()
        if not ref or ref[1]:  # no referral or already rewarded
            return None

        referrer_id = ref[0]

        # Check daily limit: max 10 rewards per day per referrer
        daily_cursor = await db.execute(
            """SELECT COUNT(*) FROM referrals
               WHERE referrer_id = ? AND reward_given = 1
               AND DATE(rewarded_at) = DATE('now')""",
            (referrer_id,)
        )
        daily_count = (await daily_cursor.fetchone())[0]
        if daily_count >= 10:
            return None

        reward_amount = float(await get_setting("referral_reward"))
        return referrer_id, reward_amount


async def mark_referral_rewarded(user_id: int, referrer_id: int, amount: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE referrals SET reward_given = 1, rewarded_at = CURRENT_TIMESTAMP
               WHERE referred_id = ? AND referrer_id = ?""",
            (user_id, referrer_id)
        )
        await db.execute(
            "UPDATE users SET stars = stars + ? WHERE user_id = ?",
            (amount, referrer_id)
        )
        await db.commit()


# ── Sponsors ──────────────────────────────────────────────────────────────────

async def get_active_sponsors() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM sponsors WHERE is_active = 1 ORDER BY id"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_all_sponsors() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM sponsors ORDER BY id")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def add_sponsor(name: str, channel_username: str, channel_link: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sponsors (name, channel_username, channel_link) VALUES (?, ?, ?)",
            (name, channel_username, channel_link)
        )
        await db.commit()


async def delete_sponsor(sponsor_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM sponsors WHERE id = ?", (sponsor_id,))
        await db.commit()


async def toggle_sponsor(sponsor_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE sponsors SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?",
            (sponsor_id,)
        )
        await db.commit()


# ── Withdrawals ───────────────────────────────────────────────────────────────

async def create_withdrawal(user_id: int, amount: float):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        min_wd = float(await get_setting("min_withdrawal"))
        user_cursor = await db.execute("SELECT stars FROM users WHERE user_id = ?", (user_id,))
        user = await user_cursor.fetchone()

        if not user or user["stars"] < amount:
            return False, "insufficient_stars"
        if amount < min_wd:
            return False, "min_amount"

        await db.execute(
            "UPDATE users SET stars = stars - ? WHERE user_id = ?", (amount, user_id)
        )
        cursor = await db.execute(
            "INSERT INTO withdrawals (user_id, amount) VALUES (?, ?)",
            (user_id, amount)
        )
        withdrawal_id = cursor.lastrowid
        await db.commit()
        return True, withdrawal_id


async def get_withdrawal_by_id(withdrawal_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def store_withdrawal_channel_msg(withdrawal_id: int, chat_id: str, message_id: int, username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE withdrawals SET channel_chat_id = ?, channel_message_id = ?, wd_username = ? WHERE id = ?",
            (chat_id, message_id, username, withdrawal_id)
        )
        await db.commit()


async def update_withdrawal_status(withdrawal_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE withdrawals SET status = ? WHERE id = ?",
            (status, withdrawal_id)
        )
        await db.commit()


async def get_withdrawals(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM withdrawals WHERE user_id = ? ORDER BY requested_at DESC LIMIT 10",
            (user_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_all_withdrawals():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT w.id, w.user_id, w.amount, w.status, w.requested_at, u.username, u.first_name "
            "FROM withdrawals w LEFT JOIN users u ON w.user_id = u.user_id "
            "ORDER BY w.requested_at DESC LIMIT 20"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# ── Statistics ────────────────────────────────────────────────────────────────

async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        users_total = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        users_today = (await (await db.execute(
            "SELECT COUNT(*) FROM users WHERE DATE(joined_at) = DATE('now')"
        )).fetchone())[0]
        referrals_total = (await (await db.execute("SELECT COUNT(*) FROM referrals")).fetchone())[0]
        tasks_total = (await (await db.execute("SELECT COUNT(*) FROM tasks WHERE is_active=1")).fetchone())[0]
        withdrawals_total = (await (await db.execute("SELECT COUNT(*) FROM withdrawals")).fetchone())[0]
        sponsors_total = (await (await db.execute("SELECT COUNT(*) FROM sponsors WHERE is_active=1")).fetchone())[0]
        total_stars_row = await (await db.execute("SELECT SUM(stars) FROM users")).fetchone()
        total_stars = total_stars_row[0] or 0.0
        return {
            "users_total": users_total,
            "users_today": users_today,
            "referrals_total": referrals_total,
            "tasks_total": tasks_total,
            "withdrawals_total": withdrawals_total,
            "sponsors_total": sponsors_total,
            "total_stars": total_stars,
        }
