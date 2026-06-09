
# Mammad's music bot
A cli music bot which searches, downloads and plays song from Youtube.

## Install

Install the prerequisites:

```bash
    sudo apt install mpv python3
    python3 -m pip install -U yt-dlp
```

Bot downloads songs in ```~/Music``` by default and uses them if needed again. If you wanna change it just open mmb.py in an editor and replace it with your desired path.

Also if Youtube is filterd on your internet, you have to run a socks proxy on port **2080** and use the bot. Otherwise open mmb.py and remove any ```--proxy, PROXY``` from file.

NOTE: You have to download your cookies from browser and set 'COOKIES' value in the mmb.py to cookies.txt path.

To install this project run:

```bash
    git clone https://github.com/ML-Lorentz/Mammad-s-music-bot.git
    cd Mammad-s-music-bot
    sudo chmod +x mmb.py
    mkdir -p /usr/local/bin
    sudo cp mmb.py /usr/local/bin/mmb
```
## Usage

For help:
```bash
mmb
```

#### Commands:

- add(a): Add a song to queue ``` mmb add "Music name"``` or ```mmb add <url>```
- pause(p): Pauses the player
- resume(r): Resume the player
- next(n): Skip currently playing song
- stop(s): Stop and clear the queue
- queue(q): List upcoming queue
- seek(sk): Seek forward or backward (negative or positive number in sec)
- loop(l): Turn loop on
- loop(l) off: Turn loop off
- status(st): Show bot status
