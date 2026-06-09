#!/usr/bin/env python3
import os
import sys
import json
import time
import sqlite3
import socket
import subprocess
from pathlib import Path

# ---------------- CONFIG ----------------
OUTDIR = Path.home() / "Music"
DB_PATH = Path.home() / ".songbot.db"
SOCK = "/tmp/songbot.sock"
PLAYER = "mpv"
PROXY = "socks5://127.0.0.1:2080"
COOKIES = "/path/to/cookies.txt"

OUTDIR.mkdir(parents=True, exist_ok=True)

# ---------------- DB ----------------
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS songs (
    id INTEGER PRIMARY KEY,
    title TEXT,
    file TEXT UNIQUE
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file TEXT
)
""")
conn.commit()

# ---------------- MPV DAEMON ----------------
def ensure_mpv():
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(SOCK)
        s.close()
        return
    except Exception:
        pass

    try:
        os.unlink(SOCK)
    except FileNotFoundError:
        pass

    subprocess.Popen(
        [
            PLAYER,
            "--idle=yes",
            "--no-video",
            "--quiet",
            "--no-terminal",
            "--input-ipc-server=" + SOCK,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for _ in range(50):
        if os.path.exists(SOCK):
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(SOCK)
                s.close()
                return
            except Exception:
                pass

        time.sleep(0.1)

    raise RuntimeError("Failed to start mpv")

def mpv(cmd):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    s.send((json.dumps({"command": cmd}) + "\n").encode())
    s.close()

def mpv_query(cmd):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)

    s.send((json.dumps({"command": cmd}) + "\n").encode())

    data = s.recv(8192).decode()
    s.close()

    return json.loads(data)

# ---------------- HELPERS ----------------
def normalize(s):
    return "".join(c.lower() if c.isalnum() else " " for c in s).split()

def find_existing(title):
    words = set(normalize(title))
    cur.execute("SELECT file FROM songs")
    for (f,) in cur.fetchall():
        if words.issubset(set(normalize(f))):
            return f
    return None

def download(query):
    if query.startswith("http"):
        title = subprocess.check_output(
            ["yt-dlp", "--no-check-certificate", "--proxy", PROXY, "--cookies", COOKIES, "--get-title", query],
            text=True
        ).strip()
        target = query
    else:
        title = subprocess.check_output(
            ["yt-dlp", "--no-check-certificate", "--proxy", PROXY, "--cookies", COOKIES, "--get-title", f"ytsearch1:{query}"],
            text=True
        ).strip()
        target = f"ytsearch1:{query}"

    existing = find_existing(title)
    if existing:
        return existing

    print(f"Downloading: {title}")

    # silent download
    subprocess.run([
        "yt-dlp",
        "--no-check-certificate", 
        "--proxy", PROXY,
        "--no-playlist",
        "--cookies", COOKIES,
        "-q",
        "-f", "ba",
        "--audio-quality", "0",
        "-o", str(OUTDIR / "%(title)s.%(ext)s"),
        target
    ], check=True)

    # find newest file
    files = sorted(
                OUTDIR.glob("*"),
                key=os.path.getmtime,
                reverse=True
            )
    file = str(files[0])

    cur.execute("INSERT OR IGNORE INTO songs(title, file) VALUES (?, ?)", (title, file))
    conn.commit()

    return file

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

# ---------------- QUEUE ----------------
def queue_add(file):
    cur.execute("INSERT INTO queue(file) VALUES (?)", (file,))
    conn.commit()
    mpv(["loadfile", file, "append-play"])

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
def add(query):
    file = download(query)
    queue_add(file)
    print(f"Queued: {file}")

def pause():
    mpv(["cycle", "pause"])

def resume():
    mpv(["set_property", "pause", False])

def stop():
    mpv(["stop"])

def seek(seconds):
    mpv(["seek", seconds, "relative"])

def loop(on=True):
    val = "inf" if on else "no"
    mpv(["set_property", "loop", val])

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
    # Current playing file
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

    if path:
        title = os.path.basename(path)
        play_status = "⏸ Paused" if paused else "▶ Playing"
        loop_status = "ON" if loop_val not in ("no", 0, False) else "OFF"

        print(f"{play_status}")
        print(f"🎵 {title}")
        print(f"⏱ {fmt_time(pos)} / {fmt_time(dur)}")
        print(f"🔁 Loop: {loop_status}")
    else:
        print("Nothing is playing")

# ---------------- MAIN ----------------
def main():
    ensure_mpv()

    if len(sys.argv) < 2:
        print("Usage: mmb add(a) | pause(p) | resume(r) | next(n) | stop(s) | queue(q) | seek(sk) | loop(l) | status(st) <args>")
        return

    cmd = sys.argv[1].lower()
    args = sys.argv[2:]

    if cmd == "add" or cmd == "a" and args:
        add(" ".join(args))
    elif cmd == "pause" or cmd == "p":
        pause()
    elif cmd == "resume" or cmd == "r":
        resume()
    elif cmd == "next" or cmd == "n":
        queue_next()
    elif cmd == "stop" or cmd == "s":
        stop()
    elif cmd == "queue" or cmd == "q":
        queue_list()
    elif cmd == "nowplaying" or cmd == "np":
        nowplaying()
    elif cmd == "status" or cmd == "st":
        status()
    elif cmd == "seek" or cmd == "sk" and args:
        try:
            sec = float(args[0])
            seek(sec)
        except ValueError:
            print("Seek argument must be seconds (number)")
    elif cmd == "loop" or cmd == "l":
        on = True
        if args and args[0].lower() in ("off", "0", "false"):
            on = False
        loop(on)
    else:
        print("Unknown command. Available: add(a), pause(p), resume(r), next(n), stop(s), queue(q), seek(sk), loop(l), status(st)")

if __name__ == "__main__":
    main()
