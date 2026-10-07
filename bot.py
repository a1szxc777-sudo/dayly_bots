import asyncio
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
DATA_DIR = Path(env('DATA_DIR', '.'))          # на сервере сюда кладём том/папку с постоянной памятью
ANTHROPIC_API_KEY = env('ANTHROPIC_API_KEY')   # необязательно: включает ИИ-чат
AI_MODEL = env('AI_MODEL', 'claude-haiku-4-5-20251001')

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
TEXT = {
    'ru': {
        'welcome': '👋 Привет! Я Dayly — твой личный помощник.\n\nЗнаю твоё расписание, погоду и домашку. '
                   'Нажми кнопку или просто напиши мне — отвечу.',
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
        'settings_text': '⚙️ Настройки уведомлений\n\nНажми, чтобы включить/выключить:',
        's_morning': 'Утренняя сводка', 's_evening': 'Вечер: что завтра', 's_lessons': 'Напоминания об уроках',
        'no_ai': '🤖 Я понимаю кнопки и команды: /today /tomorrow /add /tasks.\n'
                 'Чтобы я отвечал на любые вопросы, добавь ANTHROPIC_API_KEY.',
        'ai_error': '😵 Не получилось ответить, попробуй чуть позже.',
        'cleared': '🧹 Память чата очищена.',
        'lesson_soon': '🔔 Через 10 минут урок!\n\n📚 {name}\n⏰ {start}–{end}',
        'evening': '🌙 Добрый вечер! Вот что завтра:',
        'facts': ['У осьминога три сердца.', 'Мёд практически не портится — его находили съедобным в египетских гробницах.',
                  'Банан — это ягода, а клубника — нет.', 'Молния нагревает воздух сильнее, чем поверхность Солнца.',
                  'Сердце креветки находится в голове.', 'На Венере сутки длиннее года.',
                  'Мозг потребляет около 20% энергии всего организма.'],
        'fact': '🧠 Факт дня: ',
        'motivation_btn': '💪 Мотивация', 'quote': '💪 ',
        'weather_btn': '🌦 Погода', 'forecast_btn': '📆 3 дня', 'mood_btn': '😊 Как прошёл день',
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
        'stats_title': '📊 Настроение за 7 дней\n\n', 'stats_empty': 'Пока нет записей. Вечером нажми «Как прошёл день».',
        'avg': 'Среднее', 's_mood': 'Вечерний вопрос «как день»',
        'focus_started': '🍅 Фокус на {m} мин. Убери телефон и работай. Напишу, когда время выйдет. Остановить: /focus off',
        'focus_done': '⏰ {m} минут прошли! Сделай перерыв 5 минут: встань, попей воды.',
        'focus_stopped': '🛑 Таймер остановлен.',
        'exam_none': '⏳ Дата экзамена не задана. Напиши так: /exam 20.06.2027',
        'exam_left': '⏳ До экзамена ({d}) осталось дней: {n}', 'exam_today': '🔥 Экзамен сегодня! Ты готов(а). Удачи!',
        'exam_passed': 'Дата экзамена уже прошла. Задай новую: /exam 20.06.2027',
        'exam_saved': '✅ Дата сохранена.', 'exam_off': '🗑 Дата удалена.',
        'hourly': 'По часам',
        'quotes': ['Маленький шаг каждый день лучше, чем большой рывок раз в месяц.',
                   'Не надо быть идеальным, надо начать. Остальное придёт по ходу.',
                   'Сложно сейчас — легко потом. Потерпи чуть-чуть.',
                   'Ты уже сделал больше, чем тот, кто ещё сомневается.',
                   'Одна решённая задача — это уже победа дня.',
                   'Усталость пройдёт, а результат останется.',
                   'Сравнивай себя только с собой вчерашним.',
                   'Ошибка — не провал, а подсказка, что повторить.',
                   'Начни с самого неприятного дела — дальше будет легче.',
                   'Ты справишься. Ты же справлялся раньше.'],
    },
    'kz': {
        'welcome': '👋 Сәлем! Мен Dayly — сенің жеке көмекшіңмін.\n\nКестеңді, ауа райын және үй тапсырмасын білемін. '
                   'Батырманы бас немесе маған жаз — жауап беремін.',
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
        'settings_text': '⚙️ Хабарлама баптаулары\n\nҚосу/өшіру үшін бас:',
        's_morning': 'Таңғы шолу', 's_evening': 'Кешке: ертең не бар', 's_lessons': 'Сабақ туралы еске салу',
        'no_ai': '🤖 Мен батырмалар мен командаларды түсінемін: /today /tomorrow /add /tasks.\n'
                 'Кез келген сұраққа жауап беру үшін ANTHROPIC_API_KEY қос.',
        'ai_error': '😵 Жауап бере алмадым, сәлден кейін қайталап көр.',
        'cleared': '🧹 Чат жады тазаланды.',
        'lesson_soon': '🔔 10 минуттан кейін сабақ!\n\n📚 {name}\n⏰ {start}–{end}',
        'evening': '🌙 Қайырлы кеш! Ертең мынау болады:',
        'facts': ['Сегізаяқтың үш жүрегі бар.', 'Бал іс жүзінде бұзылмайды — оны мысыр қабірлерінен жеуге жарамды күйде тапқан.',
                  'Банан — жидек, ал құлпынай жидек емес.', 'Найзағай айналасындағы ауаны Күн бетінен де қатты қыздырады.',
                  'Асшаяның жүрегі басында орналасқан.', 'Шолпанда тәулік жылдан ұзақ.',
                  'Ми дене энергиясының шамамен 20%-ын пайдаланады.'],
        'fact': '🧠 Күн фактісі: ',
        'motivation_btn': '💪 Мотивация', 'quote': '💪 ',
        'weather_btn': '🌦 Ауа райы', 'forecast_btn': '📆 3 күн', 'mood_btn': '😊 Күн қалай өтті',
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
        'stats_title': '📊 7 күндегі көңіл-күй\n\n', 'stats_empty': 'Әзірге жазба жоқ. Кешке «Күн қалай өтті» батырмасын бас.',
        'avg': 'Орташа', 's_mood': 'Кешкі «күн қалай» сұрағы',
        'focus_started': '🍅 {m} минут фокус. Телефонды қой да, жұмыс істе. Уақыт біткенде жазамын. Тоқтату: /focus off',
        'focus_done': '⏰ {m} минут өтті! 5 минут үзіліс жаса: тұр, су іш.',
        'focus_stopped': '🛑 Таймер тоқтатылды.',
        'exam_none': '⏳ Емтихан күні көрсетілмеген. Былай жаз: /exam 20.06.2027',
        'exam_left': '⏳ Емтиханға ({d}) қалған күн: {n}', 'exam_today': '🔥 Емтихан бүгін! Сен дайынсың. Сәттілік!',
        'exam_passed': 'Емтихан күні өтіп кетті. Жаңасын қой: /exam 20.06.2027',
        'exam_saved': '✅ Күн сақталды.', 'exam_off': '🗑 Күн өшірілді.',
        'hourly': 'Сағат бойынша',
        'quotes': ['Күн сайынғы кішкентай қадам айына бір рет жасалған үлкен секіруден жақсы.',
                   'Мінсіз болудың қажеті жоқ, бастау керек. Қалғаны жолда келеді.',
                   'Қазір қиын болса, кейін оңай болады. Сәл шыда.',
                   'Күмәнданып тұрғаннан гөрі, бастап кеткен адам көп нәрсе істеп қояды.',
                   'Бір шығарылған есеп — күннің жеңісі.',
                   'Шаршау өтеді, ал нәтиже қалады.',
                   'Өзіңді тек кешегі өзіңмен салыстыр.',
                   'Қателік — сәтсіздік емес, нені қайталау керегін көрсететін белгі.',
                   'Ең жағымсыз істен баста — әрі қарай жеңіл болады.',
                   'Сен істей аласың. Бұрын да істедің ғой.'],
    },
}


def T(l, key):
    return TEXT[l][key]


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
    ''')
    for col, ddl in (('mood', 'INTEGER DEFAULT 1'), ('exam', 'TEXT')):     # миграция для старой базы
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
    if field in ('morning', 'evening', 'lessons', 'mood'):
        user(uid)
        q(f'UPDATE users SET {field}=1-{field} WHERE id=?', (uid,))


def pending(uid):
    return q('SELECT * FROM tasks WHERE user_id=? AND done=0 ORDER BY due IS NULL, due, id', (uid,))


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
    day = f'{datetime.now(TZ):%Y-%m-%d}'
    q('INSERT INTO moods(user_id, day, score) VALUES(?,?,?) '
      'ON CONFLICT(user_id, day) DO UPDATE SET score=excluded.score', (uid, day, score))


def save_note(uid, note):
    q('UPDATE moods SET note=? WHERE user_id=? AND day=?', (note[:100], uid, f'{datetime.now(TZ):%Y-%m-%d}'))


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
                   [Btn(T(l, 'stats_btn'), callback_data='stats'), Btn(T(l, 'back'), callback_data='home')]])


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


# ───────────────────────── сообщения и клавиатуры ─────────────────────────
def today_message(uid, l):
    now = datetime.now(TZ)
    parts = [T(l, 'morning'), f'📍 {CITY}\n{weather(l)}', schedule_text(now.weekday(), l)]
    if pending(uid):
        parts.append(tasks_text(uid, l))
        if SCHEDULE.get(now.weekday()):
            parts.append(T(l, 'homework'))
    if SCHEDULE.get(now.weekday()):
        parts.append(T(l, 'rest'))
    if (exam_days_left(uid) or -1) >= 0:
        parts.append(exam_text(uid, l))
    parts.append(T(l, 'quote') + random.choice(T(l, 'quotes')))
    parts.append(T(l, 'fact') + T(l, 'facts')[now.timetuple().tm_yday % len(T(l, 'facts'))])
    return '\n\n'.join(parts)


def tomorrow_message(uid, l):
    wd = (datetime.now(TZ) + timedelta(days=1)).weekday()
    text = schedule_text(wd, l, tomorrow=True)
    if pending(uid):
        text += '\n\n' + tasks_text(uid, l)
    return text


def keyboard(l):
    return Markup([
        [Btn(T(l, 'today'), callback_data='today'), Btn(T(l, 'tomorrow'), callback_data='tomorrow')],
        [Btn(T(l, 'schedule'), callback_data='schedule'), Btn(T(l, 'homework_btn'), callback_data='tasks')],
        [Btn(T(l, 'weather_btn'), callback_data='weather'), Btn(T(l, 'forecast_btn'), callback_data='forecast')],
        [Btn(T(l, 'mood_btn'), callback_data='mood'), Btn(T(l, 'focus_btn'), callback_data='focus')],
        [Btn(T(l, 'motivation_btn'), callback_data='motivation'), Btn(T(l, 'exam_btn'), callback_data='exam')],
        [Btn(T(l, 'settings'), callback_data='settings'), Btn(T(l, 'language'), callback_data='language')]])


def lang_keyboard():
    return Markup([[Btn('🇷🇺 Русский', callback_data='lang_ru'), Btn('🇰🇿 Қазақша', callback_data='lang_kz')]])


def settings_keyboard(uid, l):
    u = user(uid)
    rows = [[Btn(('✅ ' if u[k] else '❌ ') + T(l, label), callback_data='toggle_' + k)]
            for k, label in (('morning', 's_morning'), ('evening', 's_evening'), ('lessons', 's_lessons'), ('mood', 's_mood'))]
    rows.append([Btn(T(l, 'back'), callback_data='home')])
    return Markup(rows)


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
        return f'📍 {CITY}\n{weather(l)}', keyboard(l)
    if kind == 'forecast':
        return forecast_text(l), keyboard(l)
    if kind == 'mood':
        return T(l, 'mood_ask'), mood_keyboard(l)
    if kind == 'stats':
        return stats_text(uid, l), mood_keyboard(l)
    if kind == 'exam':
        return exam_text(uid, l), keyboard(l)
    if kind == 'motivation':
        return T(l, 'quote') + random.choice(T(l, 'quotes')), keyboard(l)
    if kind == 'settings':
        return T(l, 'settings_text'), settings_keyboard(uid, l)
    if kind == 'language':
        return '🌐 Выбери язык / Тілді таңда:', lang_keyboard()
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
        await update.message.reply_text(T(l, 'done_ok' if action == 'done' else 'del_ok') + '\n\n' + tasks_text(uid, l),
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
    await context.bot.send_message(context.job.chat_id, T(d['l'], 'focus_done').format(m=d['minutes']))


def focus_start(context, uid, chat_id, minutes, l):
    focus_cancel(context, uid)
    context.job_queue.run_once(focus_done, minutes * 60, chat_id=chat_id, data={'l': l, 'minutes': minutes},
                               name=f'focus_{uid}')
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
    data = qy.data
    l = L(uid)
    if data == 'focus':
        return await edit(qy, focus_start(context, uid, qy.message.chat_id, 25, l), keyboard(l))
    if data.startswith('mood_'):
        score = int(data[5:])
        save_mood(uid, score)
        context.user_data['note_until'] = time.monotonic() + 600
        return await edit(qy, T(l, 'mood_reply')[score - 1] + '\n\n' + T(l, 'mood_note_ask'), keyboard(l))
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


# ───────────────────────── ИИ-чат (необязательно) ─────────────────────────
def ai_reply(uid, l, text):
    now = datetime.now(TZ)
    history = q('SELECT role, content FROM chat WHERE user_id=? ORDER BY id DESC LIMIT 12', (uid,))[::-1]
    messages = [{'role': r['role'], 'content': r['content']} for r in history]
    while messages and messages[0]['role'] != 'user':
        messages.pop(0)
    messages.append({'role': 'user', 'content': text})
    system = (
        'Ты Dayly — дружелюбный личный помощник школьника 11 класса из города Конаев (Казахстан). '
        'Отвечай на языке, на котором пишет пользователь (русский или казахский), коротко и по делу. '
        'В учёбе объясняй ход решения, а не только ответ. Не выдумывай факты.\n\n'
        f'Сейчас: {now:%A, %d.%m.%Y %H:%M}.\n'
        f'Расписание на сегодня:\n{schedule_text(now.weekday(), l)}\n\n'
        f'Домашка/задачи:\n{tasks_text(uid, l)}')
    r = requests.post('https://api.anthropic.com/v1/messages', timeout=60,
                      headers={'x-api-key': ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01',
                               'content-type': 'application/json'},
                      json={'model': AI_MODEL, 'max_tokens': 700, 'system': system, 'messages': messages})
    r.raise_for_status()
    answer = ''.join(b.get('text', '') for b in r.json()['content'] if b.get('type') == 'text').strip()
    if not answer:
        raise RuntimeError('empty AI answer')
    q('INSERT INTO chat(user_id, role, content) VALUES(?,?,?)', (uid, 'user', text))
    q('INSERT INTO chat(user_id, role, content) VALUES(?,?,?)', (uid, 'assistant', answer))
    q('DELETE FROM chat WHERE user_id=? AND id NOT IN (SELECT id FROM chat WHERE user_id=? ORDER BY id DESC LIMIT 40)',
      (uid, uid))
    return answer[:4000]


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
    await context.bot.send_chat_action(update.effective_chat.id, 'typing')
    try:
        answer = await asyncio.to_thread(ai_reply, uid, l, update.message.text)
    except Exception:
        log.exception('AI failed')
        answer = T(l, 'ai_error')
    await update.message.reply_text(answer, reply_markup=keyboard(l))


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


async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand('today', 'Сводка на сегодня'), BotCommand('tomorrow', 'Что завтра'),
        BotCommand('schedule', 'Расписание'), BotCommand('tasks', 'Домашка и задачи'),
        BotCommand('add', 'Добавить задачу: /add [дд.мм] текст'), BotCommand('done', 'Выполнено: /done 1'),
        BotCommand('del', 'Удалить: /del 1'), BotCommand('settings', 'Настройки'), BotCommand('motivation', 'Мотивация'),
        BotCommand('weather', 'Погода по часам'), BotCommand('forecast', 'Погода на 3 дня'),
        BotCommand('mood', 'Как прошёл день'), BotCommand('stats', 'Настроение за неделю'),
        BotCommand('focus', 'Таймер фокуса: /focus 25'), BotCommand('exam', 'Отсчёт до экзамена: /exam дд.мм.гггг'),
        BotCommand('clear', 'Очистить память чата'), BotCommand('id', 'Мой Telegram ID')])


def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    for name, kind in (('start', 'home'), ('today', 'today'), ('tomorrow', 'tomorrow'), ('schedule', 'schedule'),
                       ('tasks', 'tasks'), ('settings', 'settings'), ('motivation', 'motivation'), ('weather', 'weather'),
                       ('forecast', 'forecast'), ('mood', 'mood'), ('stats', 'stats')):
        app.add_handler(CommandHandler(name, view_command(kind)))
    app.add_handler(CommandHandler('id', myid))
    app.add_handler(CommandHandler('add', add_task))
    app.add_handler(CommandHandler('done', task_command('done')))
    app.add_handler(CommandHandler('del', task_command('del')))
    app.add_handler(CommandHandler('focus', focus_command))
    app.add_handler(CommandHandler('exam', exam_command))
    app.add_handler(CommandHandler('clear', clear_chat))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(on_error)

    jq = app.job_queue
    jq.run_daily(morning, time=dtime(7, 0, tzinfo=TZ), data='weekday', name='morning_weekdays')
    jq.run_daily(morning, time=dtime(10, 0, tzinfo=TZ), data='weekend', name='morning_weekends')
    jq.run_daily(evening, time=dtime(20, 0, tzinfo=TZ), name='evening')
    jq.run_daily(mood_evening, time=dtime(21, 0, tzinfo=TZ), name='mood_evening')
    for i, (start, _) in enumerate(LESSON_TIMES):          # напоминание за 10 минут до каждого урока
        h, m = map(int, start.split(':'))
        t = (datetime(2000, 1, 1, h, m) - timedelta(minutes=10)).time()
        jq.run_daily(lesson_reminder, time=t.replace(tzinfo=TZ), data=i, name=f'lesson_{i}')

    print('Dayly is running. Ctrl+C to stop.')
    app.run_polling()


if __name__ == '__main__':
    main()
