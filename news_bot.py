import asyncio
import glob
import hashlib
import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from html import unescape
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont, features

try:
    import edge_tts
except ImportError:
    edge_tts = None

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODELS = []
for _m in (os.environ.get("GEMINI_MODEL"), "gemini-flash-latest",
           "gemini-flash-lite-latest"):
    if _m and _m not in MODELS:
        MODELS.append(_m)

FB_TOKEN = os.environ.get("FB_PAGE_TOKEN", "").strip()
FB_PAGE = os.environ.get("FB_PAGE_ID", "").strip()
GRAPH = "https://graph.facebook.com"

OUT = "news_out"
STATE = "posted.json"
MAX_NEWS = int(os.environ.get("MAX_NEWS") or 6)
MAX_REELS = int(os.environ.get("MAX_REELS") or 99)
MAX_AGE_HOURS = 30
VOICE = "bn-BD-NabanitaNeural"

# If a feed address stops working, the bot simply skips it. Add or remove lines freely.
FEEDS = [
    ("বিবিসি বাংলা", "https://feeds.bbci.co.uk/bengali/rss.xml"),
    ("প্রথম আলো", "https://www.prothomalo.com/feed"),
    ("ডয়চে ভেলে বাংলা", "https://rss.dw.com/xml/rss-bn-all"),
    ("যুগান্তর", "https://www.jugantor.com/feed/rss.xml"),
    ("ইত্তেফাক", "https://www.ittefaq.com.bd/rss/rss.xml"),
    ("বাংলা ট্রিবিউন", "https://www.banglatribune.com/feed"),
]

AL_WORDS = ("আওয়ামী", "আওয়ামি", "awami")
AL_CONTEXT = ("উল্লেখ্য, অন্তর্বর্তী সরকার দলটির কার্যক্রমের ওপর নিষেধাজ্ঞা জারি করেছে; "
              "দলটি ও তার সমর্থকরা এই সিদ্ধান্তকে অবৈধ বলছেন।")

PROMPT = """তুমি একটি বাংলা সংবাদ পেজের সম্পাদক। নিচের খবরের সূত্র থেকে ফেসবুক পোস্টের লেখা তৈরি করো।

সূত্র: {source}
শিরোনাম: {title}
বিবরণ: {desc}

কঠোর নিয়ম:
- শুধু উপরের শিরোনাম ও বিবরণে থাকা তথ্য ব্যবহার করো। নিজে থেকে কোনো নাম, সংখ্যা, তারিখ বা দাবি যোগ করবে না।
- নিজের ভাষায় লেখো, সূত্রের বাক্য হুবহু নকল করবে না।
- কোনো দল বা ব্যক্তিকে গালি, ঘৃণা বা উসকানিমূলক ভাষায় লিখবে না।
- কোনো দলের আইনি অবস্থা সম্পর্কে নিজের মতামত দেবে না। সূত্রে থাকলে "সূত্রের বক্তব্য অনুযায়ী" ধরনে দাবির উৎস উল্লেখ করো।
- বিবরণে একাধিক আলাদা খবর (সংক্ষিপ্ত খবরের তালিকা) থাকলে শুধু শিরোনামের খবর নিয়ে লেখো, অন্য খবরের কোনো তথ্য মেশাবে না।
- কোনো ব্যক্তি বা দলের অভিযোগ, দাবি বা মন্তব্য হলে শিরোনামে ও পয়েন্টে তার নাম উল্লেখ করে লেখো (যেমন "…বলেছেন অমুক"); অভিযোগকে নিজের কণ্ঠে সত্য হিসেবে লিখবে না।
- মতামত, বিশ্লেষণ বা ব্যাখ্যামূলক লেখা (খবর নয়) হলে skip=true দাও।
- image_prompt: ইংরেজিতে ১ লাইনে খবরের বিষয়ের একটি প্রতীকী ছবির বর্ণনা (যেমন "factory workers silhouette at sunrise, illustration"). কোনো আসল ব্যক্তি, মুখ, নেতা, লেখা বা লোগো থাকবে না। সাধারণ খবরে (খেলা, অর্থনীতি, শিক্ষা, স্বাস্থ্য, আবহাওয়া, বিনোদন, প্রযুক্তি ইত্যাদি) image_prompt অবশ্যই লিখবে। শুধু রাজনীতি, দল, সহিংসতা, অপরাধ, ধর্মীয় রায় বা সংবেদনশীল খবরে খালি ("") রাখবে।
- তথ্য খুব কম হলে, অথবা এটি খবর না হয়ে বিজ্ঞাপন বা আড্ডার লেখা হলে skip=true দাও।

শুধু JSON দাও:
{{
  "skip": false,
  "headline": "সর্বোচ্চ ৭০ অক্ষরের শিরোনাম",
  "points": ["প্রথম তথ্য, সর্বোচ্চ ২০ শব্দ", "দ্বিতীয় তথ্য", "তৃতীয় তথ্য (থাকলে)"],
  "post_text": "ফেসবুক পোস্টের মূল লেখা, ২-৪ বাক্য",
  "hashtags": ["#খবর", "#বাংলাদেশ"],
  "image_prompt": "ইংরেজি বর্ণনা, অথবা সংবেদনশীল হলে খালি"
}}"""


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def load_state():
    try:
        return set(json.load(open(STATE)))
    except Exception:
        return set()


def save_state(seen):
    json.dump(sorted(seen)[-3000:], open(STATE, "w"))


def key_of(link):
    return hashlib.sha1(link.encode()).hexdigest()[:16]


def fetch_items(seen):
    items = []
    now = datetime.now(timezone.utc)
    for name, url in FEEDS:
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 NewsBot"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as e:
            print(f"Feed skipped ({name}): {e}")
            continue
        count = 0
        for it in root.iter("item"):
            title = clean((it.findtext("title") or ""))
            link = (it.findtext("link") or "").strip()
            desc = clean(it.findtext("description") or "")
            when = None
            try:
                when = parsedate_to_datetime(it.findtext("pubDate") or "")
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except Exception:
                pass
            if not title or not link or key_of(link) in seen:
                continue
            if when and now - when > timedelta(hours=MAX_AGE_HOURS):
                continue
            items.append({"source": name, "title": title, "link": link, "desc": desc,
                          "when": when or now})
            count += 1
        print(f"Feed ok: {name} ({count} new items)")
    items.sort(key=lambda x: x["when"], reverse=True)
    return items


def write_post(item):
    body = {"contents": [{"parts": [{"text": PROMPT.format(
        source=item["source"], title=item["title"], desc=item["desc"][:1500] or "(নেই)")}]}],
        "generationConfig": {"responseMimeType": "application/json"}}
    last = None
    for rnd in range(3):
        for model in MODELS:
            url = ("https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{model}:generateContent?key={GEMINI_KEY}")
            try:
                r = requests.post(url, json=body, timeout=120)
                r.raise_for_status()
                data = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
                if data.get("skip"):
                    return None
                assert data["headline"] and data["points"] and data["post_text"]
                return data
            except Exception as e:
                last = e
                print(f"  Gemini {model} failed: {e}")
        time.sleep(15 * (rnd + 1))
    print(f"  Giving up on this item: {last}")
    return None


# ---------- drawing ----------
def bengali_font_path(bold=True):
    names = (["NotoSansBengali-Bold.ttf", "NotoSerifBengali-Bold.ttf"] if bold else []) + \
            ["NotoSansBengali-Regular.ttf", "Lohit-Bengali.ttf"]
    for n in names:
        for p in glob.glob(f"/usr/share/fonts/**/{n}", recursive=True):
            return p
    try:
        p = subprocess.check_output(["fc-match", "-f", "%{file}", ":lang=bn"], text=True).strip()
        if p:
            return p
    except Exception:
        pass
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",):
        if os.path.exists(p):
            print("WARNING: no Bengali font found, Bengali letters will not draw correctly")
            return p
    raise RuntimeError("No font found")


FONT_PATH = None
ENGINE = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
if ENGINE != ImageFont.Layout.RAQM:
    print("WARNING: text shaping engine missing, Bengali joined letters may look wrong")


def font(size):
    global FONT_PATH
    if FONT_PATH is None:
        FONT_PATH = bengali_font_path()
    return ImageFont.truetype(FONT_PATH, size, layout_engine=ENGINE)


def wrap(d, text, f, max_w):
    lines, cur = [], ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if d.textlength(t, font=f) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def draw_block(d, text, box, start, minimum, color, spacing=1.45, center=False):
    x0, y0, x1, y1 = box
    size = start
    while True:
        f = font(size)
        lines = wrap(d, text, f, x1 - x0)
        lh = int(size * spacing)
        if len(lines) * lh <= (y1 - y0) or size <= minimum:
            break
        size -= 4
    y = y0
    for ln in lines:
        x = x0 + ((x1 - x0) - d.textlength(ln, font=f)) // 2 if center else x0
        d.text((x, y), ln, font=f, fill=color)
        y += lh
        if y > y1 + lh:
            break
    return y


def gradient(w, h):
    img = Image.new("RGB", (w, h))
    px = ImageDraw.Draw(img)
    for yy in range(h):
        t = yy / h
        px.line([(0, yy), (w, yy)], fill=(int(12 + 10 * t), int(24 + 40 * t), int(54 + 40 * t)))
    return img


def make_bg(prompt, path, size=(1080, 1920)):
    """Try to make a symbolic AI picture. Returns the path or None (cards then stay plain)."""
    import base64
    import io
    from urllib.parse import quote
    prompt = (prompt or "").strip()
    if not prompt or os.environ.get("NO_IMAGES") == "yes":
        return None
    full = prompt + ", symbolic illustration, no text, no letters, no logos, no real people, no faces"
    img = None
    for model in ("gemini-2.5-flash-image", "gemini-2.0-flash-preview-image-generation"):
        try:
            url = ("https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{model}:generateContent?key={GEMINI_KEY}")
            body = {"contents": [{"parts": [{"text": full}]}],
                    "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]}}
            r = requests.post(url, json=body, timeout=90)
            r.raise_for_status()
            for part in r.json()["candidates"][0]["content"]["parts"]:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline:
                    img = Image.open(io.BytesIO(base64.b64decode(inline["data"]))).convert("RGB")
                    break
            if img:
                break
        except Exception as e:
            print(f"  image via {model} failed: {str(e)[:120]}")
    if img is None:
        try:
            r = requests.get("https://image.pollinations.ai/prompt/" + quote(full) +
                             "?width=1024&height=1024&nologo=true", timeout=90)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception as e:
            print(f"  image via pollinations failed: {str(e)[:120]}")
    if img is None or min(img.size) < 256:
        return None
    img.save(path, quality=92)
    return path


def base_card(w, h, bg=None):
    img = gradient(w, h)
    if bg:
        try:
            pic = Image.open(bg).convert("RGB")
            k = max(w / pic.width, h / pic.height)
            pic = pic.resize((int(pic.width * k) + 1, int(pic.height * k) + 1))
            x, y = (pic.width - w) // 2, (pic.height - h) // 2
            pic = pic.crop((x, y, x + w, y + h))
            img = Image.blend(pic, img, 0.70)   # dark enough for white text
        except Exception as e:
            print("  background skipped:", e)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 14], fill=(230, 60, 60))
    d.rectangle([60, 90, 300, 160], fill=(230, 60, 60))
    d.text((92, 98), "খবর", font=font(46), fill=(255, 255, 255))
    return img, d


def feed_card(item, data, path, bg=None):
    w, h = 1080, 1350
    img, d = base_card(w, h, bg)
    y = draw_block(d, data["headline"], (70, 220, w - 70, 760), 84, 52, (255, 255, 255))
    y = max(y + 30, 700)
    for p in data["points"][:3]:
        if y > 1180:
            break
        y = draw_block(d, "- " + p, (70, y, w - 70, 1200), 44, 32, (200, 215, 235), spacing=1.45) + 18
    d.rectangle([60, 1230, w - 60, 1234], fill=(90, 110, 150))
    d.text((70, 1262), "সূত্র: " + item["source"], font=font(40), fill=(255, 210, 90))
    img.save(path, quality=92)


def reel_slide(text, label, path, small=False, bg=None):
    w, h = 1080, 1920
    img, d = base_card(w, h, bg)
    size = 70 if small else 92
    top = 640 if small else 420
    draw_block(d, text, (80, top, w - 80, 1500), size, 44, (255, 255, 255), center=True)
    if label:
        d.text((90, 1750), label, font=font(42), fill=(255, 210, 90))
    img.save(path, quality=92)


# ---------- reel ----------
async def tts(text, path):
    await edge_tts.Communicate(text, VOICE, rate="-5%").save(path)


def duration(path):
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        text=True).strip())


def make_reel(slides, out_path, workdir):
    """slides = list of (image_path, spoken_text)."""
    segs = []
    for i, (img, spoken) in enumerate(slides):
        mp3 = f"{workdir}/a{i}.mp3"
        asyncio.run(tts(spoken, mp3))
        dur = duration(mp3) + 0.7
        frames = int(dur * 24) + 1
        seg = f"{workdir}/seg{i}.mp4"
        vf = (f"zoompan=z='min(zoom+0.0007,1.07)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              f":d={frames}:s=1080x1920:fps=24,format=yuv420p")
        subprocess.check_call(["ffmpeg", "-v", "error", "-y", "-i", img, "-i", mp3,
                               "-vf", vf, "-af", "apad", "-t", f"{dur:.2f}",
                               "-r", "24", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
                               "-ar", "44100", "-ac", "2", seg])
        segs.append(seg)
    lst = f"{workdir}/list.txt"
    open(lst, "w").write("".join(f"file '{os.path.abspath(s)}'\n" for s in segs))
    subprocess.check_call(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                           "-i", lst, "-r", "24", "-c:v", "libx264", "-preset", "veryfast",
                           "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", out_path])
    return duration(out_path)


# ---------- Facebook ----------
class FBError(Exception):
    pass


def fb_check(r):
    try:
        data = r.json()
    except Exception:
        data = {}
    if not r.ok or "error" in data:
        raise FBError(f"Facebook error {r.status_code}: {r.text[:400]}")
    return data


def post_photo(image_path, text):
    with open(image_path, "rb") as f:
        r = requests.post(f"{GRAPH}/{FB_PAGE}/photos",
                          data={"message": text, "access_token": FB_TOKEN},
                          files={"source": f}, timeout=300)
    return fb_check(r)


def post_reel(video_path, text):
    r = requests.post(f"{GRAPH}/{FB_PAGE}/video_reels",
                      data={"upload_phase": "start", "access_token": FB_TOKEN}, timeout=120)
    start = fb_check(r)
    vid, up = start["video_id"], start["upload_url"]
    size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        r = requests.post(up, data=f.read(), timeout=600, headers={
            "Authorization": f"OAuth {FB_TOKEN}", "offset": "0", "file_size": str(size)})
    fb_check(r)
    r = requests.post(f"{GRAPH}/{FB_PAGE}/video_reels",
                      data={"upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
                            "description": text, "access_token": FB_TOKEN}, timeout=120)
    return fb_check(r)


def publish_enabled():
    p = (os.environ.get("PUBLISH") or "").strip().lower()
    if p == "yes":
        return True
    if p == "no":          # a manual run left on "no" is always a safe test
        return False
    return os.path.exists("auto_post_on.txt")   # scheduled runs


# ---------- daily special posts: date, prayer times, gold ----------
BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")
BN_DAYS = ["সোমবার", "মঙ্গলবার", "বুধবার", "বৃহস্পতিবার", "শুক্রবার", "শনিবার", "রবিবার"]
BN_MONTHS = ["জানুয়ারি", "ফেব্রুয়ারি", "মার্চ", "এপ্রিল", "মে", "জুন", "জুলাই",
             "আগস্ট", "সেপ্টেম্বর", "অক্টোবর", "নভেম্বর", "ডিসেম্বর"]
HIJRI_MONTHS = ["মুহাররম", "সফর", "রবিউল আউয়াল", "রবিউস সানি", "জমাদিউল আউয়াল",
                "জমাদিউস সানি", "রজব", "শাবান", "রমজান", "শাওয়াল", "জিলকদ", "জিলহজ"]
DHAKA = ZoneInfo("Asia/Dhaka")
BHORI_GRAMS = 11.664
TROY_OUNCE = 31.1035


def bn(x):
    return str(x).translate(BN_DIGITS)


def money(x):
    s = str(int(round(x)))
    if len(s) > 3:
        s = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", s[:-3]) + "," + s[-3:]
    return bn(s)


def to_12h(hhmm):
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    if 3 <= h < 6:
        word = "ভোর"
    elif 6 <= h < 11:
        word = "সকাল"
    elif 11 <= h < 15:
        word = "দুপুর"
    elif 15 <= h < 17:
        word = "বিকাল"
    elif 17 <= h < 20:
        word = "সন্ধ্যা"
    else:
        word = "রাত"
    h12 = h % 12 or 12
    return f"{word} {bn(h12)}:{bn(f'{m:02d}')}"


def fetch_prayer(now):
    url = ("https://api.aladhan.com/v1/timingsByCity?city=Dhaka&country=Bangladesh"
           "&method=1&school=1")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()["data"]
    t = data["timings"]
    hij = data["date"]["hijri"]
    names = [("ফজর", "Fajr"), ("সূর্যোদয়", "Sunrise"), ("জোহর", "Dhuhr"),
             ("আসর", "Asr"), ("মাগরিব", "Maghrib"), ("এশা", "Isha")]
    rows = [(bn_name, to_12h(t[key][:5])) for bn_name, key in names]
    date_line = f"{BN_DAYS[now.weekday()]}, {bn(now.day)} {BN_MONTHS[now.month - 1]} {bn(now.year)}"
    hijri_line = (f"{bn(int(hij['day']))} {HIJRI_MONTHS[int(hij['month']['number']) - 1]} "
                  f"{bn(hij['year'])} হিজরি")
    return {"date_line": date_line, "hijri_line": hijri_line, "rows": rows}


def fetch_gold():
    g = requests.get("https://api.gold-api.com/price/XAU", timeout=30)
    g.raise_for_status()
    usd_oz = float(g.json()["price"])
    f = requests.get("https://open.er-api.com/v6/latest/USD", timeout=30)
    f.raise_for_status()
    bdt = float(f.json()["rates"]["BDT"])
    if not (500 < usd_oz < 20000 and 50 < bdt < 400):
        raise ValueError(f"Unreasonable gold/FX numbers: {usd_oz}, {bdt}")
    per_gram_24 = usd_oz / TROY_OUNCE * bdt
    rows = []
    for label, karat in (("২৪ ক্যারেট", 24), ("২২ ক্যারেট", 22), ("২১ ক্যারেট", 21), ("১৮ ক্যারেট", 18)):
        rows.append((label, "৳ " + money(per_gram_24 * karat / 24 * BHORI_GRAMS)))
    return {"rows": rows, "usd_oz": usd_oz, "bdt": bdt}


def table_card(title, subtitle, rows, footer, path):
    w, h = 1080, 1350
    img, d = base_card(w, h)
    y = draw_block(d, title, (70, 210, w - 70, 480), 78, 50, (255, 255, 255))
    if subtitle:
        y = draw_block(d, subtitle, (70, y + 5, w - 70, y + 140), 44, 32, (255, 210, 90)) + 10
    y = max(y + 20, 470)
    for label, value in rows:
        d.rectangle([60, y, w - 60, y + 84], fill=(22, 44, 82))
        d.text((90, y + 8), label, font=font(50), fill=(255, 255, 255))
        vw = d.textlength(value, font=font(50))
        d.text((w - 90 - vw, y + 8), value, font=font(50), fill=(255, 210, 90))
        y += 96
    draw_block(d, footer, (70, max(y + 10, 1120), w - 70, 1330), 32, 26, (170, 190, 220), spacing=1.4)
    img.save(path, quality=92)


def prayer_package(now, work_dir, out_dir):
    info = fetch_prayer(now)
    card = f"{out_dir}/prayer.jpg"
    foot = "ঢাকার সময় অনুযায়ী। অন্যান্য জেলায় কয়েক মিনিট আগে-পরে হতে পারে। হিজরি তারিখ চাঁদ দেখার ওপর নির্ভর করে।"
    table_card("আজকের নামাজের সময়সূচি", info["date_line"] + " | " + info["hijri_line"],
               info["rows"], foot, card)
    lines = [f"{lab}: {val}" for lab, val in info["rows"]]
    text = ("আজকের নামাজের সময়সূচি (ঢাকা)\n\n"
            f"{info['date_line']} | {info['hijri_line']}\n\n" + "\n".join(lines) +
            "\n\nঅন্যান্য জেলায় কয়েক মিনিট আগে-পরে হতে পারে।\n\n#নামাজ #আজান #সময়সূচি #বাংলাদেশ")
    slides = []
    p0 = f"{work_dir}/p0.jpg"
    reel_slide("আজকের নামাজের সময়সূচি\n" + info["date_line"], "ঢাকা", p0)
    slides.append((p0, "আজকের নামাজের সময়সূচি। " + info["date_line"] + "। " + info["hijri_line"]))
    for i, (lab, val) in enumerate(info["rows"], start=1):
        pi = f"{work_dir}/p{i}.jpg"
        reel_slide(f"{lab}\n{val}", "", pi)
        slides.append((pi, f"{lab}, {val}"))
    pe = f"{work_dir}/p_end.jpg"
    reel_slide("অন্যান্য জেলায় কয়েক মিনিট আগে-পরে হতে পারে। পেজটি ফলো করুন।", "", pe, small=True)
    slides.append((pe, "অন্যান্য জেলায় কয়েক মিনিট আগে পরে হতে পারে। পেজটি ফলো করুন।"))
    return {"title": "নামাজের সময়সূচি", "text": text, "card": card, "slides": slides}


def gold_package(now, work_dir, out_dir):
    info = fetch_gold()
    card = f"{out_dir}/gold.jpg"
    date_line = f"{BN_DAYS[now.weekday()]}, {bn(now.day)} {BN_MONTHS[now.month - 1]} {bn(now.year)}"
    foot = (f"আন্তর্জাতিক বাজারদর (প্রতি আউন্স {money(info['usd_oz'])} ডলার) ও ডলারের হার (১ ডলার = ৳{bn(round(info['bdt'], 2))}) "
            "থেকে হিসাব করা আনুমানিক দাম। বাজুসের ঘোষিত দাম ভিন্ন হতে পারে।")
    table_card("আজকের সোনার আনুমানিক দাম", date_line + " | প্রতি ভরি", info["rows"], foot, card)
    lines = [f"{lab}: {val}" for lab, val in info["rows"]]
    text = ("আজকের সোনার আনুমানিক দাম (প্রতি ভরি)\n\n" + date_line + "\n\n" + "\n".join(lines) +
            "\n\n" + foot + "\n\n#সোনার_দাম #স্বর্ণ #বাংলাদেশ")
    slides = []
    p0 = f"{work_dir}/g0.jpg"
    reel_slide("আজকের সোনার আনুমানিক দাম\nপ্রতি ভরি", date_line, p0)
    slides.append((p0, "আজকের সোনার আনুমানিক দাম, প্রতি ভরি। " + date_line))
    for i, (lab, val) in enumerate(info["rows"], start=1):
        pi = f"{work_dir}/g{i}.jpg"
        reel_slide(f"{lab}\n{val}", "", pi)
        slides.append((pi, f"{lab}, {val.replace('৳', '')} টাকা"))
    pe = f"{work_dir}/g_end.jpg"
    reel_slide("আন্তর্জাতিক বাজারদরের ভিত্তিতে আনুমানিক দাম। বাজুসের দাম ভিন্ন হতে পারে।", "", pe, small=True)
    slides.append((pe, "এটি আন্তর্জাতিক বাজারদরের ভিত্তিতে আনুমানিক দাম। বাজুসের ঘোষিত দাম ভিন্ন হতে পারে।"))
    return {"title": "সোনার দাম", "text": text, "card": card, "slides": slides}


def finish_item(rec, tag, live, seen, key):
    """Make the reel, optionally post, and record the result. Returns False if Facebook stopped us."""
    reel_path = None
    if rec.get("slides") and rec.pop("want_reel", True):
        try:
            work = f"{OUT}/work_{tag}"
            os.makedirs(work, exist_ok=True)
            reel_path = f"{OUT}/reel_{tag}.mp4"
            rec["reel_seconds"] = round(make_reel(rec["slides"], reel_path, work))
            rec["reel"] = reel_path
        except Exception as e:
            print("  Reel failed:", e)
            reel_path = None
    rec.pop("slides", None)
    if live:
        try:
            post_photo(rec["card"], rec["text"])
            print("  posted card")
            if reel_path:
                post_reel(reel_path, rec["text"])
                print("  posted reel")
            seen.add(key)
            save_state(seen)
            rec["posted"] = True
        except FBError as e:
            print("  Facebook problem:", e)
            rec["posted"] = False
            return False
        time.sleep(20)
    return True


def main():
    os.makedirs(OUT, exist_ok=True)
    live = publish_enabled()
    if live and not (FB_TOKEN and FB_PAGE):
        raise SystemExit("Posting is ON but FB_PAGE_TOKEN or FB_PAGE_ID secret is missing.")
    print("MODE:", "POSTING TO FACEBOOK" if live else "DRY RUN (nothing will be posted)")

    seen = load_state()
    now = datetime.now(DHAKA)
    today = now.strftime("%Y-%m-%d")
    results = []
    stopped = False

    # --- daily special posts (once per day, after 4 am Dhaka time) ---
    if now.hour >= 4 or os.environ.get("FORCE_DAILY") == "yes":
        for tag, builder in (("prayer", prayer_package), ("gold", gold_package)):
            key = f"{tag}-{today}"
            if key in seen:
                print(f"{tag}: already posted today")
                continue
            try:
                work = f"{OUT}/work_{tag}"
                os.makedirs(work, exist_ok=True)
                rec = builder(now, work, OUT)
            except Exception as e:
                print(f"{tag}: skipped, could not get data: {e}")
                continue
            print("-", rec["title"])
            ok = finish_item(rec, tag, live, seen, key)
            results.append(rec)
            if not ok:
                stopped = True
                break

    # --- news ---
    news_count, reels_made = 0, 0
    items = [] if stopped else fetch_items(seen)
    print(f"{len(items)} new news items found")
    for item in items:
        if news_count >= MAX_NEWS:
            break
        print("-", item["source"], "|", item["title"][:70])
        data = write_post(item)
        time.sleep(6)
        if not data:
            seen.add(key_of(item["link"]))
            continue
        blob = " ".join([item["title"], item["desc"], data["headline"], data["post_text"]]
                        + list(data["points"])).lower()
        is_al = any(w in blob for w in AL_WORDS)
        tags = " ".join(data.get("hashtags") or [])[:200]
        text = f"{data['headline']}\n\n{data['post_text']}"
        if is_al:
            text += f"\n\n{AL_CONTEXT}"
        text += f"\n\nসূত্র: {item['source']}\n{item['link']}\n\n{tags}"

        news_count += 1
        n = news_count
        card = f"{OUT}/news{n}.jpg"
        work = f"{OUT}/work_news{n}"
        os.makedirs(work, exist_ok=True)
        bg = None
        if not is_al:   # no generated pictures for party/political stories
            bg = make_bg(data.get("image_prompt"), f"{work}/bg.jpg")
            if bg:
                text += "\n\n(ছবিটি প্রতীকী, এআই-নির্মিত)"
        feed_card(item, data, card, bg)
        slides = []
        p0 = f"{work}/s0.jpg"
        reel_slide(data["headline"], "সূত্র: " + item["source"], p0, bg=bg)
        slides.append((p0, data["headline"]))
        for j, pt in enumerate(data["points"][:3], start=1):
            pj = f"{work}/s{j}.jpg"
            reel_slide(pt, "", pj, small=True, bg=bg)
            slides.append((pj, pt))
        if is_al:
            pj = f"{work}/s_ctx.jpg"
            reel_slide(AL_CONTEXT, "", pj, small=True)
            slides.append((pj, AL_CONTEXT))
        pe = f"{work}/s_end.jpg"
        reel_slide("সূত্র: " + item["source"] + "। আরও খবরের জন্য পেজটি ফলো করুন।", "", pe, small=True)
        slides.append((pe, "সূত্র " + item["source"] + "। আরও খবরের জন্য পেজটি ফলো করুন।"))

        rec = {"title": data["headline"], "source": item["source"], "link": item["link"],
               "text": text, "awami_league_related": is_al, "card": card, "slides": slides,
               "want_reel": reels_made < MAX_REELS}
        if rec["want_reel"]:
            reels_made += 1
        ok = finish_item(rec, f"news{n}", live, seen, key_of(item["link"]))
        results.append(rec)
        if not ok:
            break

    json.dump(results, open(f"{OUT}/posts.json", "w"), ensure_ascii=False, indent=2)
    print(f"Done. {len(results)} posts prepared ({news_count} news), reels made: "
          f"{sum(1 for r in results if r.get('reel'))}.")


if __name__ == "__main__":
    main()
