#!/usr/bin/env python3
import os, sys, json, time, sqlite3, socket, subprocess
from pathlib import Path

# ---------------- CONFIG ----------------
OUTDIR = Path.home()
DB_PATH = Path.home() / ".songbot.db"
SOCK = "/tmp/songbot.sock"
PLAYER = "mpv"
PROXY = "socks5://127.0.0.1:2080"
COOKIES = "/home/mammad/Music/cookies/cookies.txt"

OUTDIR.mkdir(parents=True, exist_ok=True)

# ---------------- DB ----------------
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

def init_db():
    cur.execute("""
    CREATE TABLE IF NOT EXISTS songs (
        id INTEGER PRIMARY KEY,
        title TEXT,
        file TEXT UNIQUE
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        song_id INTEGER,
        query TEXT UNIQUE
    )
    """)
    conn.commit()

init_db()

# ---------------- MPV ----------------
def ensure_mpv():
    try:
        s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        s.connect(SOCK); s.close(); return
    except:
        pass

    try: os.unlink(SOCK)
    except: pass

    subprocess.Popen([
        PLAYER,"--idle=yes","--no-video","--quiet","--no-terminal",
        "--input-ipc-server="+SOCK
    ],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    for _ in range(50):
        if os.path.exists(SOCK):
            try:
                s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
                s.connect(SOCK); s.close(); return
            except: pass
        time.sleep(0.1)

def mpv(cmd):
    s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    s.connect(SOCK)
    s.send((json.dumps({"command":cmd})+"\n").encode())
    s.close()

def mpv_query(cmd):
    s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    s.connect(SOCK)
    s.send((json.dumps({"command":cmd})+"\n").encode())
    data=s.recv(8192).decode()
    s.close()
    return json.loads(data)

# ---------------- HELPERS ----------------
def norm(s):
    return " ".join(c.lower() if c.isalnum() else " " for c in s).split()

def add_alias(song_id, q):
    if not q: return
    cur.execute("INSERT OR IGNORE INTO aliases(song_id,query) VALUES (?,?)",(song_id,q.lower()))
    conn.commit()

def find_alias(q):
    cur.execute("SELECT song_id FROM aliases WHERE query=?",(q.lower(),))
    r=cur.fetchone()
    if not r: return None
    cur.execute("SELECT file FROM songs WHERE id=?",(r[0],))
    r2=cur.fetchone()
    return r2[0] if r2 else None

def fmt_time(seconds):
    if seconds is None:
        return "00:00"

    seconds = int(seconds)

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"

    return f"{m:02d}:{s:02d}"

# ---------------- DOWNLOAD ----------------
def download(query):

    f=find_alias(query)
    if f: return f

    if query.startswith("http"):
        title=subprocess.check_output([
            "yt-dlp","--no-check-certificate","--proxy",PROXY,
            "--cookies",COOKIES,"--get-title",query
        ],text=True).strip()
        target=query
    else:
        title=subprocess.check_output([
            "yt-dlp","--no-check-certificate","--proxy",PROXY,
            "--cookies",COOKIES,"--get-title",f"ytsearch1:{query}"
        ],text=True).strip()
        target=f"ytsearch1:{query}"

    cur.execute("SELECT id,file FROM songs WHERE title=?",(title,))
    row=cur.fetchone()
    if row:
        song_id,file=row
        add_alias(song_id,query)
        return file

    subprocess.run([
        "yt-dlp","--no-playlist","--proxy",PROXY,"--cookies",COOKIES,
        "-q","-f","ba",
        "-o",str(OUTDIR/"%(title)s.%(ext)s"),
        target
    ],check=True)

    files=sorted(OUTDIR.glob("*"),key=os.path.getmtime,reverse=True)
    file=str(files[0])

    cur.execute("INSERT INTO songs(title,file) VALUES (?,?)",(title,file))
    song_id=cur.lastrowid
    add_alias(song_id,query)
    add_alias(song_id,title)

    conn.commit()
    return file

# ---------------- QUEUE ----------------
def queue_add(f):
    mpv(["loadfile",f,"append-play"])

def queue_next():
    mpv(["playlist-next"])

def queue_list():
    playlist_resp = mpv_query(["get_property", "playlist"])

    playlist = playlist_resp.get("data")

    if not playlist:
        print("Queue empty")
        return

    upcoming = []
    current_found = False

    for item in playlist:
        if item.get("current"):
            current_found = True
            upcoming.append(item)
            continue

        if current_found:
            upcoming.append(item)

    if not upcoming:
        print("Queue empty.")
        return

    print("📋 Queue")

    for i, item in enumerate(upcoming, 1):
        title = os.path.basename(item["filename"])
        print(f"{i}. {title}")

# ---------------- COMMANDS ----------------
def add(q):
    f=download(q)
    queue_add(f)
    print("Queued:",os.path.basename(f))

def pause(): mpv(["cycle","pause"])
def resume(): mpv(["set_property","pause",False])
def stop(): mpv(["stop"])
def seek(s): mpv(["seek",s,"relative"])

def loop(on=True):
    mpv(["set_property","loop","inf" if on else "no"])

def nowplaying():
    path_resp = mpv_query(["get_property", "path"])
    pos_resp = mpv_query(["get_property", "playback-time"])
    dur_resp = mpv_query(["get_property", "duration"])

    path = path_resp.get("data")
    pos = pos_resp.get("data")
    dur = dur_resp.get("data")

    if not path:
        print("Nothing playing")
        return

    title = os.path.basename(path)

    print(f"🎵 {title}")
    print(f"⏱ {fmt_time(pos)} / {fmt_time(dur)}")

def status():
    path_resp = mpv_query(["get_property", "path"])
    pos_resp = mpv_query(["get_property", "playback-time"])
    dur_resp = mpv_query(["get_property", "duration"])
    pause_resp = mpv_query(["get_property", "pause"])
    loop_resp = mpv_query(["get_property", "loop"])

    path = path_resp.get("data")
    pos = pos_resp.get("data")
    dur = dur_resp.get("data")
    paused = pause_resp.get("data")
    loop_val = loop_resp.get("data")

    if not path:
        print("Nothing is playing")
        return

    title = os.path.basename(path)

    play_status = "⏸ Paused" if paused else "▶ Playing"
    loop_status = "ON" if loop_val not in ("no", 0, False) else "OFF"

    print(f"{play_status}")
    print(f"🎵 {title}")
    print(f"⏱ {fmt_time(pos)} / {fmt_time(dur)}")
    print(f"🔁 Loop: {loop_status}")

def search(text):
    text = text.lower().strip()

    cur.execute("SELECT query, title, file FROM songs")
    rows = cur.fetchall()

    results = []

    for q, title, file in rows:
        hay_q = (q or "").lower()
        hay_t = (title or "").lower()

        score = 0

        # substring match instead of word match
        if text in hay_q:
            score += 2
        if text in hay_t:
            score += 3

        if score > 0:
            results.append((score, q, title, file))

    if not results:
        print("No matches found")
        return

    results.sort(reverse=True)

    print("🔎 Results:")

    for i, (_, q, title, file) in enumerate(results[:10], 1):
        name = title or q or os.path.basename(file)
        print(f"{i}. {name}")

    try:
        choice = input("\nPlay which number? (Enter = cancel): ").strip()
        if not choice:
            return

        idx = int(choice) - 1
        if 0 <= idx < len(results[:10]):
            _, _, _, file = results[idx]
            queue_add(file)
            print(f"Queued: {os.path.basename(file)}")

    except:
        pass

# ---------------- MAIN ----------------
def main():
    ensure_mpv()
    if len(sys.argv)<2:
        print("Usage: mmb add(a) | pause(p) | resume(r) | next(n) | stop(s) | queue(q) | seek(sk) | loop(l) | nowplaying(np) | search(sr)")
        return

    c=sys.argv[1]; a=sys.argv[2:]

    if c in ("add","a") and a:
        add(" ".join(a))
    elif c in ("pause","p"): pause()
    elif c in ("resume","r"): resume()
    elif c in ("stop","s"): stop()
    elif c in ("queue","q"): queue_list()
    elif c in ("status","st"): status()
    elif c in ("nowplaying", "np"): nowplaying()
    elif c in ("next", "n"): queue_next()
    elif c in ("seek","sk") and a: seek(float(a[0]))
    elif c in ("search", "sr") and a: search(" ".join(a))
    elif c in ("loop", "l"): loop(True)
    elif c in ("loopoff", "lo"): loop(False)
    else:
        print("Unknown command!")

if __name__=="__main__":
    main()
