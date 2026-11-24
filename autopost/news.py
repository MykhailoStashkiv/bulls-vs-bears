"""BullBear: AI-огляд ринку кожні кілька годин.

Збирає свіжі заголовки з крипто-RSS, рух BTC/ETH/SOL з Kraken,
просить ШІ (Claude) написати короткий двомовний огляд і публікує
в Telegram-канал. Без ключа ШІ публікує просто добірку новин.

Змінні середовища:
  TG_TOKEN, TG_CHANNEL     — бот і канал
  ANTHROPIC_API_KEY        — ключ ШІ (необов'язково)
  AI_MODEL                 — модель (за замовчуванням claude-haiku-5-5)
  DRY_RUN=1                — нічого не надсилати
"""
import email.utils
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
]
PAIRS = [("BTC", "XBTUSD"), ("ETH", "ETHUSD"), ("SOL", "SOLUSD")]
STATE = os.path.join(os.path.dirname(__file__), "posted_news.json")
BOT_URL = "https://t.me/BullBearLiveBot"
UA = {"User-Agent": "Mozilla/5.0 (BullBear news bot)"}


def fetch(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def news(hours=6):
    cutoff = time.time() - hours * 3600
    out = []
    for src, url in FEEDS:
        try:
            root = ET.fromstring(fetch(url))
        except Exception as e:  # noqa: BLE001
            print("RSS не вдався:", src, e)
            continue
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            pub = it.findtext("pubDate") or ""
            try:
                ts = email.utils.parsedate_to_datetime(pub).timestamp()
            except Exception:  # noqa: BLE001
                ts = time.time()
            desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")
            desc = html.unescape(re.sub(r"\s+", " ", desc)).strip()[:300]
            if title and link and ts >= cutoff:
                out.append({"src": src, "title": html.unescape(title), "link": link, "ts": ts, "desc": desc})
    out.sort(key=lambda x: -x["ts"])
    return out


def market():
    res = {}
    for name, pair in PAIRS:
        try:
            d = json.loads(fetch(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=60"))
            rows = next(v for k, v in d["result"].items() if k != "last")
            last = float(rows[-1][4])
            p4 = float(rows[-5][4])
            p24 = float(rows[-25][4])
            res[name] = {"price": last, "ch4": (last / p4 - 1) * 100, "ch24": (last / p24 - 1) * 100}
        except Exception as e:  # noqa: BLE001
            print("Kraken не вдався:", name, e)
    return res


def ai_text(items, mk):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    heads = "\n".join(f"- [{x['src']}] {x['title']} — {x['desc'][:180]}" for x in items[:12])
    mkt = "\n".join(f"{k}: {v['price']:,.2f} USD, 4h {v['ch4']:+.2f}%, 24h {v['ch24']:+.2f}%" for k, v in mk.items())
    prompt = f"""You write posts for a Telegram channel "BullBear Live" (bulls vs bears crypto battle game).
Use ONLY the data and headlines below. Do not invent facts, numbers, quotes or events.
No financial advice: never tell readers to buy, sell or hold.

MARKET (Kraken):
{mkt}

HEADLINES (last hours):
{heads}

Write the post in this exact structure, plain text, emojis welcome, max 1100 characters total:
1) One punchy title line about who is winning right now: bulls 🐂 or bears 🐻 (based on the price changes).
2) "🔥 Top stories" — the 3 most important headlines, one line each, rewritten short (no links).
3) "🧠 AI take" — 2 sentences: what these stories may mean for market mood. Neutral, careful.
4) "🇺🇦" — the same title + AI take in Ukrainian, 2–3 short lines.
Return only the post text."""
    body = json.dumps({
        "model": os.environ.get("AI_MODEL", "claude-haiku-5-5"),
        "max_tokens": 700,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    try:
        r = json.loads(fetch("https://api.anthropic.com/v1/messages", data=body, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, timeout=60))
        return "".join(b.get("text", "") for b in r.get("content", [])).strip()
    except urllib.error.HTTPError as e:
        print("ШІ не відповів:", e.code, e.read()[:300])
    except Exception as e:  # noqa: BLE001
        print("ШІ не відповів:", e)
    return None


def plain_text(items, mk):
    up = sum(v["ch4"] for v in mk.values()) >= 0 if mk else True
    lines = ["<b>" + ("🐂 Bulls lead the last 4h" if up else "🐻 Bears lead the last 4h") + "</b>", ""]
    lines.append("🔥 <b>Top crypto stories / Головні новини:</b>")
    for x in items[:5]:
        lines.append(f"• {html.escape(x['title'])}")
    return "\n".join(lines)


def build(items, mk, ai):
    head = []
    big = [k for k, v in mk.items() if abs(v["ch4"]) >= 3]
    if big:
        head.append("🚨 <b>BIG MOVE:</b> " + ", ".join(f"{k} {mk[k]['ch4']:+.1f}% in 4h" for k in big))
        head.append("")
    body = html.escape(ai) if ai else plain_text(items, mk)
    mline = " · ".join(f"{k} {v['price']:,.0f} ({v['ch24']:+.1f}% 24h)" for k, v in mk.items())
    src = "\n".join(f'<a href="{html.escape(x["link"])}">{html.escape(x["src"])}: {html.escape(x["title"][:70])}</a>' for x in items[:3])
    tail = ["", "📊 " + mline, "", "🔗 " + src.replace("\n", "\n🔗 "), "",
            "⚔️ Out-tap a friend in 60 s 👉 @BullBearLiveBot",
            "<i>" + ("AI-generated summary of public news. " if ai else "Public crypto news digest. ") + "Not financial advice.</i>"]
    return "\n".join(head) + body + "\n" + "\n".join(tail)


def send(token, chat, text):
    markup = json.dumps({"inline_keyboard": [[{"text": "⚔️ Play / Грати", "url": BOT_URL}]]})
    data = urllib.parse.urlencode({"chat_id": chat, "text": text[:4000], "parse_mode": "HTML",
                                   "reply_markup": markup, "disable_web_page_preview": "true"}).encode()
    for attempt in range(3):
        try:
            r = json.loads(fetch(f"https://api.telegram.org/bot{token}/sendMessage", data=data))
            if r.get("ok"):
                return True
            print(r)
            return False
        except urllib.error.HTTPError as e:
            info = {}
            try:
                info = json.loads(e.read())
            except Exception:  # noqa: BLE001
                pass
            print("Telegram:", e.code, info.get("description"))
            if e.code != 429 or attempt == 2:
                return False
            time.sleep(min(90, int((info.get("parameters") or {}).get("retry_after", 30)) + 2))
    return False


def main():
    try:
        seen = set(json.load(open(STATE)))
    except Exception:  # noqa: BLE001
        seen = set()
    items = [x for x in news() if x["link"] not in seen]
    mk = market()
    if len(items) < 2:
        print("мало свіжих новин, пропускаю")
        return
    ai = ai_text(items, mk)
    text = build(items, mk, ai)
    print(text)
    if os.environ.get("DRY_RUN") == "1":
        return
    token = os.environ.get("TG_TOKEN")
    if not token:
        sys.exit("TG_TOKEN не задано")
    if send(token, os.environ.get("TG_CHANNEL", "@BullBearLive"), text):
        print("опубліковано")
        seen |= {x["link"] for x in items[:12]}
        json.dump(sorted(seen)[-400:], open(STATE, "w"))


if __name__ == "__main__":
    main()
