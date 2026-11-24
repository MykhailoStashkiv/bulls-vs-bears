"""BullBear: щогодинний «звіт битви» в Telegram-канал.

Бере угоди BTC/USD з Kraken за останню годину, малює картинку
(ціна + бульбашки великих угод + шкала тиску) і публікує в канал
через бота. Запускається GitHub Actions за розкладом.

Змінні середовища:
  TG_TOKEN    — токен бота (GitHub secret)
  TG_CHANNEL  — канал, наприклад @BullBearLive
  DRY_RUN=1   — лише намалювати картинку, нічого не надсилати
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

PAIR = "XBTUSD"
COIN = "BTC"
HOUR = 3600
BOT_URL = "https://t.me/BullBearLiveBot"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "BullBear/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_trades(seconds=HOUR):
    """Угоди за останні `seconds`: (час, ціна, кількість, купівля?)."""
    start = time.time() - seconds
    since = int(start * 1e9)
    out = []
    for _ in range(40):
        d = get(f"https://api.kraken.com/0/public/Trades?pair={PAIR}&since={since}")
        if d.get("error"):
            raise RuntimeError(d["error"])
        res = d["result"]
        key = next(k for k in res if k != "last")
        rows = res[key]
        for p, v, t, side, *_ in rows:
            if float(t) >= start:
                out.append((float(t), float(p), float(v), side == "b"))
        new_since = int(res["last"])
        if not rows or new_since == since or float(rows[-1][2]) >= time.time() - 5:
            break
        since = new_since
        time.sleep(1.1)
    return out


def fu(u):
    if u >= 1e6:
        return f"${u / 1e6:.2f}M"
    if u >= 1e3:
        return f"${u / 1e3:.0f}K" if u >= 1e5 else f"${u / 1e3:.1f}K"
    return f"${u:.0f}"


def analyze(trades):
    usd = sorted(p * v for _, p, v, _ in trades)
    big = usd[int(len(usd) * 0.98)] if len(usd) > 50 else (usd[-1] if usd else 0)
    buy = sum(p * v for _, p, v, b in trades if b)
    sell = sum(p * v for _, p, v, b in trades if not b)
    p0, p1 = trades[0][1], trades[-1][1]
    hi = max(t[1] for t in trades)
    lo = min(t[1] for t in trades)
    whales = sorted(trades, key=lambda t: t[1] * t[2], reverse=True)[:3]
    return dict(buy=buy, sell=sell, p0=p0, p1=p1, hi=hi, lo=lo, big=big, whales=whales, n=len(trades))


def draw(trades, a, path):
    bg, g, r, y = "#07080c", "#00f593", "#ff3b5c", "#ffc43d"
    fig = plt.figure(figsize=(12, 6.75), dpi=100, facecolor=bg)
    ax = fig.add_axes([0.06, 0.2, 0.86, 0.62], facecolor=bg)
    t0 = trades[0][0]
    xs = [(t - t0) / 60 for t, *_ in trades]
    ax.plot(xs, [p for _, p, _, _ in trades], color="#e8eaf0", lw=1.4, alpha=0.85)
    bx = [(t - t0) / 60 for t, p, v, b in trades if p * v >= a["big"]]
    by = [p for t, p, v, b in trades if p * v >= a["big"]]
    bs = [min(1400, 40 + (p * v / a["big"]) * 60) for t, p, v, b in trades if p * v >= a["big"]]
    bc = [g if b else r for t, p, v, b in trades if p * v >= a["big"]]
    ax.scatter(bx, by, s=bs, c=bc, alpha=0.45, edgecolors=bc, linewidths=1.2, zorder=3)
    for t, p, v, b in a["whales"][:1]:
        ax.scatter([(t - t0) / 60], [p], s=900, facecolors="none", edgecolors=y, linewidths=2.5, zorder=4)
    ax.tick_params(colors="#8a90a2", labelsize=10)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.grid(color="#ffffff", alpha=0.05)
    ax.set_xlabel("хвилини тому →  зараз", color="#8a90a2", fontsize=10)
    ax.yaxis.tick_right()

    tot = a["buy"] + a["sell"] or 1
    bp = a["buy"] / tot
    chg = (a["p1"] / a["p0"] - 1) * 100
    win = "БИКИ" if bp >= 0.5 else "ВЕДМЕДІ"
    fig.text(0.06, 0.9, f"{COIN}  {a['p1']:,.2f}", color="#e8eaf0", fontsize=30, weight="bold")
    fig.text(0.06, 0.845, f"{'+' if chg >= 0 else '−'}{abs(chg):.2f}% за годину", color=g if chg >= 0 else r, fontsize=14, weight="bold")
    fig.text(0.92, 0.9, f"ГОДИНУ ВИГРАЛИ {win}", color=g if bp >= 0.5 else r, fontsize=22, weight="bold", ha="right")
    fig.text(0.92, 0.85, "BullBear · Kraken", color="#8a90a2", fontsize=11, ha="right")

    bar = fig.add_axes([0.06, 0.07, 0.86, 0.05])
    bar.barh([0], [bp], color=g, height=1)
    bar.barh([0], [1 - bp], left=[bp], color=r, height=1)
    bar.axvline(0.5, color="white", lw=2, alpha=0.7)
    bar.set_xlim(0, 1)
    bar.axis("off")
    fig.text(0.06, 0.13, f"Бики {bp * 100:.0f}% · {fu(a['buy'])}", color=g, fontsize=13, weight="bold")
    fig.text(0.92, 0.13, f"{fu(a['sell'])} · {(1 - bp) * 100:.0f}% Ведмеді", color=r, fontsize=13, weight="bold", ha="right")
    fig.savefig(path, facecolor=bg)
    plt.close(fig)


def caption(a):
    tot = a["buy"] + a["sell"] or 1
    bp = round(a["buy"] / tot * 100)
    chg = (a["p1"] / a["p0"] - 1) * 100
    head = "🐂 Годину виграли БИКИ!" if bp >= 50 else "🐻 Годину виграли ВЕДМЕДІ!"
    lines = [
        f"<b>{head}</b>",
        "",
        f"{COIN}: <b>{a['p1']:,.2f}</b> ({'+' if chg >= 0 else '−'}{abs(chg):.2f}% за годину)",
        f"🐂 {bp}% · {fu(a['buy'])}   vs   {fu(a['sell'])} · {100 - bp}% 🐻",
        f"Діапазон: {a['lo']:,.0f} – {a['hi']:,.0f}",
        "",
        "🐋 <b>Найбільші кити години:</b>",
    ]
    for t, p, v, b in a["whales"]:
        lines.append(f"{'🟢 купив' if b else '🔴 продав'} {v:.2f} {COIN} · {fu(p * v)} по {p:,.0f}")
    lines += ["", "Дивись битву наживо 👇", "<i>Візуалізація ринку, не торгова порада.</i>"]
    return "\n".join(lines)


def send(token, chat, photo, text):
    boundary = "----bullbear" + str(int(time.time()))
    markup = json.dumps({"inline_keyboard": [[{"text": "⚔️ Відкрити битву", "url": BOT_URL}]]})
    fields = {"chat_id": chat, "caption": text, "parse_mode": "HTML", "reply_markup": markup}
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    with open(photo, "rb") as f:
        img = f.read()
    body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"battle.png\"\r\nContent-Type: image/png\r\n\r\n".encode() + img + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read())
    if not res.get("ok"):
        raise RuntimeError(res)


def main():
    trades = fetch_trades()
    if len(trades) < 20:
        print("замало угод, пропускаю")
        return
    a = analyze(trades)
    draw(trades, a, "battle.png")
    text = caption(a)
    print(text)
    if os.environ.get("DRY_RUN") == "1":
        return
    token = os.environ.get("TG_TOKEN")
    if not token:
        sys.exit("TG_TOKEN не задано")
    send(token, os.environ.get("TG_CHANNEL", "@BullBearLive"), "battle.png", text)
    print("опубліковано")


if __name__ == "__main__":
    main()
