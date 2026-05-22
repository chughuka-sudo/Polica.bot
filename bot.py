import asyncio, logging, random, sqlite3, time, json
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand

TOKEN = "8332513172:AAGIlNH389YLBPLiBRR212YA-uhf8ZWV8Bg"
NEWS_CHANNEL_ID = -1003903126013
DB_NAME = "political_games.db"
STARTING_MONEY = 550_000_000_000
GAME_SPEED = 4 * 3600
BOT_ATTACK_CHANCE = 0.15
PAUSE_THRESHOLD = 0.6

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
bot = Bot(token=TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

COUNTRIES = ["Австрия","Бельгия","Великобритания","Германия","Греция","Дания","Ирландия","Испания","Италия",
             "Нидерланды","Норвегия","Польша","Португалия","Финляндия","Франция","Швейцария","Швеция","Чехия",
             "Венгрия","Румыния","Япония","Южная Корея","Китай","Индия","Индонезия","Малайзия","Сингапур",
             "Таиланд","Вьетнам","Филиппины","Тайвань","Монголия","Казахстан","Турция","Израиль","ОАЭ","Катар",
             "Саудовская Аравия","Иран","Пакистан","США","Канада","Мексика","Куба","Панама","Бразилия","Аргентина",
             "Чили","Перу","Колумбия","Австралия","Новая Зеландия","Россия","Беларусь","Украина","Сербия"]
BOT_COUNTRIES = ["Дания","Ирландия","Португалия","Финляндия","Чехия","Венгрия","Румыния","Монголия","Казахстан",
                 "Катар","Куба","Панама","Перу","Сербия"]
GOVERNMENTS = ["Демократия","Монархия","Коммунизм","Диктатура","Республика","Федерация","Конфедерация"]

class DB:
    def __init__(self): self._conn = None
    def conn(self):
        if not self._conn:
            self._conn = sqlite3.connect(DB_NAME, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn
    def ex(self, q, p=()): return self.conn().cursor().execute(q, p)
    def com(self): self.conn().commit()
    def init(self):
        self.ex("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)")
        self.ex("CREATE TABLE IF NOT EXISTS players(user_id INTEGER PRIMARY KEY,username TEXT,country TEXT,government TEXT,in_game INTEGER DEFAULT 1,is_bot INTEGER DEFAULT 0,alliance_id INTEGER,created_at REAL)")
        self.ex("CREATE TABLE IF NOT EXISTS cities(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,name TEXT,population INTEGER DEFAULT 100000,treasury REAL,soldiers INTEGER DEFAULT 5000,equipment TEXT DEFAULT '{}',last_update REAL)")
        self.ex("CREATE TABLE IF NOT EXISTS alliances(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE,founder_id INTEGER,created_at REAL)")
        self.ex("CREATE TABLE IF NOT EXISTS alliance_members(alliance_id INTEGER,user_id INTEGER,role TEXT DEFAULT 'member',PRIMARY KEY(alliance_id,user_id))")
        self.ex("CREATE TABLE IF NOT EXISTS wars(id INTEGER PRIMARY KEY AUTOINCREMENT,attacker_id INTEGER,defender_id INTEGER,target_city_id INTEGER,start_time REAL,peace_offer INTEGER DEFAULT 0)")
        self.ex("CREATE TABLE IF NOT EXISTS peace_offers(id INTEGER PRIMARY KEY AUTOINCREMENT,war_id INTEGER,from_user_id INTEGER,to_user_id INTEGER,cities_to_give TEXT,money_to_give REAL,status TEXT DEFAULT 'pending',created_at REAL)")
        self.ex("CREATE TABLE IF NOT EXISTS games(id INTEGER PRIMARY KEY AUTOINCREMENT,start_time REAL,is_active INTEGER DEFAULT 1,is_paused INTEGER DEFAULT 0,pause_start_time REAL,total_pause_duration REAL DEFAULT 0)")
        self.ex("CREATE TABLE IF NOT EXISTS pause_votes(game_id INTEGER,user_id INTEGER,vote_type TEXT,created_at REAL,UNIQUE(game_id,user_id))")
        self.ex("INSERT OR IGNORE INTO settings VALUES('money',?)",(str(STARTING_MONEY),))
        self.com()
db = DB()

class GameStates(StatesGroup):
    country = State()
    government = State()

def kb(btns, rw=2):
    k = InlineKeyboardMarkup(row_width=rw)
    for t, c in btns: k.add(InlineKeyboardButton(t, callback_data=c))
    return k

def colored_kb(peace, war, rw=2):
    k = InlineKeyboardMarkup(row_width=rw)
    for t, c in peace: k.add(InlineKeyboardButton(f"🟢 {t}", callback_data=c))
    for t, c in war: k.add(InlineKeyboardButton(f"🔴 {t}", callback_data=c))
    return k

def paused():
    g = db.ex("SELECT is_paused FROM games WHERE is_active=1").fetchone()
    return bool(g['is_paused']) if g else False

def find_countries(text):
    text = text.lower().strip()
    if not text: return []
    exact = [c for c in COUNTRIES if c.lower()==text]
    if exact: return exact
    sw = [c for c in COUNTRIES if c.lower().startswith(text)]
    cn = [c for c in COUNTRIES if text in c.lower() and c not in sw]
    return (sw+cn)[:5]

async def post_news(news, urgent=False):
    try:
        p = "🔴 " if urgent else "📰 "
        if NEWS_CHANNEL_ID: await bot.send_message(NEWS_CHANNEL_ID, p+news)
        if urgent:
            for pl in db.ex("SELECT user_id FROM players WHERE in_game=1 AND is_bot=0").fetchall():
                try: await bot.send_message(pl['user_id'], p+news)
                except: pass
    except Exception as e: logger.error(f"News: {e}")

async def notify_all(msg):
    for pl in db.ex("SELECT user_id FROM players WHERE in_game=1 AND is_bot=0").fetchall():
        try: await bot.send_message(pl['user_id'], msg)
        except: pass

async def check_start():
    if db.ex("SELECT COUNT(*) as c FROM players WHERE in_game=1 AND is_bot=0").fetchone()['c']>=1:
        if not db.ex("SELECT * FROM games WHERE is_active=1").fetchone():
            db.ex("INSERT INTO games(start_time,is_active) VALUES(?,1)", (time.time(),))
            db.com()
            taken = {r['country'] for r in db.ex("SELECT country FROM players WHERE in_game=1").fetchall()}
            for c in BOT_COUNTRIES:
                if c not in taken and len(taken)<20:
                    await create_bot(c); taken.add(c)
            await post_news("🌍 Игра началась!", True)

async def create_bot(country):
    bid = -1*(hash(country)%1000000+1000000)
    db.ex("INSERT INTO players(user_id,username,country,government,is_bot,created_at) VALUES(?,?,?,?,1,?)",
          (bid,f"Bot_{country}",country,random.choice(GOVERNMENTS),time.time()))
    for i in range(random.randint(1,3)):
        db.ex("INSERT INTO cities(user_id,name,population,treasury,soldiers,equipment,last_update) VALUES(?,?,?,?,?,?,?)",
              (bid,f"{'Столица' if i==0 else 'Город'}{i+1} {country}",random.randint(50000,300000),
               random.uniform(STARTING_MONEY*.3,STARTING_MONEY),random.randint(1000,30000),
               json.dumps({"Танки":random.randint(10,50)}),time.time()))
    db.com()

async def bot_act(bid):
    r = random.random()
    if r<0.05:
        c = db.ex("SELECT country FROM players WHERE user_id=?",(bid,)).fetchone()
        if c:
            try:
                db.ex("INSERT INTO alliances(name,founder_id,created_at) VALUES(?,?,?)",
                      (f"Пакт {c['country']}",bid,time.time()))
                aid = db.ex("SELECT last_insert_rowid() as id").fetchone()['id']
                db.ex("INSERT INTO alliance_members VALUES(?,?,'founder')",(aid,bid))
                db.ex("UPDATE players SET alliance_id=? WHERE user_id=?",(aid,bid))
                db.com()
            except: pass
    elif r<0.15 and random.random()<BOT_ATTACK_CHANCE:
        t = db.ex("SELECT user_id FROM players WHERE in_game=1 AND user_id!=? ORDER BY RANDOM() LIMIT 1",(bid,)).fetchone()
        if t:
            ct = db.ex("SELECT id FROM cities WHERE user_id=? ORDER BY RANDOM() LIMIT 1",(t['user_id'],)).fetchone()
            if ct:
                db.ex("INSERT INTO wars(attacker_id,defender_id,target_city_id,start_time) VALUES(?,?,?,?)",
                      (bid,t['user_id'],ct['id'],time.time()))
                db.com()

@dp.message_handler(commands=['start','menu'])
async def start(msg: types.Message):
    p = db.ex("SELECT * FROM players WHERE user_id=? AND in_game=1",(msg.from_user.id,)).fetchone()
    if p:
        ps = "⏸️ ПАУЗА" if paused() else "▶️ Активна"
        await msg.answer(f"🎮 {p['country']} | {p['government']}\n💰 ${STARTING_MONEY:,}\nСтатус: {ps}",
            reply_markup=colored_kb(
                [("💰 Экономика","eco"),("🏙️ Города","cities"),("🤝 Альянсы","all"),
                 ("🛡️ Техника","tech"),("⏯️ Пауза","pause_menu"),("⚙️ Настройки","settings")],
                [("⚔️ Война","war"),("👥 Армия","army"),("🏳️ Мир","peace_opt")]))
    else:
        await msg.answer("🌍 Добро пожаловать! Напишите название страны:")
        await GameStates.country.set()

@dp.message_handler(commands=['reset'])
async def reset(msg: types.Message):
    db.ex("UPDATE players SET in_game=0 WHERE user_id=?",(msg.from_user.id,))
    db.ex("DELETE FROM cities WHERE user_id=?",(msg.from_user.id,))
    db.com()
    await msg.answer("✅ Данные сброшены! Введите /start")

@dp.message_handler(state=GameStates.country)
async def choose_country(msg: types.Message, state: FSMContext):
    cs = find_countries(msg.text)
    if not cs: await msg.answer("❌ Не найдено"); return
    if len(cs)==1:
        await state.update_data(country=cs[0])
        await msg.answer(f"✅ {cs[0]}\nВыберите правительство:",
                        reply_markup=kb([(g,f"gov_{g}") for g in GOVERNMENTS],3))
        await GameStates.government.set()
    else: await msg.answer("🔍 Выберите:",reply_markup=kb([(c,f"cnt_{c}") for c in cs],1))

@dp.callback_query_handler(lambda c: c.data.startswith('cnt_'), state=GameStates.country)
async def sel_country(cb: types.CallbackQuery, state: FSMContext):
    await state.update_data(country=cb.data.split('_',1)[1])
    await cb.message.edit_text(f"✅ Выберите правительство:",
                              reply_markup=kb([(g,f"gov_{g}") for g in GOVERNMENTS],3))
    await GameStates.government.set()

@dp.callback_query_handler(lambda c: c.data.startswith('gov_'), state=GameStates.government)
async def sel_gov(cb: types.CallbackQuery, state: FSMContext):
    gov = cb.data.split('_',1)[1]
    data = await state.get_data()
    c = data['country']
    if db.ex("SELECT 1 FROM players WHERE country=? AND in_game=1",(c,)).fetchone():
        await cb.answer("❌ Занято!"); return
    uid = cb.from_user.id
    db.ex("INSERT INTO players VALUES(?,?,?,?,1,0,NULL,?)",
          (uid,cb.from_user.username or str(uid),c,gov,time.time()))
    db.ex("INSERT INTO cities(user_id,name,population,treasury,soldiers,equipment,last_update) VALUES(?,?,?,?,?,'{}',?)",
          (uid,f"Столица {c}",100000,STARTING_MONEY,5000,time.time()))
    db.com()
    await cb.message.edit_text(f"✅ {c} | {gov}\n💰 ${STARTING_MONEY:,}",
        reply_markup=colored_kb(
            [("💰 Экономика","eco"),("🏙️ Города","cities"),("🤝 Альянсы","all"),
             ("🛡️ Техника","tech"),("⏯️ Пауза","pause_menu"),("⚙️ Настройки","settings")],
            [("⚔️ Война","war"),("👥 Армия","army"),("🏳️ Мир","peace_opt")]))
    await state.finish()
    await check_start()

@dp.callback_query_handler(lambda c: c.data=='back')
async def back(cb: types.CallbackQuery):
    await cb.message.delete()
    await start(cb.message)
    await cb.answer()

@dp.callback_query_handler(lambda c: c.data=='eco')
async def eco(cb: types.CallbackQuery):
    c = db.ex("SELECT SUM(treasury) as t,SUM(population) as p,COUNT(*) as cnt FROM cities WHERE user_id=?",
              (cb.from_user.id,)).fetchone()
    if not c or not c['cnt']: await cb.answer("Нет городов!"); return
    await cb.message.edit_text(f"💰 Экономика\n🏙️ Городов: {c['cnt']}\n💵 Казна: ${c['t']:,.0f}\n👥 Население: {c['p']:,}",
                              reply_markup=kb([("↩️ Назад","back")]))

@dp.callback_query_handler(lambda c: c.data=='cities')
async def cities(cb: types.CallbackQuery):
    cs = db.ex("SELECT * FROM cities WHERE user_id=?",(cb.from_user.id,)).fetchall()
    if not cs: await cb.answer("Нет городов!"); return
    txt = "🏙️ Города:\n\n"
    for c in cs: txt += f"• {c['name']}: 👥{c['population']:,} 💰${c['treasury']:,.0f} ⚔️{c['soldiers']:,}\n"
    await cb.message.edit_text(txt,reply_markup=kb([("↩️ Назад","back")]))

@dp.callback_query_handler(lambda c: c.data=='war')
async def war(cb: types.CallbackQuery):
    ts = db.ex("SELECT user_id,country FROM players WHERE in_game=1 AND user_id!=?",
               (cb.from_user.id,)).fetchall()
    if not ts: await cb.answer("Нет целей!"); return
    await cb.message.edit_text("⚔️ Выберите цель:",
        reply_markup=kb([(f"⚔️ {t['country']}",f"wt_{t['user_id']}") for t in ts[:10]]+[("↩️ Отмена","back")],1))

@dp.callback_query_handler(lambda c: c.data.startswith('wt_'))
async def war_start(cb: types.CallbackQuery):
    tid = int(cb.data.split('_')[1])
    ct = db.ex("SELECT id FROM cities WHERE user_id=? ORDER BY RANDOM() LIMIT 1",(tid,)).fetchone()
    if ct:
        db.ex("INSERT INTO wars(attacker_id,defender_id,target_city_id,start_time) VALUES(?,?,?,?)",
              (cb.from_user.id,tid,ct['id'],time.time()))
        db.com()
        await cb.message.edit_text("⚔️ Война объявлена!",reply_markup=kb([("↩️ В меню","back")]))
        await post_news("⚔️ Новая война!",True)

@dp.callback_query_handler(lambda c: c.data=='army')
async def army(cb: types.CallbackQuery):
    cs = db.ex("SELECT * FROM cities WHERE user_id=?",(cb.from_user.id,)).fetchall()
    if not cs: await cb.answer("Нет армии!"); return
    total = sum(c['soldiers'] for c in cs)
    txt = f"👥 Армия: {total:,} солдат\n\n"
    for c in cs:
        eq = json.loads(c['equipment'])
        txt += f"🏙️ {c['name']}: {c['soldiers']:,} ⚔️\n"
    await cb.message.edit_text(txt,reply_markup=kb([("↩️ Назад","back")]))

@dp.callback_query_handler(lambda c: c.data=='peace_opt')
async def peace_opt(cb: types.CallbackQuery):
    uid = cb.from_user.id
    ws = db.ex("SELECT w.*,p.country as enemy FROM wars w JOIN players p ON (CASE WHEN w.attacker_id=? THEN w.defender_id ELSE w.attacker_id END)=p.user_id WHERE (w.attacker_id=? OR w.defender_id=?)",
               (uid,uid,uid)).fetchall()
    if not ws:
        await cb.message.edit_text("☮️ Нет войн",reply_markup=kb([("↩️ Назад","back")])); return
    await cb.message.edit_text("🏳️ Выберите войну:",
        reply_markup=kb([(f"Капитуляция перед {w['enemy']}",f"sur_{w['id']}") for w in ws]+[("↩️ Назад","back")],1))

@dp.callback_query_handler(lambda c: c.data.startswith('sur_'))
async def surrender(cb: types.CallbackQuery):
    wid = int(cb.data.split('_')[1])
    w = db.ex("SELECT * FROM wars WHERE id=?",(wid,)).fetchone()
    if not w: await cb.answer("Не найдено!"); return
    uid = cb.from_user.id
    eid = w['defender_id'] if w['attacker_id']==uid else w['attacker_id']
    money = (db.ex("SELECT SUM(treasury) as t FROM cities WHERE user_id=?",(uid,)).fetchone()['t'] or 0)*0.15
    db.ex("INSERT INTO peace_offers(war_id,from_user_id,to_user_id,cities_to_give,money_to_give,created_at) VALUES(?,?,?,'[]',?,?)",
          (wid,uid,eid,money,time.time()))
    db.ex("UPDATE wars SET peace_offer=1 WHERE id=?",(wid,))
    db.com()
    try: await bot.send_message(eid,f"🏳️ Предложение мира!\n💰 ${money:,.0f}",
                                reply_markup=kb([("✅ Принять",f"ap_{wid}"),("❌ Отклонить",f"rp_{wid}")]))
    except: pass
    await cb.message.edit_text("✅ Предложение отправлено!",reply_markup=kb([("↩️ В меню","back")]))

@dp.callback_query_handler(lambda c: c.data.startswith('ap_'))
async def accept_p(cb: types.CallbackQuery):
    wid = int(cb.data.split('_')[1])
    o = db.ex("SELECT * FROM peace_offers WHERE war_id=? AND status='pending'",(wid,)).fetchone()
    if not o: await cb.answer("Не найдено!"); return
    db.ex("UPDATE cities SET treasury=treasury+? WHERE user_id=?",(o['money_to_give'],o['to_user_id']))
    db.ex("DELETE FROM wars WHERE id=?",(wid,))
    db.ex("UPDATE peace_offers SET status='accepted' WHERE id=?",(o['id'],))
    db.com()
    await cb.message.edit_text("✅ Мир!",reply_markup=kb([("↩️ В меню","back")]))
    await post_news("☮️ Война завершена!",True)

@dp.callback_query_handler(lambda c: c.data.startswith('rp_'))
async def reject_p(cb: types.CallbackQuery):
    wid = int(cb.data.split('_')[1])
    db.ex("UPDATE wars SET peace_offer=0 WHERE id=?",(wid,))
    db.com()
    await cb.message.edit_text("❌ Отклонено!",reply_markup=kb([("↩️ В меню","back")]))

@dp.callback_query_handler(lambda c: c.data=='pause_menu')
async def pause_menu(cb: types.CallbackQuery):
    g = db.ex("SELECT * FROM games WHERE is_active=1").fetchone()
    if not g: await cb.answer("Нет игры!"); return
    ip = g['is_paused']
    tp = db.ex("SELECT COUNT(*) as c FROM players WHERE in_game=1 AND is_bot=0").fetchone()['c']
    vf = db.ex("SELECT COUNT(*) as c FROM pause_votes WHERE game_id=? AND vote_type=?",
               (g['id'],'resume' if ip else 'pause')).fetchone()['c']
    st = "🔴 ПАУЗА" if ip else "🟢 АКТИВНА"
    btns = [("▶️ Продолжить" if ip else "⏸️ Пауза","vp_resume" if ip else "vp_pause"),("↩️ Назад","back")]
    await cb.message.edit_text(f"⏯️ {st}\nГолосов: {vf}/{tp}\nПорог: {int(tp*PAUSE_THRESHOLD)}",
                               reply_markup=kb(btns,1))

@dp.callback_query_handler(lambda c: c.data.startswith('vp_'))
async def vote_pause(cb: types.CallbackQuery):
    g = db.ex("SELECT * FROM games WHERE is_active=1").fetchone()
    if not g: await cb.answer("Нет игры!"); return
    vt = 'resume' if cb.data=='vp_resume' else 'pause'
    ip = g['is_paused']
    if (ip and vt=='pause') or (not ip and vt=='resume'): await cb.answer("Недоступно!"); return
    db.ex("INSERT OR REPLACE INTO pause_votes VALUES(?,?,?,?)",(g['id'],cb.from_user.id,vt,time.time()))
    db.com()
    tp = db.ex("SELECT COUNT(*) as c FROM players WHERE in_game=1 AND is_bot=0").fetchone()['c']
    vf = db.ex("SELECT COUNT(*) as c FROM pause_votes WHERE game_id=? AND vote_type=?",(g['id'],vt)).fetchone()['c']
    if vf/tp>=PAUSE_THRESHOLD:
        if vt=='pause':
            db.ex("UPDATE games SET is_paused=1,pause_start_time=? WHERE id=?",(time.time(),g['id']))
            db.com()
            await notify_all("⏸️ Игра на паузе!")
        else:
            dur = time.time()-g['pause_start_time'] if g['pause_start_time'] else 0
            db.ex("UPDATE games SET is_paused=0,total_pause_duration=total_pause_duration+? WHERE id=?",(dur,g['id']))
            db.ex("DELETE FROM pause_votes WHERE game_id=?",(g['id'],))
            db.com()
            await notify_all(f"▶️ Игра продолжается! Пауза: {dur/3600:.1f}ч")
    await pause_menu(cb)

@dp.callback_query_handler(lambda c: c.data=='tech')
async def tech(cb: types.CallbackQuery):
    cs = db.ex("SELECT equipment FROM cities WHERE user_id=?",(cb.from_user.id,)).fetchall()
    eq = {}
    for c in cs:
        for k,v in json.loads(c['equipment']).items(): eq[k]=eq.get(k,0)+v
    txt = "🛡️ Техника:\n\n"+"\n".join([f"• {k}: {v}" for k,v in eq.items()]) if eq else "Нет техники"
    await cb.message.edit_text(txt,reply_markup=kb([("↩️ Назад","back")]))

@dp.callback_query_handler(lambda c: c.data=='all')
async def alliances(cb: types.CallbackQuery):
    al = db.ex("SELECT a.name,p.country FROM alliances a JOIN players p ON a.founder_id=p.user_id").fetchall()
    txt = "🤝 Альянсы:\n\n"+"\n".join([f"• {a['name']} ({a['country']})" for a in al]) if al else "Нет альянсов"
    await cb.message.edit_text(txt,reply_markup=kb([("↩️ Назад","back")]))

@dp.callback_query_handler(lambda c: c.data=='settings')
async def settings(cb: types.CallbackQuery):
    g = db.ex("SELECT * FROM games WHERE is_active=1").fetchone()
    if g: await cb.answer("Игра уже идёт!"); return
    await cb.message.edit_text("⚙️ Настройки:\n/edit_money 1000 — казна (млрд)\n/edit_speed 24 — скорость (4/12/24/48/16
