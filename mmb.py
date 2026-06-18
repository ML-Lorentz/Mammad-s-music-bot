#!/usr/bin/env python3
import os, sys, json, time, sqlite3, socket, subprocess
from pathlib import Path

# ---------------- CONFIG ----------------
OUTDIR = Path.home() / "Music"
DB_PATH = Path.home() / ".songbot.db"
SOCK = "/tmp/songbot.sock"
PLAYER = "mpv"
PROXY = "socks5://127.0.0.1:2080"
COOKIES = "/path/to/your/cookies.txt"

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
    cur.execute("""
    CREATE TABLE IF NOT EXISTS playlists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS playlist_songs (
        playlist_id INTEGER,
        song_id INTEGER,
        position INTEGER,
        PRIMARY KEY (playlist_id, position)
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

def youtube_search(query):
    lines = subprocess.check_output(
        [
            "yt-dlp",
            "--proxy", PROXY,
            "--cookies", COOKIES,
            "--no-check-certificate",
            "--print",
            "%(title)s|||%(webpage_url)s|||%(duration_string)s",
            f"ytsearch5:{query}",
        ],
        text=True,
    ).splitlines()

    if not lines:
        print("No results.")
        return

    results = []

    print("\n🔎 YouTube results\n")

    for i, line in enumerate(lines, 1):
        try:
            title, url, dur = line.split("|||")
        except ValueError:
            continue

        results.append((title, url))
        print(f"{i}. {title} ({dur})")

    choice = input("\nChoose [1-5] (Enter=cancel): ").strip()

    if not choice:
        return

    try:
        idx = int(choice) - 1
        if idx < 0 or idx >= len(results):
            return
    except:
        return

    selected_title, selected_url = results[idx]
    alias = input("Alias (Enter to skip): ").strip()

    file = download(selected_url)

    cur.execute(
            "SELECT id FROM songs WHERE file=?",
            (file,)
            )
    row = cur.fetchone()

    if row:
        if alias:
            add_alias(row[0], alias)

    queue_add(file)
    print(f"Queued: {file}")

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

# ---------------- PLAYLISTS ----------------

def playlist_create(name):
    cur.execute(
        "INSERT OR IGNORE INTO playlists(name) VALUES (?)",
        (name,)
    )
    conn.commit()
    print(f"Created playlist: {name}")

def playlist_list():
    cur.execute("SELECT name FROM playlists ORDER BY name")

    rows = cur.fetchall()

    if not rows:
        print("No playlists")
        return

    print("📚 Playlists")

    for i, (name,) in enumerate(rows, 1):
        print(f"{i}. {name}")

def playlist_show(name):
    cur.execute(
        """
        SELECT s.title
        FROM playlist_songs ps
        JOIN playlists p ON p.id = ps.playlist_id
        JOIN songs s ON s.id = ps.song_id
        WHERE p.name = ?
        ORDER BY ps.position
        """,
        (name,)
    )

    rows = cur.fetchall()

    if not rows:
        print("Playlist empty")
        return

    print(f"📋 {name}")

    for i, (title,) in enumerate(rows, 1):
        print(f"{i}. {title}")

def playlist_add(name, query):
    file = download(query)

    cur.execute(
        "SELECT id FROM songs WHERE file=?",
        (file,)
    )

    row = cur.fetchone()

    if not row:
        print("Song not found")
        return

    song_id = row[0]

    cur.execute(
        "SELECT id FROM playlists WHERE name=?",
        (name,)
    )

    row = cur.fetchone()

    if not row:
        print("Playlist does not exist")
        return

    playlist_id = row[0]

    cur.execute(
        """
        SELECT 1
        FROM playlist_songs
        WHERE playlist_id=? AND song_id=?
        """,
        (playlist_id, song_id)
    )

    if cur.fetchone():
        print("Song already in playlist")
        return

    cur.execute(
        """
        SELECT COALESCE(MAX(position),0)+1
        FROM playlist_songs
        WHERE playlist_id=?
        """,
        (playlist_id,)
    )

    pos = cur.fetchone()[0]

    cur.execute(
        """
        INSERT INTO playlist_songs
        (playlist_id, song_id, position)
        VALUES (?, ?, ?)
        """,
        (playlist_id, song_id, pos)
    )

    conn.commit()

    print(f"Added to {name}")

def playlist_save(name):

    # create playlist if it doesn't exist
    cur.execute(
        "INSERT OR IGNORE INTO playlists(name) VALUES (?)",
        (name,)
    )
    conn.commit()

    cur.execute(
        "SELECT id FROM playlists WHERE name=?",
        (name,)
    )

    row = cur.fetchone()

    if not row:
        print("Failed to create playlist")
        return

    playlist_id = row[0]

    playlist_resp = mpv_query(["get_property", "playlist"])
    playlist = playlist_resp.get("data")

    if not playlist:
        print("Queue empty")
        return

    # current song + upcoming songs only
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
        print("Queue empty")
        return

    added = 0
    skipped = 0

    for item in upcoming:

        file = item["filename"]

        cur.execute(
            "SELECT id FROM songs WHERE file=?",
            (file,)
        )

        row = cur.fetchone()

        if not row:
            continue

        song_id = row[0]

        # skip duplicates
        cur.execute(
            """
            SELECT 1
            FROM playlist_songs
            WHERE playlist_id=? AND song_id=?
            """,
            (playlist_id, song_id)
        )

        if cur.fetchone():
            skipped += 1
            continue

        cur.execute(
            """
            SELECT COALESCE(MAX(position),0)+1
            FROM playlist_songs
            WHERE playlist_id=?
            """,
            (playlist_id,)
        )

        pos = cur.fetchone()[0]

        cur.execute(
            """
            INSERT INTO playlist_songs
            (playlist_id, song_id, position)
            VALUES (?, ?, ?)
            """,
            (playlist_id, song_id, pos)
        )

        added += 1

    conn.commit()

    print(f"📋 Playlist: {name}")
    print(f"➕ Added: {added}")
    print(f"⏭ Skipped: {skipped}")

def playlist_play(name):
    cur.execute(
        """
        SELECT s.file
        FROM playlist_songs ps
        JOIN playlists p ON p.id = ps.playlist_id
        JOIN songs s ON s.id = ps.song_id
        WHERE p.name=?
        ORDER BY ps.position
        """,
        (name,)
    )

    rows = cur.fetchall()

    if not rows:
        print("Playlist empty")
        return

    for (file,) in rows:
        queue_add(file)

    print(f"Queued playlist: {name}")

def playlist_remove(name, number):

    cur.execute(
        """
        SELECT ps.playlist_id, ps.position
        FROM playlist_songs ps
        JOIN playlists p ON p.id = ps.playlist_id
        WHERE p.name=?
        ORDER BY ps.position
        """,
        (name,)
    )

    rows = cur.fetchall()

    if not rows:
        print("Playlist empty")
        return

    idx = number - 1

    if idx < 0 or idx >= len(rows):
        print("Invalid song number")
        return

    playlist_id, position = rows[idx]

    cur.execute(
        """
        DELETE FROM playlist_songs
        WHERE playlist_id=? AND position=?
        """,
        (playlist_id, position)
    )

    cur.execute(
        """
        UPDATE playlist_songs
        SET position = position - 1
        WHERE playlist_id=? AND position > ?
        """,
        (playlist_id, position)
    )

    conn.commit()

    print(f"Removed song #{number} from {name}")

def playlist_delete(name):
    cur.execute(
        "SELECT id FROM playlists WHERE name=?",
        (name,)
    )

    row = cur.fetchone()

    if not row:
        print("Playlist not found")
        return

    pid = row[0]

    cur.execute(
        "DELETE FROM playlist_songs WHERE playlist_id=?",
        (pid,)
    )

    cur.execute(
        "DELETE FROM playlists WHERE id=?",
        (pid,)
    )

    conn.commit()

    print(f"Deleted playlist: {name}")

# ---------------- MAIN ----------------
def main():
    ensure_mpv()
    if len(sys.argv)<2:
        print("Usage: mmb add(a) | pause(p) | resume(r) | next(n) | stop(s) | queue(q) | seek(sk) | loop(l) | nowplaying(np) | search(sr) | ytsearch(y)")
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
    elif c in ("ytsearch", "y") and a: youtube_search(" ".join(a))
    elif c == "pl":

        if not a:
            playlist_list()

        elif a[0] == "create" and len(a) >= 2:
            playlist_create(a[1])

        elif a[0] == "show" and len(a) >= 2:
            playlist_show(a[1])

        elif a[0] == "play" and len(a) >= 2:
            playlist_play(a[1])

        elif a[0] == "delete" and len(a) >= 2:
            playlist_delete(a[1])

        elif a[0] == "add" and len(a) >= 3:
            playlist_add(a[1], " ".join(a[2:]))

        elif a[0] == "save" and len(a) >= 2:
            playlist_save(a[1])

        elif a[0] == "rm" and len(a) >= 3:
            try:
                playlist_remove(a[1], int(a[2]))
            except ValueError:
                print("Song number must be a number")

        else:
            print("Usage:")
            print("  mmb pl")
            print("  mmb pl create NAME")
            print("  mmb pl show NAME")
            print("  mmb pl play NAME")
            print("  mmb pl delete NAME")
            print("  mmb pl add NAME SONG")
            print("  mmb pl rm NAME NUMBER")
            print("  mmb pl save NAME")

    else:
        print("Unknown command!")

if __name__=="__main__":
    main()
