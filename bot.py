import asyncio
import base64
import logging
import os
import random
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup, Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

import content as C

# ───────────────────────── настройки ─────────────────────────
load_dotenv()


def env(name, default=''):
    return os.getenv(name, default).strip()


BOT_TOKEN = env('BOT_TOKEN')
OWNER_ID = int(env('OWNER_ID')) if env('OWNER_ID').isdigit() else None
CITY = env('CITY', 'Konaev')
TZ = ZoneInfo(env('TIMEZONE', 'Asia/Almaty'))
LAT = float(env('LAT', '43.8667'))
LON = float(env('LON', '77.0667'))
DATA_DIR = Path(env('DATA_DIR', '.'))          # на сервере сюда кладём папку с постоянной памятью
ANTHROPIC_API_KEY = env('ANTHROPIC_API_KEY')   # необязательно: включает ИИ-агента
if ANTHROPIC_API_KEY.upper().startswith('PASTE'):    # ключ-заглушка из .env.example считаем пустым
    ANTHROPIC_API_KEY = ''
AI_MODEL = env('AI_MODEL', 'claude-haiku-4-5-20251001')
AI_URL = 'https://api.anthropic.com/v1/messages'
WATER_GOAL = 8

if not BOT_TOKEN:
    raise RuntimeError('BOT_TOKEN is missing. Put it in .env or in the server environment variables')

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / 'dayly.db'

logging.basicConfig(format='%(asctime)s | %(levelname)s | %(message)s', level=logging.INFO)
log = logging.getLogger('dayly')

# ───────────────────────── расписание ─────────────────────────
LESSON_TIMES = [('08:00', '08:45'), ('08:50', '09:35'), ('09:45', '10:30'), ('10:40', '11:25'),
                ('11:30', '12:15'), ('12:20', '13:05'), ('13:10', '13:55')]
SCHEDULE = {
    0: ['Биология', 'Қазақ тілі', 'Алгебра', 'Қаз тарихы', 'Дене шыны', 'Ағылшын', 'Сынып сағаты'],
    1: ['Дене шыны', 'Қазақ әдебиеті', 'Алгебра', 'География', 'Физика', 'Орыс тілі'],
    2: ['Химия', 'Қазақ әдебиеті', 'Алгебра', 'Қаз тарихы', 'Физика', 'ДЖ тарих', 'Информатика'],
    3: ['Химия', 'Геометрия', 'Орыс тілі', 'Дене шыны', 'География', 'Ағылшын', 'Құқық'],
    4: ['Ағылшын', 'Алгебра', 'Геометрия', 'Биология', 'Информатика', 'БЭД'],
}

# ───────────────────────── тексты ─────────────────────────
# Новые тексты (меню, игры, ИИ, профиль) лежат в content.py → UI и подмешиваются сюда.
TEXT = {
    'ru': {
        'today': '🌤 Сегодня', 'tomorrow': '🌙 Завтра', 'schedule': '📚 Расписание',
        'homework_btn': '📝 Домашка', 'settings': '⚙️ Настройки', 'language': '🌐 Язык', 'back': '⬅️ Назад',
        'not_owner': '⛔ Этот бот настроен только для владельца.',
        'no_owner': '⚙️ OWNER_ID не задан. Отправь /id и впиши число в настройки (OWNER_ID).',
        'morning': '☀️ Доброе утро!',
        'weather_error': 'Погоду сейчас получить не удалось.',
        'rest': '🛋 После школы: сначала отдых и TikTok, потом поесть 😄',
        'homework': '📝 Домашку лучше делать вечером. Математику ставлю первой — она сложнее.',
        'day_off': '🛌 Выходной — уроков нет.',
        'sched_today': '📚 Расписание на сегодня\n\n', 'sched_tomorrow': '📚 Расписание на завтра\n\n',
        'tasks_title': '📝 Домашка и задачи\n\n',
        'no_tasks': '✅ Всё сделано, задач нет!',
        'add_usage': 'Напиши так:\n/add Алгебра №123\nили с датой:\n/add 25.10 Реферат по истории',
        'added': '➕ Добавлено!',
        'done_ok': '✅ Отмечено выполненным.', 'del_ok': '🗑 Удалено.',
        'bad_num': 'Укажи номер из списка, например: /done 2',
        's_morning': 'Утренняя сводка', 's_evening': 'Вечер: что завтра', 's_lessons': 'Напоминания об уроках',
        'cleared': '🧹 Память чата очищена.',
        'lesson_soon': '🔔 Через 10 минут урок!\n\n📚 {name}\n⏰ {start}–{end}',
        'evening': '🌙 Добрый вечер! Вот что завтра:',
        'fact': '🧠 Факт: ', 'motivation_btn': '💪 Мотивация', 'quote': '💪 ',
        'weather_btn': '🌦 Погода', 'forecast_btn': '📆 3 дня', 'mood_btn': '😊 Как день',
        'focus_btn': '🍅 Фокус 25', 'exam_btn': '⏳ ҰБТ', 'stats_btn': '📊 Статистика',
        'days': ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'],
        'mood_ask': '😊 Как прошёл твой день? Выбери смайлик:',
        'mood_reply': ['😞 Тяжёлый день бывает у всех. Отдохни, завтра будет легче.',
                       '😕 Не самый лучший день. Сделай вечером что-нибудь приятное.',
                       '😐 Обычный нормальный день. Это тоже хорошо.',
                       '🙂 Хороший день! Так держать.',
                       '😄 Отлично! Запомни, что сегодня получилось.'],
        'mood_note_ask': '✍️ Если хочешь, напиши одной строкой, что было главным за день (10 минут на ответ).',
        'note_saved': '📔 Записал в дневник.',
        'stats_title': '📊 Настроение за 7 дней\n\n', 'stats_empty': 'Пока нет записей. Вечером нажми «Как день».',
        'avg': 'Среднее', 's_mood': 'Вечерний вопрос «как день»',
        'focus_started': '🍅 Фокус на {m} мин. Убери телефон и работай. Напишу, когда время выйдет. Остановить: /focus off',
        'focus_done': '⏰ {m} минут прошли! Сделай перерыв 5 минут: встань, попей воды.',
        'focus_stopped': '🛑 Таймер остановлен.',
        'exam_none': '⏳ Дата экзамена не задана. Напиши так: /exam 20.06.2027',
        'exam_left': '⏳ До экзамена ({d}) осталось дней: {n}', 'exam_today': '🔥 Экзамен сегодня! Ты готов(а). Удачи!',
        'exam_passed': 'Дата экзамена уже прошла. Задай новую: /exam 20.06.2027',
        'exam_saved': '✅ Дата сохранена.', 'exam_off': '🗑 Дата удалена.',
        'hourly': 'По часам',
    },
    'kz': {
        'today': '🌤 Бүгін', 'tomorrow': '🌙 Ертең', 'schedule': '📚 Кесте',
        'homework_btn': '📝 Үй жұмысы', 'settings': '⚙️ Баптаулар', 'language': '🌐 Тіл', 'back': '⬅️ Артқа',
        'not_owner': '⛔ Бұл бот тек иесіне арналған.',
        'no_owner': '⚙️ OWNER_ID көрсетілмеген. /id жібер де, санды OWNER_ID-ге жаз.',
        'morning': '☀️ Қайырлы таң!',
        'weather_error': 'Қазір ауа райын алу мүмкін болмады.',
        'rest': '🛋 Мектептен кейін: алдымен демалып, TikTok көріп ал 😄',
        'homework': '📝 Үй тапсырмасын кешке жасаған дұрыс. Математиканы бірінші қойдым — ол қиындау.',
        'day_off': '🛌 Демалыс — сабақ жоқ.',
        'sched_today': '📚 Бүгінгі сабақ кестесі\n\n', 'sched_tomorrow': '📚 Ертеңгі сабақ кестесі\n\n',
        'tasks_title': '📝 Үй жұмысы мен тапсырмалар\n\n',
        'no_tasks': '✅ Бәрі дайын, тапсырма жоқ!',
        'add_usage': 'Былай жаз:\n/add Алгебра №123\nнемесе күнімен:\n/add 25.10 Тарихтан реферат',
        'added': '➕ Қосылды!',
        'done_ok': '✅ Орындалды деп белгіленді.', 'del_ok': '🗑 Өшірілді.',
        'bad_num': 'Тізімдегі нөмірді жаз, мысалы: /done 2',
        's_morning': 'Таңғы шолу', 's_evening': 'Кешке: ертең не бар', 's_lessons': 'Сабақ туралы еске салу',
        'cleared': '🧹 Чат жады тазаланды.',
        'lesson_soon': '🔔 10 минуттан кейін сабақ!\n\n📚 {name}\n⏰ {start}–{end}',
        'evening': '🌆 Қайырлы кеш! Ертең мынау болады:',
        'fact': '🧠 Дерек: ', 'motivation_btn': '💪 Мотивация', 'quote': '💪 ',
        'weather_btn': '🌦 Ауа райы', 'forecast_btn': '📆 3 күн', 'mood_btn': '😊 Күн қалай',
        'focus_btn': '🍅 Фокус 25', 'exam_btn': '⏳ ҰБТ', 'stats_btn': '📊 Статистика',
        'days': ['Дс', 'Сс', 'Сә', 'Бс', 'Жм', 'Сн', 'Жс'],
        'mood_ask': '😊 Күнің қалай өтті? Смайлик таңда:',
        'mood_reply': ['😞 Ауыр күн болады. Демал, ертең жеңілірек болады.',
                       '😕 Ең жақсы күн емес. Кешке өзіңе ұнайтын нәрсе жаса.',
                       '😐 Қалыпты, қарапайым күн. Бұл да жақсы.',
                       '🙂 Жақсы күн! Осылай жалғастыр.',
                       '😄 Тамаша! Бүгін не сәтті болғанын есте сақта.'],
        'mood_note_ask': '✍️ Қаласаң, күннің ең басты оқиғасын бір жолмен жаз (жауап беруге 10 минут).',
        'note_saved': '📔 Күнделікке жаздым.',
        'stats_title': '📊 7 күндегі көңіл-күй\n\n', 'stats_empty': 'Әзірге жазба жоқ. Кешке «Күн қалай» батырмасын бас.',
        'avg': 'Орташа', 's_mood': 'Кешкі «күн қалай» сұрағы',
        'focus_started': '🍅 {m} минут фокус. Телефонды қой да, жұмыс істе. Уақыт біткенде жазамын. Тоқтату: /focus off',
        'focus_done': '⏰ {m} минут өтті! 5 минут үзіліс жаса: тұр, су іш.',
        'focus_stopped': '🛑 Таймер тоқтатылды.',
        'exam_none': '⏳ Емтихан күні көрсетілмеген. Былай жаз: /exam 20.06.2027',
        'exam_left': '⏳ Емтиханға ({d}) қалған күн: {n}', 'exam_today': '🔥 Емтихан бүгін! Сен дайынсың. Сәттілік!',
        'exam_passed': 'Емтихан күні өтіп кетті. Жаңасын қой: /exam 20.06.2027',
        'exam_saved': '✅ Күн сақталды.', 'exam_off': '🗑 Күн өшірілді.',
        'hourly': 'Сағат бойынша',
    },
}
for _lang in TEXT:
    TEXT[_lang].update(C.UI[_lang])


def T(l, key):
    return TEXT[l][key]


def pick(bank, l):
    return random.choice(bank.get(l) or bank['ru'])


def daily(bank, l, salt=0):
    items = bank.get(l) or bank['ru']
    return items[(datetime.now(TZ).timetuple().tm_yday + salt) % len(items)]


def bar(value, total, width=10):
    filled = round(width * min(max(value, 0), total) / total) if total else 0
    return '▰' * filled + '▱' * (width - filled)


def today_str():
    return f'{datetime.now(TZ):%Y-%m-%d}'


def lang_idx(l):
    return 0 if l == 'ru' else 1


# ───────────────────────── база данных ─────────────────────────
def q(sql, args=()):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(sql, args).fetchall()
        con.commit()
        return rows
    finally:
        con.close()


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript('''
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY, lang TEXT DEFAULT 'ru',
            morning INTEGER DEFAULT 1, evening INTEGER DEFAULT 1, lessons INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, text TEXT, due TEXT, done INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS chat(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, role TEXT, content TEXT);
        CREATE TABLE IF NOT EXISTS moods(
            user_id INTEGER, day TEXT, score INTEGER, note TEXT, PRIMARY KEY(user_id, day));
        CREATE TABLE IF NOT EXISTS water(
            user_id INTEGER, day TEXT, glasses INTEGER DEFAULT 0, PRIMARY KEY(user_id, day));
        CREATE TABLE IF NOT EXISTS once(
            user_id INTEGER, day TEXT, key TEXT, PRIMARY KEY(user_id, day, key));
    ''')
    for col, ddl in (('mood', 'INTEGER DEFAULT 1'), ('exam', 'TEXT'),                # миграции для старой базы
                     ('water_on', 'INTEGER DEFAULT 1'), ('xp', 'INTEGER DEFAULT 0'),
                     ('streak', 'INTEGER DEFAULT 0'), ('last_active', 'TEXT'),
                     ('persona', "TEXT DEFAULT 'friend'"), ('bday', 'TEXT'),
                     ('focus_total', 'INTEGER DEFAULT 0'), ('name', 'TEXT')):
        try:
            con.execute(f'ALTER TABLE users ADD COLUMN {col} {ddl}')
        except sqlite3.OperationalError:
            pass
    con.commit()
    con.close()


def user(uid):
    q('INSERT OR IGNORE INTO users(id) VALUES(?)', (uid,))
    return q('SELECT * FROM users WHERE id=?', (uid,))[0]


def L(uid):
    return user(uid)['lang']


def toggle(uid, field):
    if field in ('morning', 'evening', 'lessons', 'mood', 'water_on'):
        user(uid)
        q(f'UPDATE users SET {field}=1-{field} WHERE id=?', (uid,))


def pending(uid):
    return q('SELECT * FROM tasks WHERE user_id=? AND done=0 ORDER BY due IS NULL, due, id', (uid,))


# ───────────────────────── XP, уровни, серия ─────────────────────────
def once(uid, key):
    """True, если действие сегодня выполняется впервые (защита от накрутки XP)."""
    day = today_str()
    if q('SELECT 1 FROM once WHERE user_id=? AND day=? AND key=?', (uid, day, key)):
        return False
    q('INSERT INTO once(user_id, day, key) VALUES(?,?,?)', (uid, day, key))
    return True


def level_info(xp):
    idx = 0
    for i, lvl in enumerate(C.LEVELS):
        if xp >= lvl[0]:
            idx = i
    nxt = C.LEVELS[idx + 1] if idx + 1 < len(C.LEVELS) else None
    return idx + 1, C.LEVELS[idx], nxt


def level_title(lvl, l):
    return lvl[2 + lang_idx(l)]


def reward(uid, n, l, key=None):
    """Начисляет XP. Возвращает текст-приписку ('' если награда за сегодня уже получена)."""
    if key and not once(uid, key):
        return ''
    old_xp = user(uid)['xp']
    q('UPDATE users SET xp=xp+? WHERE id=?', (n, uid))
    old_lvl, new = level_info(old_xp)[0], level_info(old_xp + n)
    note = f'\n\n⭐ +{n} XP'
    if new[0] > old_lvl:
        note += '\n' + T(l, 'level_up').format(lvl=new[0], icon=new[1][1], title=level_title(new[1], l))
    return note


def touch(uid, name=None):
    """Отмечает активность: серия дней подряд и +2 XP за первый заход за день."""
    u = user(uid)
    if name and name != u['name']:
        q('UPDATE users SET name=? WHERE id=?', (name[:40], uid))
    today = datetime.now(TZ).date()
    if u['last_active'] == today.isoformat():
        return
    last = date.fromisoformat(u['last_active']) if u['last_active'] else None
    streak = u['streak'] + 1 if last == today - timedelta(days=1) else 1
    q('UPDATE users SET last_active=?, streak=?, xp=xp+2 WHERE id=?', (today.isoformat(), streak, uid))


# ───────────────────────── задачи / домашка ─────────────────────────
def parse_task(args):
    text = ' '.join(args).strip()
    m = re.match(r'^(\d{1,2})\.(\d{1,2})\s+(.+)$', text)
    if not m:
        return text, None
    day, mon, rest = int(m[1]), int(m[2]), m[3]
    today = datetime.now(TZ).date()
    try:
        d = date(today.year, mon, day)
        if (today - d).days > 30:      # 05.01 в декабре = следующий год
            d = date(today.year + 1, mon, day)
    except ValueError:
        return text, None
    return rest, d.isoformat()


def parse_due(s):
    """ДД.ММ, ДД.ММ.ГГГГ или ГГГГ-ММ-ДД → ISO-дата или None (нужно для ИИ-инструментов)."""
    s = (s or '').strip()
    if not s:
        return None
    today = datetime.now(TZ).date()
    m = re.match(r'^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?$', s)
    try:
        if m:
            if m[3]:
                return date(int(m[3]), int(m[2]), int(m[1])).isoformat()
            d = date(today.year, int(m[2]), int(m[1]))
            if (today - d).days > 30:
                d = date(today.year + 1, int(m[2]), int(m[1]))
            return d.isoformat()
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def tasks_text(uid, l):
    rows = pending(uid)
    if not rows:
        return T(l, 'no_tasks')
    today = datetime.now(TZ).date()
    out = []
    for i, r in enumerate(rows, 1):
        extra = ''
        if r['due']:
            d = date.fromisoformat(r['due'])
            mark = '⚠️ ' if d < today else '🔥 ' if d == today else ''
            extra = f' — {mark}{d:%d.%m}'
        out.append(f'{i}. {r["text"]}{extra}')
    return T(l, 'tasks_title') + '\n'.join(out)


# ───────────────────────── расписание ─────────────────────────
def schedule_text(day, l, tomorrow=False):
    names = SCHEDULE.get(day)
    if not names:
        return T(l, 'day_off')
    title = T(l, 'sched_tomorrow' if tomorrow else 'sched_today')
    return title + '\n'.join(f'{i + 1}. {LESSON_TIMES[i][0]}–{LESSON_TIMES[i][1]} — {s}' for i, s in enumerate(names))


# ───────────────────────── погода ─────────────────────────
_wcache = {'t': 0.0, 'd': None}


def fetch_weather():
    if _wcache['d'] and time.monotonic() - _wcache['t'] < 600:
        return _wcache['d']
    r = requests.get('https://api.open-meteo.com/v1/forecast', params={
        'latitude': LAT, 'longitude': LON, 'timezone': str(TZ), 'forecast_days': 3,
        'current': 'temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code',
        'hourly': 'temperature_2m,precipitation_probability,weather_code',
        'daily': 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max'}, timeout=10)
    r.raise_for_status()
    _wcache.update(t=time.monotonic(), d=r.json())
    return _wcache['d']


def wmo(code):
    if code == 0:
        return '☀️', 'ясно', 'ашық'
    if code <= 3:
        return '⛅', 'облачно', 'бұлтты'
    if code in (45, 48):
        return '🌫', 'туман', 'тұман'
    if code <= 57:
        return '🌦', 'морось', 'себелеген жаңбыр'
    if code <= 67 or 80 <= code <= 82:
        return '🌧', 'дождь', 'жаңбыр'
    if code <= 77 or code in (85, 86):
        return '❄️', 'снег', 'қар'
    return '⛈', 'гроза', 'найзағай'


def clothes(mn, mx, rain, l):
    if mx < 0:
        ru, kz = 'пуховик, шапка, перчатки', 'қалың күртеше, бөрік, қолғап'
    elif mx < 10:
        ru, kz = 'тёплая куртка', 'жылы күрте'
    elif mx < 18 or mn < 8:
        ru, kz = 'куртка/толстовка', 'күрте/толстовка'
    elif mx < 25:
        ru, kz = 'футболка + лёгкая кофта', 'футболка + жеңіл кофта'
    else:
        ru, kz = 'футболка, лёгкая одежда, пей больше воды', 'футболка, жеңіл киім, су көбірек іш'
    if rain >= 50:
        ru += ' + ☂️ зонт'
        kz += ' + ☂️ қолшатыр'
    return ru if l == 'ru' else kz


def hourly_line(d, now):
    hourly = d['hourly']
    times = hourly['time']
    out = []
    for h in (8, 13, 18, 21):
        key = f'{now:%Y-%m-%d}T{h:02d}:00'
        if h < now.hour or key not in times:
            continue
        i = times.index(key)
        icon = wmo(hourly['weather_code'][i])[0]
        p = hourly['precipitation_probability'][i] or 0
        out.append(f'{h:02d}:00 {icon} {hourly["temperature_2m"][i]:.0f}°C 💧{p:.0f}%')
    return '\n'.join(out)


def weather(l='ru'):
    try:
        d = fetch_weather()
        now = datetime.now(TZ)
        c, day = d['current'], d['daily']
        mx, mn = day['temperature_2m_max'][0], day['temperature_2m_min'][0]
        rain = day['precipitation_probability_max'][0] or 0
        icon, ru_name, kz_name = wmo(c['weather_code'])
        cl = clothes(mn, mx, rain, l)
        t, feels = c['temperature_2m'], c['apparent_temperature']
        if l == 'ru':
            text = (f'{icon} {ru_name.capitalize()}, сейчас {t:.0f}°C (ощущается как {feels:.0f}°C)\n'
                    f'📈 Днём: {mx:.0f}°C | 🌙 Ночью: {mn:.0f}°C\n💨 Ветер: {c["wind_speed_10m"]:.0f} км/ч\n'
                    f'💧 Влажность: {c["relative_humidity_2m"]:.0f}%\n🌧 Осадки: {rain:.0f}%\n👕 Что надеть: {cl}')
        else:
            text = (f'{icon} {kz_name.capitalize()}, қазір {t:.0f}°C (сезіледі: {feels:.0f}°C)\n'
                    f'📈 Күндіз: {mx:.0f}°C | 🌙 Түнде: {mn:.0f}°C\n💨 Жел: {c["wind_speed_10m"]:.0f} км/сағ\n'
                    f'💧 Ылғалдылық: {c["relative_humidity_2m"]:.0f}%\n🌧 Жауын: {rain:.0f}%\n👕 Киім: {cl}')
        hourly = hourly_line(d, now)
        if hourly:
            text += f'\n\n🕒 {T(l, "hourly")}:\n{hourly}'
        return text
    except Exception:
        log.exception('weather failed')
        return T(l, 'weather_error')


def forecast_text(l):
    try:
        day = fetch_weather()['daily']
        names = T(l, 'days')
        lines = []
        for i, ds in enumerate(day['time']):
            dd = date.fromisoformat(ds)
            icon = wmo(day['weather_code'][i])[0]
            rain = day['precipitation_probability_max'][i] or 0
            lines.append(f'{names[dd.weekday()]} {dd:%d.%m} {icon} {day["temperature_2m_min"][i]:.0f}…'
                         f'{day["temperature_2m_max"][i]:.0f}°C 🌧{rain:.0f}%')
        return f'📆 {CITY}\n\n' + '\n'.join(lines)
    except Exception:
        log.exception('forecast failed')
        return T(l, 'weather_error')


# ───────────────────────── настроение, экзамен ─────────────────────────
MOOD_EMOJI = {1: '😞', 2: '😕', 3: '😐', 4: '🙂', 5: '😄'}


def save_mood(uid, score):
    day = today_str()
    q('INSERT INTO moods(user_id, day, score) VALUES(?,?,?) '
      'ON CONFLICT(user_id, day) DO UPDATE SET score=excluded.score', (uid, day, score))


def save_note(uid, note):
    q('UPDATE moods SET note=? WHERE user_id=? AND day=?', (note[:100], uid, today_str()))


def stats_text(uid, l):
    today = datetime.now(TZ).date()
    rows = {r['day']: r for r in q('SELECT day, score, note FROM moods WHERE user_id=? AND day>=?',
                                   (uid, (today - timedelta(days=6)).isoformat()))}
    if not rows:
        return T(l, 'stats_empty')
    names, lines, scores = T(l, 'days'), [], []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        r = rows.get(d.isoformat())
        head = f'{names[d.weekday()]} {d:%d.%m}'
        if r:
            scores.append(r['score'])
            lines.append(f'{head} {MOOD_EMOJI[r["score"]]}' + (f' — {r["note"]}' if r['note'] else ''))
        else:
            lines.append(f'{head} —')
    return T(l, 'stats_title') + '\n'.join(lines) + f'\n\n{T(l, "avg")}: {sum(scores) / len(scores):.1f}/5'


def mood_keyboard(l):
    return Markup([[Btn(MOOD_EMOJI[i], callback_data=f'mood_{i}') for i in range(1, 6)],
                   [Btn(T(l, 'stats_btn'), callback_data='stats'), Btn(T(l, 'menu'), callback_data='home')]])


def parse_date(text):
    for fmt in ('%d.%m.%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    return None


def exam_days_left(uid):
    e = user(uid)['exam']
    return (date.fromisoformat(e) - datetime.now(TZ).date()).days if e else None


def exam_text(uid, l):
    left = exam_days_left(uid)
    if left is None:
        return T(l, 'exam_none')
    if left < 0:
        return T(l, 'exam_passed')
    if left == 0:
        return T(l, 'exam_today')
    return T(l, 'exam_left').format(n=left, d=f'{date.fromisoformat(user(uid)["exam"]):%d.%m.%Y}')


# ───────────────────────── вода ─────────────────────────
def water_today(uid):
    rows = q('SELECT glasses FROM water WHERE user_id=? AND day=?', (uid, today_str()))
    return rows[0]['glasses'] if rows else 0


def set_water(uid, delta):
    new = max(0, min(30, water_today(uid) + delta))
    q('INSERT INTO water(user_id, day, glasses) VALUES(?,?,?) '
      'ON CONFLICT(user_id, day) DO UPDATE SET glasses=excluded.glasses', (uid, today_str(), new))
    return new


def water_goal_note(uid, l, before, after):
    """Если норма воды только что достигнута — поздравление и XP (один раз в день)."""
    if before < WATER_GOAL <= after:
        return '\n\n' + T(l, 'water_goal_done') + reward(uid, 10, l, key='water_goal')
    return ''


def water_view(uid, l):
    n = water_today(uid)
    text = f'{T(l, "water_title")}\n\n{bar(n, WATER_GOAL, 8)} {n}/{WATER_GOAL}\n\n{pick(C.WATER_TIPS, l)}'
    kb = Markup([[Btn('💧 +1', callback_data='water_add_1'), Btn('💧💧 +2', callback_data='water_add_2'),
                  Btn('➖ −1', callback_data='water_sub')],
                 [Btn(T(l, 'life_btn'), callback_data='life'), Btn(T(l, 'menu'), callback_data='home')]])
    return text, kb


# ───────────────────────── гороскоп, день рождения ─────────────────────────
def zodiac_index(month, day):
    idx = 9    # до 20 января — Козерог
    for m, d, i in C.SIGN_STARTS:
        if (month, day) >= (m, d):
            idx = i
    return idx


def horoscope_text(i, l):
    now = datetime.now(TZ)
    rng = random.Random(f'{now:%Y-%m-%d}-{i}')       # один знак = один и тот же прогноз весь день
    icon, ru_name, kz_name = C.SIGNS[i]
    sign = f'{icon} {ru_name if l == "ru" else kz_name}'
    colors = C.HORO_COLORS.get(l) or C.HORO_COLORS['ru']
    lines = [T(l, 'horo_head').format(d=f'{now:%d.%m}', s=sign), '',
             rng.choice(C.HORO_OPEN.get(l) or C.HORO_OPEN['ru']), '']
    for key in ('horo_love', 'horo_study', 'horo_luck'):
        v = rng.randint(40, 100)
        lines.append(f'{T(l, key)}  {bar(v, 100)} {v}%')
    lines += ['', f'{T(l, "horo_color")}: {rng.choice(colors)}   {T(l, "horo_num")}: {rng.randint(1, 99)}',
              f'💡 {rng.choice(C.HORO_ADVICE.get(l) or C.HORO_ADVICE["ru"])}', '', T(l, 'horo_note')]
    return '\n'.join(lines)


def horo_pick_keyboard(l):
    names = [f'{s[0]} {s[1 + lang_idx(l)]}' for s in C.SIGNS]
    rows = [[Btn(names[r * 3 + c], callback_data=f'hs_{r * 3 + c}') for c in range(3)] for r in range(4)]
    rows.append([Btn(T(l, 'fun_btn'), callback_data='fun'), Btn(T(l, 'menu'), callback_data='home')])
    return Markup(rows)


def safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:      # 29 февраля в невисокосный год
        return date(year, 3, 1)


def bday_info(uid):
    """(индекс знака, дней до дня рождения, 'ДД.ММ') или None."""
    b = user(uid)['bday']
    if not b:
        return None
    month, day = map(int, b.split('-'))
    today = datetime.now(TZ).date()
    nxt = safe_date(today.year, month, day)
    if nxt < today:
        nxt = safe_date(today.year + 1, month, day)
    return zodiac_index(month, day), (nxt - today).days, f'{day:02d}.{month:02d}'


# ───────────────────────── профиль ─────────────────────────
def profile_text(uid, l):
    u = user(uid)
    lvl, cur, nxt = level_info(u['xp'])
    lines = [T(l, 'p_title') + (f' — {u["name"]}' if u['name'] else ''), '',
             f'{cur[1]} ' + T(l, 'p_level').format(lvl=lvl, title=level_title(cur, l)),
             f'⭐ {u["xp"]} XP']
    if nxt:
        span = nxt[0] - cur[0]
        lines.append(f'{bar(u["xp"] - cur[0], span)} ' +
                     T(l, 'p_next').format(title=level_title(nxt, l), n=nxt[0] - u['xp']))
    else:
        lines.append(T(l, 'p_max'))
    done = q('SELECT COUNT(*) AS c FROM tasks WHERE user_id=? AND done=1', (uid,))[0]['c']
    mood = q('SELECT AVG(score) AS a FROM moods WHERE user_id=?', (uid,))[0]['a']
    glasses = water_today(uid)
    lines += ['', T(l, 'p_streak').format(n=u['streak']), T(l, 'p_done').format(n=done),
              T(l, 'p_focus').format(n=u['focus_total']),
              f'{T(l, "p_water")}: {bar(glasses, WATER_GOAL, 8)} {glasses}/{WATER_GOAL}']
    if mood is not None:
        lines.append(T(l, 'p_mood').format(a=f'{mood:.1f}'))
    persona = C.PERSONAS.get(u['persona']) or C.PERSONAS['friend']
    lines.append(T(l, 'p_mode').format(m=f'{persona["icon"]} {persona[l]}'))
    info = bday_info(uid)
    if info:
        lines.append(T(l, 'p_bday').format(s=C.SIGNS[info[0]][0], d=info[2], n=info[1]))
    return '\n'.join(lines)


# ───────────────────────── сообщения и клавиатуры ─────────────────────────
def greeting(l):
    h = datetime.now(TZ).hour
    return T(l, 'greet')[0 if h < 5 else 1 if h < 12 else 2 if h < 18 else 3]


def today_message(uid, l):
    now = datetime.now(TZ)
    parts = [greeting(l)]
    holiday = C.HOLIDAYS.get((now.month, now.day))
    if holiday:
        parts.append(T(l, 'holiday') + holiday[lang_idx(l)])
    info = bday_info(uid)
    if info and info[1] == 0:
        parts.append(T(l, 'bday_today'))
    elif info and info[1] <= 7:
        parts.append(T(l, 'bday_in').format(n=info[1]))
    parts += [f'📍 {CITY}\n{weather(l)}', schedule_text(now.weekday(), l)]
    if pending(uid):
        parts.append(tasks_text(uid, l))
        if SCHEDULE.get(now.weekday()):
            parts.append(T(l, 'homework'))
    if SCHEDULE.get(now.weekday()):
        parts.append(T(l, 'rest'))
    if (exam_days_left(uid) or -1) >= 0:
        parts.append(exam_text(uid, l))
    parts.append(T(l, 'quote') + pick(C.QUOTES, l))
    parts.append(T(l, 'fact') + daily(C.FACTS, l))
    return '\n\n'.join(parts)


def tomorrow_message(uid, l):
    wd = (datetime.now(TZ) + timedelta(days=1)).weekday()
    text = schedule_text(wd, l, tomorrow=True)
    if pending(uid):
        text += '\n\n' + tasks_text(uid, l)
    return text


def main_rows(l):
    return [
        [Btn(T(l, 'today'), callback_data='today'), Btn(T(l, 'tomorrow'), callback_data='tomorrow')],
        [Btn(T(l, 'schedule'), callback_data='schedule'), Btn(T(l, 'homework_btn'), callback_data='tasks')],
        [Btn(T(l, 'weather_btn'), callback_data='weather'), Btn(T(l, 'mood_btn'), callback_data='mood')],
        [Btn(T(l, 'fun_btn'), callback_data='fun'), Btn(T(l, 'life_btn'), callback_data='life'),
         Btn(T(l, 'study_btn'), callback_data='study')],
        [Btn(T(l, 'ai_btn'), callback_data='ai'), Btn(T(l, 'profile_btn'), callback_data='profile'),
         Btn(T(l, 'settings'), callback_data='settings')]]


def keyboard(l, *extra_rows):
    return Markup([*extra_rows, *main_rows(l)])


def ai_keyboard(l):
    return Markup([[Btn(T(l, 'ai_simple_btn'), callback_data='ai_simple'),
                    Btn(T(l, 'ai_more_btn'), callback_data='ai_more')],
                   [Btn(T(l, 'menu'), callback_data='home')]])


def lang_keyboard():
    return Markup([[Btn('🇷🇺 Русский', callback_data='lang_ru'), Btn('🇰🇿 Қазақша', callback_data='lang_kz')]])


def settings_keyboard(uid, l):
    u = user(uid)
    rows = [[Btn(('✅ ' if u[k] else '❌ ') + T(l, label), callback_data='toggle_' + k)]
            for k, label in (('morning', 's_morning'), ('evening', 's_evening'), ('lessons', 's_lessons'),
                             ('mood', 's_mood'), ('water_on', 's_water'))]
    rows.append([Btn(T(l, 'language'), callback_data='language'), Btn(T(l, 'menu'), callback_data='home')])
    return Markup(rows)


# подменю: (подпись кнопки, действие)
SUBMENUS = {
    'fun': ('fun_title', [('dice_btn', 'dice'), ('ball_btn', 'ball'), ('coin_btn', 'coin'), ('joke_btn', 'joke'),
                          ('riddle_btn', 'riddle'), ('horo_btn', 'horoscope'), ('wyr_btn', 'wyr'),
                          ('challenge_btn', 'challenge'), ('compliment_btn', 'compliment')]),
    'life': ('life_title', [('water_btn', 'water'), ('breath_btn', 'breath'), ('eyes_btn', 'eyes'),
                            ('sleep_btn', 'sleep'), ('fact_btn', 'fact'), ('advice_btn', 'advice'),
                            ('motivation_btn', 'motivation'), ('bored_btn', 'bored')]),
    'study': ('study_title', [('quiz_btn', 'quiz'), ('word_btn', 'word'), ('focus_btn', 'focus'),
                              ('exam_btn', 'exam'), ('hack_btn', 'hack'), ('photo_btn', 'photo_info'),
                              ('homework_btn', 'tasks'), ('profile_btn', 'profile')]),
}

# карточки «одна порция контента + кнопка Ещё» и к какому подменю они относятся
CARD_PARENT = {'joke': 'fun', 'ball': 'fun', 'coin': 'fun', 'wyr': 'fun', 'compliment': 'fun',
               'advice': 'life', 'bored': 'life', 'fact': 'life', 'motivation': 'life', 'hack': 'study'}
STATIC_PAGES = {'breath': C.BREATH, 'eyes': C.EYES, 'sleep': C.SLEEP}
DICE = {'d': '🎲', 't': '🎯', 'b': '🏀', 'f': '⚽', 'w': '🎳', 's': '🎰'}


def submenu_keyboard(kind, l):
    _, items = SUBMENUS[kind]
    rows = [[Btn(T(l, items[i][0]), callback_data=items[i][1])] + (
        [Btn(T(l, items[i + 1][0]), callback_data=items[i + 1][1])] if i + 1 < len(items) else [])
        for i in range(0, len(items), 2)]
    rows.append([Btn(T(l, 'menu'), callback_data='home')])
    return Markup(rows)


def card_text(kind, l):
    if kind == 'joke':
        return '😂 ' + pick(C.JOKES, l)
    if kind == 'ball':
        return T(l, 'ball_head') + pick(C.BALL, l) + T(l, 'ball_hint')
    if kind == 'coin':
        return T(l, 'coin_head') + random.choice(T(l, 'coin'))
    if kind == 'wyr':
        return '🤔 ' + pick(C.WYR, l)
    if kind == 'compliment':
        return '💌 ' + pick(C.COMPLIMENTS, l)
    if kind == 'advice':
        return '🧭 ' + pick(C.ADVICE, l)
    if kind == 'bored':
        return '🥱 ' + T(l, 'bored_head') + pick(C.BORED, l)
    if kind == 'fact':
        return T(l, 'fact') + pick(C.FACTS, l)
    if kind == 'motivation':
        return T(l, 'quote') + pick(C.QUOTES, l)
    return '💡 ' + pick(C.HACKS, l)


def card_keyboard(kind, l):
    parent = CARD_PARENT[kind]
    return Markup([[Btn(T(l, 'more'), callback_data=kind), Btn(T(l, parent + '_btn'), callback_data=parent)],
                   [Btn(T(l, 'menu'), callback_data='home')]])


def riddle_view(l):
    i = random.randrange(len(C.RIDDLES))
    kb = Markup([[Btn(T(l, 'riddle_answer_btn'), callback_data=f'rd_{i}')],
                 [Btn(T(l, 'more'), callback_data='riddle'), Btn(T(l, 'fun_btn'), callback_data='fun')],
                 [Btn(T(l, 'menu'), callback_data='home')]])
    return T(l, 'riddle_head') + C.RIDDLES[i]['q'], kb


def riddle_answer(i, l):
    item = C.RIDDLES[i]
    kb = Markup([[Btn(T(l, 'more'), callback_data='riddle'), Btn(T(l, 'fun_btn'), callback_data='fun')],
                 [Btn(T(l, 'menu'), callback_data='home')]])
    return f'{T(l, "riddle_head")}{item["q"]}\n\n{T(l, "riddle_answer")}{item["a"]}', kb


def challenge_view(l, fresh=False):
    text = (pick(C.CHALLENGES, l) if fresh else daily(C.CHALLENGES, l))
    kb = Markup([[Btn(T(l, 'done_btn'), callback_data='chal_done')],
                 [Btn(T(l, 'other_btn'), callback_data='challenge_new'), Btn(T(l, 'fun_btn'), callback_data='fun')],
                 [Btn(T(l, 'menu'), callback_data='home')]])
    return T(l, 'chal_head') + text, kb


def quiz_view(l):
    i = random.randrange(len(C.QUIZ))
    item = C.QUIZ[i]
    rows = [[Btn(f'{"ABCD"[k]}) {opt}', callback_data=f'qz_{i}_{k}')] for k, opt in enumerate(item['a'])]
    rows.append([Btn(T(l, 'study_btn'), callback_data='study'), Btn(T(l, 'menu'), callback_data='home')])
    return f'🎓 {T(l, "quiz_title")}\n\n{item["q"]}', Markup(rows)


def quiz_answer(uid, l, i, k):
    item = C.QUIZ[i]
    ok = k == item['c']
    head = ('✅ ' + T(l, 'quiz_ok')) if ok else ('❌ ' + T(l, 'quiz_no').format(a=item['a'][item['c']]))
    text = f'🎓 {item["q"]}\n\n{head}'
    if item.get('e'):
        text += f'\n💡 {item["e"]}'
    if ok:
        text += reward(uid, 5, l)
    kb = Markup([[Btn(T(l, 'quiz_next'), callback_data='quiz')],
                 [Btn(T(l, 'study_btn'), callback_data='study'), Btn(T(l, 'menu'), callback_data='home')]])
    return text, kb


def word_view(l, fresh=False):
    en, ru, kz, example = random.choice(C.WORDS) if fresh else C.WORDS[datetime.now(TZ).timetuple().tm_yday % len(C.WORDS)]
    text = f'{T(l, "word_head")}🇬🇧 {en} — {ru if l == "ru" else kz}\n💬 {example}{T(l, "word_tail")}'
    kb = Markup([[Btn(T(l, 'more'), callback_data='word_more'), Btn(T(l, 'study_btn'), callback_data='study')],
                 [Btn(T(l, 'menu'), callback_data='home')]])
    return text, kb


def ai_menu_view(uid, l):
    u = user(uid)
    cur = C.PERSONAS.get(u['persona']) or C.PERSONAS['friend']
    text = T(l, 'ai_title').format(icon=cur['icon'], name=cur[l], desc=cur['desc_' + l])
    if not ANTHROPIC_API_KEY:
        text = T(l, 'ai_off') + '\n\n' + text
    rows = [[Btn(('✅ ' if key == u['persona'] else '') + f'{p["icon"]} {p[l]}', callback_data=f'persona_{key}')]
            for key, p in C.PERSONAS.items()]
    rows = [rows[i] + (rows[i + 1] if i + 1 < len(rows) else []) for i in range(0, len(rows), 2)]
    rows.append([Btn(T(l, 'clear_btn'), callback_data='clear'), Btn(T(l, 'menu'), callback_data='home')])
    return text, Markup(rows)


def back_keyboard(l, parent):
    return Markup([[Btn(T(l, parent + '_btn'), callback_data=parent), Btn(T(l, 'menu'), callback_data='home')]])


def view(kind, uid, l):
    """Возвращает (текст, клавиатура). Вызывается в отдельном потоке — внутри есть сетевые запросы."""
    now = datetime.now(TZ)
    if kind == 'today':
        return today_message(uid, l), keyboard(l)
    if kind == 'schedule':
        return schedule_text(now.weekday(), l), keyboard(l)
    if kind == 'tomorrow':
        return tomorrow_message(uid, l), keyboard(l)
    if kind == 'tasks':
        return tasks_text(uid, l), keyboard(l)
    if kind == 'weather':
        return f'📍 {CITY}\n{weather(l)}', keyboard(l, [Btn(T(l, 'forecast_btn'), callback_data='forecast')])
    if kind == 'forecast':
        return forecast_text(l), keyboard(l, [Btn(T(l, 'weather_btn'), callback_data='weather')])
    if kind == 'mood':
        return T(l, 'mood_ask'), mood_keyboard(l)
    if kind == 'stats':
        return stats_text(uid, l), mood_keyboard(l)
    if kind == 'exam':
        return exam_text(uid, l), keyboard(l)
    if kind == 'settings':
        return T(l, 'settings_text'), settings_keyboard(uid, l)
    if kind == 'language':
        return '🌐 Выбери язык / Тілді таңда:', lang_keyboard()
    if kind == 'profile':
        return profile_text(uid, l), keyboard(l)
    if kind == 'help':
        return T(l, 'help'), keyboard(l)
    if kind in SUBMENUS:
        return T(l, SUBMENUS[kind][0]), submenu_keyboard(kind, l)
    if kind in CARD_PARENT:
        return card_text(kind, l), card_keyboard(kind, l)
    if kind in STATIC_PAGES:
        return STATIC_PAGES[kind].get(l) or STATIC_PAGES[kind]['ru'], back_keyboard(l, 'life')
    if kind == 'dice':
        names = list(DICE.items())
        rows = [[Btn(e, callback_data=f'dice_{k}') for k, e in names[:3]], [Btn(e, callback_data=f'dice_{k}') for k, e in names[3:]],
                [Btn(T(l, 'fun_btn'), callback_data='fun'), Btn(T(l, 'menu'), callback_data='home')]]
        return T(l, 'dice_title'), Markup(rows)
    if kind == 'riddle':
        return riddle_view(l)
    if kind == 'challenge':
        return challenge_view(l)
    if kind == 'challenge_new':
        return challenge_view(l, fresh=True)
    if kind == 'quiz':
        return quiz_view(l)
    if kind == 'word':
        return word_view(l)
    if kind == 'word_more':
        return word_view(l, fresh=True)
    if kind == 'water':
        return water_view(uid, l)
    if kind == 'ai':
        return ai_menu_view(uid, l)
    if kind == 'photo_info':
        return T(l, 'photo_info'), back_keyboard(l, 'study')
    if kind == 'horoscope':
        info = bday_info(uid)
        if info:
            return horoscope_text(info[0], l), horo_pick_keyboard(l)
        return T(l, 'horo_pick'), horo_pick_keyboard(l)
    return T(l, 'welcome'), keyboard(l)


# ───────────────────────── доступ ─────────────────────────
async def allowed(update: Update):
    uid = update.effective_user.id
    if OWNER_ID is None:
        await update.effective_message.reply_text(T('ru', 'no_owner'))
        return False
    if uid != OWNER_ID:
        await update.effective_message.reply_text(T('ru', 'not_owner'))
        return False
    await asyncio.to_thread(touch, uid, update.effective_user.first_name)
    return True


# ───────────────────────── команды ─────────────────────────
def view_command(kind):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await allowed(update):
            return
        uid = update.effective_user.id
        text, kb = await asyncio.to_thread(view, kind, uid, L(uid))
        await update.message.reply_text(text, reply_markup=kb)
    return handler


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f'🆔 Твой Telegram ID: {update.effective_user.id}')


async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await allowed(update):
        return
    uid = update.effective_user.id
    l = L(uid)
    text, due = parse_task(context.args)
    if not text:
        return await update.message.reply_text(T(l, 'add_usage'))
    q('INSERT INTO tasks(user_id,text,due) VALUES(?,?,?)', (uid, text, due))
    await update.message.reply_text(T(l, 'added') + '\n\n' + tasks_text(uid, l), reply_markup=keyboard(l))


def task_command(action):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await allowed(update):
            return
        uid = update.effective_user.id
        l = L(uid)
        rows = pending(uid)
        try:
            nums = {int(a) for a in context.args}
            assert nums and all(1 <= n <= len(rows) for n in nums)
        except (ValueError, AssertionError):
            return await update.message.reply_text(T(l, 'bad_num'))
        for n in nums:
            tid = rows[n - 1]['id']
            if action == 'done':
                q('UPDATE tasks SET done=1 WHERE id=?', (tid,))
            else:
                q('DELETE FROM tasks WHERE id=?', (tid,))
        note = reward(uid, 10 * len(nums), l) if action == 'done' else ''
        await update.message.reply_text(T(l, 'done_ok' if action == 'done' else 'del_ok') + '\n\n' + tasks_text(uid, l) + note,
                                        reply_markup=keyboard(l))
    return handler


def focus_cancel(context, uid):
    stopped = False
    for j in context.job_queue.get_jobs_by_name(f'focus_{uid}'):
        j.schedule_removal()
        stopped = True
    return stopped


async def focus_done(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    uid = d['uid']
    q('UPDATE users SET focus_total=focus_total+1 WHERE id=?', (uid,))
    await context.bot.send_message(context.job.chat_id,
                                   T(d['l'], 'focus_done').format(m=d['minutes']) + reward(uid, 15, d['l']))


def focus_start(context, uid, chat_id, minutes, l):
    focus_cancel(context, uid)
    context.job_queue.run_once(focus_done, minutes * 60, chat_id=chat_id,
                               data={'l': l, 'minutes': minutes, 'uid': uid}, name=f'focus_{uid}')
    return T(l, 'focus_started').format(m=minutes)


async def focus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await allowed(update):
        return
    uid = update.effective_user.id
    l = L(uid)
    arg = context.args[0] if context.args else '25'
    if arg.lower() in ('off', 'stop', '0'):
        focus_cancel(context, uid)
        return await update.message.reply_text(T(l, 'focus_stopped'), reply_markup=keyboard(l))
    minutes = min(max(int(arg), 5), 120) if arg.isdigit() else 25
    await update.message.reply_text(focus_start(context, uid, update.effective_chat.id, minutes, l),
                                    reply_markup=keyboard(l))


async def exam_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await allowed(update):
        return
    uid = update.effective_user.id
    l = L(uid)
    if context.args:
        arg = context.args[0]
        if arg.lower() == 'off':
            q('UPDATE users SET exam=NULL WHERE id=?', (uid,))
            return await update.message.reply_text(T(l, 'exam_off'), reply_markup=keyboard(l))
        d = parse_date(arg)
        if not d:
            return await update.message.reply_text(T(l, 'exam_none'))
        q('UPDATE users SET exam=? WHERE id=?', (d.isoformat(), uid))
        return await update.message.reply_text(T(l, 'exam_saved') + '\n' + exam_text(uid, l), reply_markup=keyboard(l))
    await update.message.reply_text(exam_text(uid, l), reply_markup=keyboard(l))


async def clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await allowed(update):
        return
    uid = update.effective_user.id
    q('DELETE FROM chat WHERE user_id=?', (uid,))
    await update.message.reply_text(T(L(uid), 'cleared'))


async def choose_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await allowed(update):
        return
    l = L(update.effective_user.id)
    options = [o.strip() for o in re.split(r',|;|\||\s+или\s+|\s+немесе\s+', ' '.join(context.args)) if o.strip()]
    if len(options) < 2:
        return await update.message.reply_text(T(l, 'choose_usage'))
    await update.message.reply_text(T(l, 'choose_pick').format(x=random.choice(options)))


async def random_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await allowed(update):
        return
    l = L(update.effective_user.id)
    nums = [int(a) for a in context.args if re.fullmatch(r'-?\d{1,9}', a)]
    a, b = (1, 100) if not nums else (1, nums[0]) if len(nums) == 1 else (nums[0], nums[1])
    a, b = min(a, b), max(a, b)
    await update.message.reply_text(T(l, 'random_res').format(a=a, b=b, n=random.randint(a, b)))


async def bday_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await allowed(update):
        return
    uid = update.effective_user.id
    l = L(uid)
    arg = context.args[0] if context.args else ''
    if arg.lower() == 'off':
        q('UPDATE users SET bday=NULL WHERE id=?', (uid,))
        return await update.message.reply_text(T(l, 'bday_off'), reply_markup=keyboard(l))
    m = re.match(r'^(\d{1,2})\.(\d{1,2})', arg)
    try:
        d = date(2000, int(m[2]), int(m[1]))     # 2000 — високосный год, чтобы принять 29.02
    except (TypeError, ValueError):
        return await update.message.reply_text(T(l, 'bday_usage'))
    q('UPDATE users SET bday=? WHERE id=?', (f'{d.month:02d}-{d.day:02d}', uid))
    await update.message.reply_text(T(l, 'bday_saved') + '\n\n' + profile_text(uid, l), reply_markup=keyboard(l))


# ───────────────────────── кнопки ─────────────────────────
async def edit(query, text, kb):
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except BadRequest as e:
        if 'not modified' not in str(e).lower():
            raise


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    qy = update.callback_query
    await qy.answer()
    uid = qy.from_user.id
    if uid != OWNER_ID:
        return
    await asyncio.to_thread(touch, uid, qy.from_user.first_name)
    data = qy.data
    l = L(uid)
    chat_id = qy.message.chat_id
    if data == 'focus':
        return await edit(qy, focus_start(context, uid, chat_id, 25, l), keyboard(l))
    if data.startswith('mood_'):
        score = int(data[5:])
        save_mood(uid, score)
        note = reward(uid, 5, l, key='mood')
        context.user_data['note_until'] = time.monotonic() + 600
        return await edit(qy, T(l, 'mood_reply')[score - 1] + '\n\n' + T(l, 'mood_note_ask') + note, keyboard(l))
    if data.startswith('dice_'):
        return await context.bot.send_dice(chat_id, emoji=DICE.get(data[5:], '🎲'))
    if data.startswith('water_'):
        delta = {'water_add_1': 1, 'water_add_2': 2, 'water_sub': -1}.get(data, 0)
        note = ''
        if delta:
            before = water_today(uid)
            note = water_goal_note(uid, l, before, set_water(uid, delta))
        text, kb = water_view(uid, l)
        return await edit(qy, text + note, kb)
    if data == 'chal_done':
        note = reward(uid, 10, l, key='challenge')
        text = (T(l, 'chal_done_ok') + note) if note else T(l, 'chal_done_again')
        return await edit(qy, text, Markup([[Btn(T(l, 'fun_btn'), callback_data='fun'), Btn(T(l, 'menu'), callback_data='home')]]))
    if data.startswith('rd_'):
        text, kb = riddle_answer(int(data[3:]), l)
        return await edit(qy, text, kb)
    if data.startswith('qz_'):
        _, i, k = data.split('_')
        text, kb = quiz_answer(uid, l, int(i), int(k))
        return await edit(qy, text, kb)
    if data.startswith('hs_'):
        return await edit(qy, horoscope_text(int(data[3:]), l), horo_pick_keyboard(l))
    if data.startswith('persona_'):
        key = data[8:]
        if key in C.PERSONAS:
            q('UPDATE users SET persona=? WHERE id=?', (key, uid))
        text, kb = ai_menu_view(uid, l)
        return await edit(qy, text, kb)
    if data == 'clear':
        q('DELETE FROM chat WHERE user_id=?', (uid,))
        return await edit(qy, T(l, 'cleared'), keyboard(l))
    if data in ('ai_simple', 'ai_more'):
        if not ANTHROPIC_API_KEY:
            return await edit(qy, T(l, 'no_ai'), keyboard(l))
        prompt = T(l, data)
        return await respond_ai(context, chat_id, uid, l, prompt, prompt)
    if data.startswith('lang_'):
        user(uid)
        q('UPDATE users SET lang=? WHERE id=?', ('kz' if data == 'lang_kz' else 'ru', uid))
        kind = 'home'
    elif data.startswith('toggle_'):
        toggle(uid, data[7:])
        kind = 'settings'
    else:
        kind = data
    text, kb = await asyncio.to_thread(view, kind, uid, L(uid))
    await edit(qy, text, kb)


# ───────────────────────── ИИ-агент (необязательно) ─────────────────────────
AI_TOOLS = [
    {'name': 'add_task', 'description': 'Добавить домашку или задачу в список пользователя.',
     'input_schema': {'type': 'object', 'properties': {
         'text': {'type': 'string', 'description': 'Текст задачи, например «Алгебра №15»'},
         'due': {'type': 'string', 'description': 'Срок: ДД.ММ или ГГГГ-ММ-ДД. Необязательно.'}}, 'required': ['text']}},
    {'name': 'list_tasks', 'description': 'Показать текущие невыполненные задачи с номерами.',
     'input_schema': {'type': 'object', 'properties': {}}},
    {'name': 'complete_task', 'description': 'Отметить задачу выполненной по номеру из списка задач.',
     'input_schema': {'type': 'object', 'properties': {'number': {'type': 'integer'}}, 'required': ['number']}},
    {'name': 'delete_task', 'description': 'Удалить задачу по номеру из списка задач.',
     'input_schema': {'type': 'object', 'properties': {'number': {'type': 'integer'}}, 'required': ['number']}},
    {'name': 'set_exam_date', 'description': 'Задать дату экзамена (ҰБТ) для обратного отсчёта или удалить её.',
     'input_schema': {'type': 'object', 'properties': {
         'date': {'type': 'string', 'description': 'ДД.ММ.ГГГГ или слово off, чтобы удалить'}}, 'required': ['date']}},
    {'name': 'start_focus', 'description': 'Запустить фокус-таймер (помодоро) на указанное число минут.',
     'input_schema': {'type': 'object', 'properties': {
         'minutes': {'type': 'integer', 'description': 'От 5 до 120, по умолчанию 25'}}}},
    {'name': 'add_water', 'description': 'Записать выпитые стаканы воды за сегодня.',
     'input_schema': {'type': 'object', 'properties': {'glasses': {'type': 'integer', 'description': 'По умолчанию 1'}}}},
    {'name': 'get_weather', 'description': 'Получить актуальную погоду в городе пользователя.',
     'input_schema': {'type': 'object', 'properties': {}}},
    {'name': 'get_schedule', 'description': 'Получить расписание уроков.',
     'input_schema': {'type': 'object', 'properties': {
         'day': {'type': 'string', 'enum': ['today', 'tomorrow', 'week']}}, 'required': ['day']}},
]


class AIError(Exception):
    def __init__(self, status, message):
        super().__init__(f'{status}: {message}')
        self.status, self.message = status, message


def ai_error_key(e):
    if isinstance(e, AIError):
        if e.status in (401, 403):
            return 'ai_error_key'
        if e.status == 402 or 'credit' in e.message.lower():
            return 'ai_error_credit'
        if e.status == 404:
            return 'ai_error_model'
        if e.status in (429, 529):
            return 'ai_error_busy'
    return 'ai_error'


def anthropic_call(system, messages, max_tokens=1024):
    payload = {'model': AI_MODEL, 'max_tokens': max_tokens, 'system': system, 'messages': messages, 'tools': AI_TOOLS}
    headers = {'x-api-key': ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'}
    for attempt in range(3):
        r = requests.post(AI_URL, headers=headers, json=payload, timeout=60)
        if r.status_code in (429, 500, 502, 503, 529) and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code >= 400:
            try:
                msg = r.json().get('error', {}).get('message', '')
            except ValueError:
                msg = r.text[:200]
            log.error('Anthropic API %s: %s', r.status_code, msg)
            raise AIError(r.status_code, msg)
        return r.json()


def clean_md(text):
    """Telegram показывает простой текст, поэтому убираем markdown-разметку."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.S)
    text = re.sub(r'__(.+?)__', r'\1', text, flags=re.S)
    text = re.sub(r'(?m)^\s{0,3}#{1,6}\s*', '', text)
    text = re.sub(r'(?m)^(\s*)[-*]\s+', r'\1• ', text)
    return text.replace('`', '').strip()


def build_system(uid, l):
    now = datetime.now(TZ)
    u = user(uid)
    persona = C.PERSONAS.get(u['persona']) or C.PERSONAS['friend']
    wd = T('ru', 'days')
    moods = q('SELECT day, score, note FROM moods WHERE user_id=? ORDER BY day DESC LIMIT 3', (uid,))
    mood_line = '; '.join(f'{m["day"]}: {m["score"]}/5' + (f' ({m["note"]})' if m['note'] else '') for m in moods) or 'нет данных'
    lvl, cur, _ = level_info(u['xp'])
    return (
        'Ты Dayly — личный ИИ-помощник и друг школьника 11 класса из города Конаев (Казахстан), '
        'который готовится к ҰБТ. '
        + (f'Его зовут {u["name"]}. ' if u['name'] else '') +
        'Отвечай на языке последнего сообщения (русский или казахский).\n'
        'Формат: это Telegram, пиши простым текстом без markdown (никаких **, #, таблиц). Для списков используй «•» '
        'и уместные эмодзи. Обычно 3–8 строк; подробнее только если просят.\n'
        'Не выдумывай факты: если не уверен — так и скажи. В учёбе объясняй ход решения, а не только ответ.\n'
        'Если человек пишет о тяжёлых переживаниях, отнесись тепло, не обесценивай, посоветуй поговорить с близким '
        'взрослым; при серьёзной беде упомяни бесплатный телефон доверия 111 (Казахстан).\n'
        'У тебя есть инструменты: добавлять, закрывать и удалять задачи, ставить дату экзамена, запускать '
        'фокус-таймер, записывать воду, смотреть погоду и расписание. Если просят что-то из этого — вызови инструмент, '
        'затем коротко подтверди сделанное. Не говори, что не умеешь.\n\n'
        + persona['prompt'] + '\n\n'
        f'Сейчас: {wd[now.weekday()]}, {now:%d.%m.%Y %H:%M} ({TZ}).\n'
        f'Расписание на сегодня:\n{schedule_text(now.weekday(), "ru")}\n'
        f'Расписание на завтра:\n{schedule_text((now + timedelta(days=1)).weekday(), "ru", tomorrow=True)}\n\n'
        f'Задачи:\n{tasks_text(uid, "ru")}\n'
        f'Экзамен: {exam_text(uid, "ru")}\n'
        f'Настроение (последние дни): {mood_line}\n'
        f'Вода сегодня: {water_today(uid)}/{WATER_GOAL}. Уровень в боте: {lvl} ({cur[2]}), серия {u["streak"]} дн.')


def run_tool(uid, name, args, actions):
    """Выполняет инструмент ИИ. Результат — обычный текст для модели."""
    try:
        if name == 'add_task':
            text = str(args.get('text', '')).strip()
            if not text:
                return 'Ошибка: пустой текст задачи.'
            q('INSERT INTO tasks(user_id,text,due) VALUES(?,?,?)', (uid, text[:200], parse_due(str(args.get('due', '')))))
            return 'Задача добавлена.\n' + tasks_text(uid, 'ru')
        if name == 'list_tasks':
            return tasks_text(uid, 'ru')
        if name in ('complete_task', 'delete_task'):
            rows = pending(uid)
            n = int(args.get('number', 0))
            if not 1 <= n <= len(rows):
                return f'Ошибка: задачи №{n} нет.'
            tid = rows[n - 1]['id']
            if name == 'complete_task':
                q('UPDATE tasks SET done=1 WHERE id=?', (tid,))
                reward(uid, 10, 'ru')
                return 'Отмечено выполненной (+10 XP).\n' + tasks_text(uid, 'ru')
            q('DELETE FROM tasks WHERE id=?', (tid,))
            return 'Удалено.\n' + tasks_text(uid, 'ru')
        if name == 'set_exam_date':
            raw = str(args.get('date', '')).strip()
            if raw.lower() == 'off':
                q('UPDATE users SET exam=NULL WHERE id=?', (uid,))
                return 'Дата экзамена удалена.'
            d = parse_date(raw)
            if not d:
                return 'Ошибка: нужна дата в формате ДД.ММ.ГГГГ.'
            q('UPDATE users SET exam=? WHERE id=?', (d.isoformat(), uid))
            return exam_text(uid, 'ru')
        if name == 'start_focus':
            minutes = min(max(int(args.get('minutes') or 25), 5), 120)
            actions.append(('focus', minutes))
            return f'Фокус-таймер на {minutes} минут будет запущен.'
        if name == 'add_water':
            before = water_today(uid)
            after = set_water(uid, max(1, min(int(args.get('glasses') or 1), 10)))
            water_goal_note(uid, 'ru', before, after)
            return f'Записано. Сегодня выпито {after}/{WATER_GOAL} стаканов.'
        if name == 'get_weather':
            return weather('ru')
        if name == 'get_schedule':
            now = datetime.now(TZ)
            day = args.get('day', 'today')
            if day == 'week':
                return '\n'.join(f'{T("ru", "days")[wd]}: ' + ', '.join(names) for wd, names in SCHEDULE.items())
            if day == 'tomorrow':
                return schedule_text((now + timedelta(days=1)).weekday(), 'ru', tomorrow=True)
            return schedule_text(now.weekday(), 'ru')
    except Exception as e:
        log.exception('tool %s failed', name)
        return f'Ошибка инструмента: {e}'
    return f'Неизвестный инструмент: {name}'


def ai_reply(uid, l, user_content, saved_text):
    """Диалог с Claude. user_content — строка или список блоков (фото). Возвращает (ответ, действия для бота)."""
    history = q('SELECT role, content FROM chat WHERE user_id=? ORDER BY id DESC LIMIT 14', (uid,))[::-1]
    messages = [{'role': r['role'], 'content': r['content']} for r in history]
    while messages and messages[0]['role'] != 'user':
        messages.pop(0)
    messages.append({'role': 'user', 'content': user_content})
    system = build_system(uid, l)
    actions, answer = [], ''
    for _ in range(6):                      # до 6 шагов «подумал → вызвал инструмент → ответил»
        data = anthropic_call(system, messages)
        blocks = []
        for b in data.get('content', []):
            if b.get('type') == 'text':
                blocks.append({'type': 'text', 'text': b.get('text', '')})
            elif b.get('type') == 'tool_use':
                blocks.append({'type': 'tool_use', 'id': b['id'], 'name': b['name'], 'input': b.get('input') or {}})
        if data.get('stop_reason') == 'tool_use':
            messages.append({'role': 'assistant', 'content': blocks})
            messages.append({'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': b['id'], 'content': run_tool(uid, b['name'], b['input'], actions)}
                for b in blocks if b['type'] == 'tool_use']})
            continue
        answer = ''.join(b['text'] for b in blocks if b['type'] == 'text').strip()
        break
    if not answer:
        raise RuntimeError('empty AI answer')
    answer = clean_md(answer)
    q('INSERT INTO chat(user_id, role, content) VALUES(?,?,?)', (uid, 'user', saved_text))
    q('INSERT INTO chat(user_id, role, content) VALUES(?,?,?)', (uid, 'assistant', answer))
    q('DELETE FROM chat WHERE user_id=? AND id NOT IN (SELECT id FROM chat WHERE user_id=? ORDER BY id DESC LIMIT 40)',
      (uid, uid))
    return answer[:3900], actions


async def respond_ai(context, chat_id, uid, l, user_content, saved_text):
    await context.bot.send_chat_action(chat_id, 'typing')
    try:
        answer, actions = await asyncio.to_thread(ai_reply, uid, l, user_content, saved_text)
        kb = ai_keyboard(l)
    except Exception as e:
        log.exception('AI failed')
        answer, actions, kb = T(l, ai_error_key(e)), [], keyboard(l)
    extra = [focus_start(context, uid, chat_id, minutes, l) for kind, minutes in actions if kind == 'focus']
    if extra:
        answer += '\n\n' + '\n'.join(extra)
    await context.bot.send_message(chat_id, answer, reply_markup=kb)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await allowed(update):
        return
    uid = update.effective_user.id
    l = L(uid)
    if time.monotonic() < context.user_data.pop('note_until', 0):      # после выбора смайлика ждём заметку
        save_note(uid, update.message.text)
        return await update.message.reply_text(T(l, 'note_saved'), reply_markup=keyboard(l))
    if not ANTHROPIC_API_KEY:
        return await update.message.reply_text(T(l, 'no_ai'), reply_markup=keyboard(l))
    await respond_ai(context, update.effective_chat.id, uid, l, update.message.text, update.message.text)


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await allowed(update):
        return
    uid = update.effective_user.id
    l = L(uid)
    if not ANTHROPIC_API_KEY:
        return await update.message.reply_text(T(l, 'photo_no_ai'), reply_markup=keyboard(l))
    await context.bot.send_chat_action(update.effective_chat.id, 'typing')
    try:
        tg_file = await update.message.photo[-1].get_file()
        raw = bytes(await tg_file.download_as_bytearray())
    except Exception:
        log.exception('photo download failed')
        return await update.message.reply_text(T(l, 'ai_error'), reply_markup=keyboard(l))
    caption = (update.message.caption or '').strip()
    content = [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg',
                                            'data': base64.b64encode(raw).decode()}},
               {'type': 'text', 'text': caption or T(l, 'photo_default')}]
    await respond_ai(context, update.effective_chat.id, uid, l, content, '📸 ' + (caption or 'фото'))


# ───────────────────────── автоматические сообщения ─────────────────────────
async def morning(context: ContextTypes.DEFAULT_TYPE):
    if OWNER_ID is None:
        return
    is_weekday = datetime.now(TZ).weekday() < 5
    if (context.job.data == 'weekday') != is_weekday:     # 07:00 — по будням, 10:00 — по выходным
        return
    u = user(OWNER_ID)
    if not u['morning']:
        return
    text, kb = await asyncio.to_thread(view, 'today', OWNER_ID, u['lang'])
    await context.bot.send_message(OWNER_ID, text, reply_markup=kb)


async def evening(context: ContextTypes.DEFAULT_TYPE):
    if OWNER_ID is None:
        return
    u = user(OWNER_ID)
    wd = (datetime.now(TZ) + timedelta(days=1)).weekday()
    if not u['evening'] or not SCHEDULE.get(wd):
        return
    l = u['lang']
    await context.bot.send_message(OWNER_ID, T(l, 'evening') + '\n\n' + tomorrow_message(OWNER_ID, l),
                                   reply_markup=keyboard(l))


async def mood_evening(context: ContextTypes.DEFAULT_TYPE):
    if OWNER_ID is None:
        return
    u = user(OWNER_ID)
    if u['mood']:
        await context.bot.send_message(OWNER_ID, T(u['lang'], 'mood_ask'), reply_markup=mood_keyboard(u['lang']))


async def water_reminder(context: ContextTypes.DEFAULT_TYPE):
    if OWNER_ID is None:
        return
    u = user(OWNER_ID)
    n = water_today(OWNER_ID)
    if not u['water_on'] or n >= WATER_GOAL:
        return
    l = u['lang']
    await context.bot.send_message(
        OWNER_ID, T(l, 'water_remind').format(n=n, g=WATER_GOAL),
        reply_markup=Markup([[Btn('💧 +1', callback_data='water_add_1'), Btn(T(l, 'menu'), callback_data='home')]]))


async def lesson_reminder(context: ContextTypes.DEFAULT_TYPE):
    if OWNER_ID is None:
        return
    idx = context.job.data
    lessons = SCHEDULE.get(datetime.now(TZ).weekday(), [])
    u = user(OWNER_ID)
    if idx >= len(lessons) or not u['lessons']:
        return
    start, end = LESSON_TIMES[idx]
    await context.bot.send_message(OWNER_ID, T(u['lang'], 'lesson_soon').format(name=lessons[idx], start=start, end=end))


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error('Unhandled error', exc_info=context.error)


COMMANDS = [
    ('today', 'Сводка на сегодня'), ('tomorrow', 'Что завтра'), ('schedule', 'Расписание'),
    ('tasks', 'Домашка и задачи'), ('add', 'Добавить задачу: /add [дд.мм] текст'), ('done', 'Выполнено: /done 1'),
    ('del', 'Удалить: /del 1'), ('weather', 'Погода по часам'), ('forecast', 'Погода на 3 дня'),
    ('mood', 'Как прошёл день'), ('stats', 'Настроение за неделю'), ('profile', 'Профиль, уровень и XP'),
    ('water', 'Трекер воды'), ('focus', 'Таймер фокуса: /focus 25'), ('exam', 'Отсчёт до экзамена: /exam дд.мм.гггг'),
    ('quiz', 'Викторина'), ('word', 'Английское слово дня'), ('hack', 'Лайфхак для учёбы'),
    ('fun', 'Развлечения'), ('dice', 'Кубики и игры'), ('joke', 'Шутка'), ('ball', 'Шар судьбы'),
    ('coin', 'Подбросить монетку'), ('riddle', 'Загадка'), ('horoscope', 'Гороскоп'), ('wyr', 'Что выберешь?'),
    ('challenge', 'Челлендж дня'), ('compliment', 'Комплимент'), ('choose', 'Выбрать: /choose пицца, суши'),
    ('random', 'Случайное число: /random 1 100'), ('life', 'Жизнь и здоровье'), ('fact', 'Интересный факт'),
    ('advice', 'Совет по жизни'), ('bored', 'Мне скучно'), ('breath', 'Дыхание против стресса'),
    ('eyes', 'Отдых для глаз'), ('sleep', 'Советы про сон'), ('motivation', 'Мотивация'),
    ('study', 'Меню учёбы'), ('bday', 'День рождения: /bday дд.мм'), ('mode', 'Режим ИИ'),
    ('settings', 'Настройки'), ('clear', 'Очистить память чата'), ('help', 'Что я умею'), ('id', 'Мой Telegram ID')]

VIEW_COMMANDS = (('start', 'home'), ('today', 'today'), ('tomorrow', 'tomorrow'), ('schedule', 'schedule'),
                 ('tasks', 'tasks'), ('settings', 'settings'), ('weather', 'weather'), ('forecast', 'forecast'),
                 ('mood', 'mood'), ('stats', 'stats'), ('profile', 'profile'), ('water', 'water'), ('quiz', 'quiz'),
                 ('word', 'word'), ('hack', 'hack'), ('fun', 'fun'), ('dice', 'dice'), ('joke', 'joke'),
                 ('ball', 'ball'), ('coin', 'coin'), ('riddle', 'riddle'), ('horoscope', 'horoscope'), ('wyr', 'wyr'),
                 ('challenge', 'challenge'), ('compliment', 'compliment'), ('life', 'life'), ('fact', 'fact'),
                 ('advice', 'advice'), ('bored', 'bored'), ('breath', 'breath'), ('eyes', 'eyes'), ('sleep', 'sleep'),
                 ('motivation', 'motivation'), ('study', 'study'), ('mode', 'ai'), ('help', 'help'))


async def post_init(app: Application):
    await app.bot.set_my_commands([BotCommand(name, desc) for name, desc in COMMANDS])


def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    for name, kind in VIEW_COMMANDS:
        app.add_handler(CommandHandler(name, view_command(kind)))
    app.add_handler(CommandHandler('id', myid))
    app.add_handler(CommandHandler('add', add_task))
    app.add_handler(CommandHandler('done', task_command('done')))
    app.add_handler(CommandHandler('del', task_command('del')))
    app.add_handler(CommandHandler('focus', focus_command))
    app.add_handler(CommandHandler('exam', exam_command))
    app.add_handler(CommandHandler('clear', clear_chat))
    app.add_handler(CommandHandler('choose', choose_command))
    app.add_handler(CommandHandler('random', random_command))
    app.add_handler(CommandHandler('bday', bday_command))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(on_error)

    jq = app.job_queue
    jq.run_daily(morning, time=dtime(7, 0, tzinfo=TZ), data='weekday', name='morning_weekdays')
    jq.run_daily(morning, time=dtime(10, 0, tzinfo=TZ), data='weekend', name='morning_weekends')
    jq.run_daily(evening, time=dtime(20, 0, tzinfo=TZ), name='evening')
    jq.run_daily(mood_evening, time=dtime(21, 0, tzinfo=TZ), name='mood_evening')
    for hour in (11, 15, 19):                              # напоминания о воде
        jq.run_daily(water_reminder, time=dtime(hour, 30, tzinfo=TZ), name=f'water_{hour}')
    for i, (start, _) in enumerate(LESSON_TIMES):          # напоминание за 10 минут до каждого урока
        h, m = map(int, start.split(':'))
        t = (datetime(2000, 1, 1, h, m) - timedelta(minutes=10)).time()
        jq.run_daily(lesson_reminder, time=t.replace(tzinfo=TZ), data=i, name=f'lesson_{i}')

    print('Dayly is running. Ctrl+C to stop.')
    app.run_polling()


if __name__ == '__main__':
    main()
