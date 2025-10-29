"""
개발 학회용 Discord 작업 타이머 봇 (필수 기능만)
------------------------------------------------
유지 기능
- 타이머 기반 개발시간 기록: 프로젝트/태그/메모 분류
- 뽀모도로(집중/휴식/사이클) + 자동 세션 기록
- 리더보드(/summary) 차트
- 수동 기록(/log)
- CSV 추출(/export_csv)

요구 사항
- Python 3.10+
- pip install -U discord.py aiosqlite matplotlib
- 환경변수 DISCORD_TOKEN 설정

실행
  python bot.py
"""

import os
import io
import csv
import math
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ------------------ Config ------------------
TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = os.getenv("ACTIVITY_DB", "dev_timer.db")
TZ = timezone.utc  # 내부 기록은 UTC

intents = discord.Intents.default()
# message_content 불필요 (링크 캡처 제거)

bot = commands.Bot(command_prefix=commands.when_mentioned_or("!"), intents=intents)
log = logging.getLogger("devtimer.min")
logging.basicConfig(level=logging.INFO)

# ------------------ DB ------------------
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS work_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  project TEXT,
  tags TEXT,
  note TEXT,
  start_ts INTEGER NOT NULL,
  end_ts INTEGER,
  source TEXT NOT NULL DEFAULT 'timer'  -- timer|manual|pomodoro
);
CREATE INDEX IF NOT EXISTS idx_work_user_time ON work_sessions(user_id, start_ts);

CREATE TABLE IF NOT EXISTS pomodoro (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  work_min INTEGER NOT NULL,
  break_min INTEGER NOT NULL,
  cycles INTEGER NOT NULL,
  project TEXT,
  tags TEXT,
  started_ts INTEGER NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()

# ------------------ Utils ------------------
now = lambda: datetime.now(TZ)

def to_epoch(dt: datetime) -> int:
    return int(dt.timestamp())

def from_epoch(x: int) -> datetime:
    return datetime.fromtimestamp(x, tz=TZ)

async def get_open_session(db, guild_id: int, user_id: int) -> Optional[int]:
    async with db.execute(
        "SELECT id FROM work_sessions WHERE guild_id=? AND user_id=? AND end_ts IS NULL ORDER BY id DESC LIMIT 1",
        (guild_id, user_id),
    ) as cur:
        row = await cur.fetchone()
        return row[0] if row else None

# ------------------ Charts ------------------
async def bar_chart(labels: list[str], minutes: list[int], title: str) -> bytes:
    hours = [m/60 for m in minutes]
    fig = plt.figure(figsize=(10,5), dpi=150)
    ax = fig.add_subplot(111)
    ax.bar(labels, hours)
    ax.set_title(title)
    ax.set_ylabel("시간 (h)")
    ax.tick_params(axis='x', rotation=30, ha='right')
    ax.set_ylim(0, max(1, math.ceil(max(hours+[0]) * 1.2)))
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    plt.close(fig)
    buf.seek(0)
    return buf.read()

# ------------------ Bot Events ------------------
@bot.event
async def on_ready():
    await init_db()
    try:
        await bot.tree.sync()
    except Exception:
        log.exception("Slash sync failed")
    log.info("Logged in as %s", bot.user)

# ------------------ Slash: Timer ------------------
@bot.tree.command(name="start", description="작업 타이머 시작")
@app_commands.describe(project="프로젝트", tags="쉼표 태그", note="메모")
async def start_cmd(inter: discord.Interaction, project: Optional[str]=None, tags: Optional[str]=None, note: Optional[str]=None):
    await inter.response.defer(ephemeral=True)
    async with aiosqlite.connect(DB_PATH) as db:
        open_id = await get_open_session(db, inter.guild.id, inter.user.id)
        if open_id:
            await inter.followup.send("이미 진행 중인 타이머가 있어요. /stop 으로 종료해주세요.")
            return
        await db.execute(
            "INSERT INTO work_sessions (guild_id,user_id,project,tags,note,start_ts,end_ts,source) VALUES (?,?,?,?,?,?,NULL,'timer')",
            (inter.guild.id, inter.user.id, project, tags, note, to_epoch(now())),
        )
        await db.commit()
    await inter.followup.send(f"▶️ 시작 — 프로젝트: **{project or '미지정'}**, 태그: {tags or '-'}")


@bot.tree.command(name="stop", description="작업 타이머 종료")
@app_commands.describe(note="마무리 메모")
async def stop_cmd(inter: discord.Interaction, note: Optional[str]=None):
    await inter.response.defer(ephemeral=True)
    async with aiosqlite.connect(DB_PATH) as db:
        open_id = await get_open_session(db, inter.guild.id, inter.user.id)
        if not open_id:
            await inter.followup.send("진행 중인 타이머가 없어요. /start 로 시작하세요.")
            return
        # 가져와서 소요 계산
        async with db.execute("SELECT start_ts, project FROM work_sessions WHERE id=?", (open_id,)) as cur:
            row = await cur.fetchone()
        await db.execute("UPDATE work_sessions SET end_ts=?, note=COALESCE(note, ?) WHERE id=?", (to_epoch(now()), note, open_id))
        await db.commit()
    minutes = (to_epoch(now()) - row[0]) // 60
    await inter.followup.send(f"⏹️ 종료 — **{minutes}분** / 프로젝트: **{row[1] or '미지정'}**")


@bot.tree.command(name="status", description="내 타이머 상태")
async def status_cmd(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True)
    async with aiosqlite.connect(DB_PATH) as db:
        open_id = await get_open_session(db, inter.guild.id, inter.user.id)
        if not open_id:
            await inter.followup.send("진행 중인 타이머가 없어요.")
            return
        async with db.execute("SELECT start_ts, project, tags, note FROM work_sessions WHERE id=?", (open_id,)) as cur:
            s, proj, tags, note = await cur.fetchone()
    minutes = (to_epoch(now()) - s) // 60
    await inter.followup.send(f"⏱️ 진행중 {minutes}분 / 프로젝트: **{proj or '미지정'}** / 태그: {tags or '-'} / 메모: {note or '-'}")

# ------------------ Slash: Manual Log ------------------
@bot.tree.command(name="log", description="수동 기록 추가(분 단위)")
@app_commands.describe(minutes="분", project="프로젝트", tags="태그", note="메모", when="시작시각(UTC, YYYY-MM-DD HH:MM)")
async def log_cmd(inter: discord.Interaction, minutes: int, project: Optional[str]=None, tags: Optional[str]=None, note: Optional[str]=None, when: Optional[str]=None):
    await inter.response.defer(ephemeral=True)
    start_dt = now() if not when else datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    end_dt = start_dt + timedelta(minutes=max(1, minutes))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO work_sessions (guild_id,user_id,project,tags,note,start_ts,end_ts,source) VALUES (?,?,?,?,?,?,?,'manual')",
            (inter.guild.id, inter.user.id, project, tags, note, to_epoch(start_dt), to_epoch(end_dt)),
        )
        await db.commit()
    await inter.followup.send(f"📝 수동 기록 — **{minutes}분** / 프로젝트: **{project or '미지정'}**")

# ------------------ Slash: Summary (Leaderboard) ------------------
async def fetch_totals(guild_id: int, since: datetime, until: datetime, project: Optional[str]):
    q = (
        "SELECT user_id, SUM((COALESCE(end_ts, ?) - start_ts)/60) AS m "
        "FROM work_sessions WHERE guild_id=? AND start_ts<? AND COALESCE(end_ts, ?) > ?"
    )
    params = [to_epoch(until), guild_id, to_epoch(until), to_epoch(until), to_epoch(since)]
    if project:
        q += " AND project=?"
        params.append(project)
    q += " GROUP BY user_id ORDER BY m DESC"
    rows = []
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(q, params) as cur:
            async for uid, m in cur:
                rows.append((uid, int(m or 0)))
    return rows

@bot.tree.command(name="summary", description="리더보드 차트")
@app_commands.describe(days="최근 N일(기본 7)", top_n="상위 N명(기본 10)", project="프로젝트 필터")
async def summary_cmd(inter: discord.Interaction, days: int=7, top_n: int=10, project: Optional[str]=None):
    await inter.response.defer()
    since = now() - timedelta(days=max(1, days))
    until = now()
    totals = await fetch_totals(inter.guild.id, since, until, project)
    if not totals:
        await inter.followup.send("데이터가 아직 없어요. /start 로 타이머를 시작해 보세요!")
        return
    items = totals[:max(1, top_n)]
    labels, mins = [], []
    for uid, m in items:
        mbr = inter.guild.get_member(uid)
        labels.append(mbr.display_name if mbr else str(uid))
        mins.append(m)
    png = await bar_chart(labels, mins, f"최근 {days}일 리더보드")
    await inter.followup.send(file=discord.File(io.BytesIO(png), filename="leaderboard.png"))

# ------------------ Slash: CSV Export ------------------
@bot.tree.command(name="export_csv", description="최근 N일 세션을 CSV로 추출")
@app_commands.describe(days="최근 N일(기본 30)", project="프로젝트 필터")
async def export_csv_cmd(inter: discord.Interaction, days: int=30, project: Optional[str]=None):
    await inter.response.defer()
    since = now() - timedelta(days=max(1, days))
    until = now()
    q = (
        "SELECT user_id, project, tags, note, start_ts, COALESCE(end_ts, ?) as end_ts FROM work_sessions "
        "WHERE guild_id=? AND start_ts<? AND COALESCE(end_ts, ?) > ?"
    )
    params = [to_epoch(until), inter.guild.id, to_epoch(until), to_epoch(until), to_epoch(since)]
    if project:
        q += " AND project=?"
        params.append(project)

    rows = []
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(q, params) as cur:
            async for r in cur:
                rows.append(r)
    if not rows:
        await inter.followup.send("내보낼 데이터가 없어요.")
        return
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["user_id","project","tags","note","start_iso","end_iso","minutes"])
    for uid, proj, tags, note, s, e in rows:
        minutes = (e - s)//60
        w.writerow([uid, proj or "", tags or "", note or "", from_epoch(s).isoformat(), from_epoch(e).isoformat(), minutes])
    data = buf.getvalue().encode()
    await inter.followup.send(file=discord.File(io.BytesIO(data), filename="sessions.csv"))

# ------------------ Pomodoro ------------------
@bot.tree.command(name="pomodoro", description="뽀모도로 시작")
@app_commands.describe(work_min="집중 분(기본 50)", break_min="휴식 분(기본 10)", cycles="사이클(기본 1)", project="프로젝트", tags="태그")
async def pomodoro_cmd(inter: discord.Interaction, work_min: int=50, break_min: int=10, cycles: int=1, project: Optional[str]=None, tags: Optional[str]=None):
    await inter.response.defer(ephemeral=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO pomodoro (guild_id,user_id,work_min,break_min,cycles,project,tags,started_ts,active) VALUES (?,?,?,?,?,?,?,?,1)",
            (inter.guild.id, inter.user.id, max(1, work_min), max(1, break_min), max(1, cycles), project, tags, to_epoch(now())),
        )
        await db.commit()
    await inter.followup.send(f"🍅 뽀모도로 시작 — {work_min}m 집중 / {break_min}m 휴식 × {cycles}")

@tasks.loop(seconds=30)
async def pomodoro_worker():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id,guild_id,user_id,work_min,break_min,cycles,project,tags,started_ts FROM pomodoro WHERE active=1") as cur:
            items = await cur.fetchall()
        for pid, gid, uid, w, b, cyc, proj, tags, started in items:
            guild = bot.get_guild(gid)
            member = guild.get_member(uid) if guild else None
            if not guild or not member:
                continue
            elapsed = (to_epoch(now()) - started)//60
            phase = w + b
            if elapsed >= phase * cyc:
                await db.execute("UPDATE pomodoro SET active=0 WHERE id=?", (pid,))
                await db.commit()
                try:
                    await member.send("✅ 뽀모도로 완료!")
                except Exception:
                    pass
                continue
            in_cycle = elapsed % phase
            open_id = await get_open_session(db, gid, uid)
            if in_cycle == 0 and open_id is None:
                # work 시작
                await db.execute(
                    "INSERT INTO work_sessions (guild_id,user_id,project,tags,note,start_ts,end_ts,source) VALUES (?,?,?,?,?,?,NULL,'pomodoro')",
                    (gid, uid, proj, tags, "pomodoro", to_epoch(now())),
                )
                await db.commit()
                try:
                    await member.send("▶️ 집중 시작!")
                except Exception:
                    pass
            elif in_cycle == w and open_id is not None:
                # work 종료
                await db.execute("UPDATE work_sessions SET end_ts=? WHERE id=?", (to_epoch(now()), open_id))
                await db.commit()
                try:
                    await member.send("⏸️ 휴식 시작")
                except Exception:
                    pass

@pomodoro_worker.before_loop
async def before_worker():
    await bot.wait_until_ready()

pomodoro_worker.start()

# ------------------ main ------------------
if __name__ == "__main__":
    if not TOKEN:
        print("환경변수 DISCORD_TOKEN이 필요합니다: DISCORD_TOKEN=<bot token>")
    else:
        asyncio.run(init_db())
        bot.run(TOKEN)
