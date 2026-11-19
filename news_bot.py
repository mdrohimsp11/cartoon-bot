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

import requests
from PIL import Image, ImageDraw, ImageFont, features

try:
    import edge_tts
except ImportError:
    edge_tts = None

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODELS = []
for _m in (os.environ.get("GEMINI_MODEL"), "gemini-flash-latest",
           "gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-lite-latest"):
    if _m and _m not in MODELS:
        MODELS.append(_m)

FB_TOKEN = os.environ.get("FB_PAGE_TOKEN", "").strip()
FB_PAGE = os.environ.get("FB_PAGE_ID", "").strip()
GRAPH = "https://graph.facebook.com"

OUT = "news_out"
STATE = "posted.json"
MAX_NEWS = int(os.environ.get("MAX_NEWS") or 5)
MAX_REELS = int(os.environ.get("MAX_REELS") or 2)
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
- তথ্য খুব কম হলে, অথবা এটি খবর না হয়ে বিজ্ঞাপন বা আড্ডার লেখা হলে skip=true দাও।

শুধু JSON দাও:
{{
  "skip": false,
  "headline": "সর্বোচ্চ ৭০ অক্ষরের শিরোনাম",
  "points": ["প্রথম তথ্য, সর্বোচ্চ ২০ শব্দ", "দ্বিতীয় তথ্য", "তৃতীয় তথ্য (থাকলে)"],
  "post_text": "ফেসবুক পোস্টের মূল লেখা, ২-৪ বাক্য",
  "hashtags": ["#খবর", "#বাংলাদেশ"]
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


def base_card(w, h):
    img = gradient(w, h)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 14], fill=(230, 60, 60))
    d.rectangle([60, 90, 300, 160], fill=(230, 60, 60))
    d.text((92, 98), "খবর", font=font(46), fill=(255, 255, 255))
    return img, d


def feed_card(item, data, path):
    w, h = 1080, 1350
    img, d = base_card(w, h)
    y = draw_block(d, data["headline"], (70, 220, w - 70, 760), 84, 52, (255, 255, 255))
    y = max(y + 30, 700)
    for p in data["points"][:3]:
        if y > 1180:
            break
        y = draw_block(d, "• " + p, (70, y, w - 70, 1200), 44, 32, (200, 215, 235), spacing=1.45) + 18
    d.rectangle([60, 1230, w - 60, 1234], fill=(90, 110, 150))
    d.text((70, 1262), "সূত্র: " + item["source"], font=font(40), fill=(255, 210, 90))
    img.save(path, quality=92)


def reel_slide(text, label, path, small=False):
    w, h = 1080, 1920
    img, d = base_card(w, h)
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
    if (os.environ.get("PUBLISH") or "").strip().lower() == "yes":
        return True
    return os.path.exists("auto_post_on.txt")


def main():
    os.makedirs(OUT, exist_ok=True)
    live = publish_enabled()
    if live and not (FB_TOKEN and FB_PAGE):
        raise SystemExit("Posting is ON but FB_PAGE_TOKEN or FB_PAGE_ID secret is missing.")
    print("MODE:", "POSTING TO FACEBOOK" if live else "DRY RUN (nothing will be posted)")

    seen = load_state()
    items = fetch_items(seen)
    print(f"{len(items)} new items found")
    results, reels_made = [], 0
    for item in items:
        if len(results) >= MAX_NEWS:
            break
        print("-", item["source"], "|", item["title"][:70])
        data = write_post(item)
        time.sleep(6)
        if not data:
            seen.add(key_of(item["link"]))
            continue
        is_al = any(w in (item["title"] + " " + item["desc"]).lower() for w in AL_WORDS)
        tags = " ".join(data.get("hashtags") or [])[:200]
        text = f"{data['headline']}\n\n{data['post_text']}"
        if is_al:
            text += f"\n\n{AL_CONTEXT}"
        text += f"\n\nসূত্র: {item['source']}\n{item['link']}\n\n{tags}"

        n = len(results) + 1
        card = f"{OUT}/news{n}.jpg"
        feed_card(item, data, card)
        record = {"title": data["headline"], "source": item["source"], "link": item["link"],
                  "text": text, "awami_league_related": is_al, "card": card}

        reel_path = None
        if reels_made < MAX_REELS:
            try:
                work = f"{OUT}/work{n}"
                os.makedirs(work, exist_ok=True)
                slides = []
                p0 = f"{work}/s0.jpg"
                reel_slide(data["headline"], "সূত্র: " + item["source"], p0)
                slides.append((p0, data["headline"]))
                for j, pt in enumerate(data["points"][:3], start=1):
                    pj = f"{work}/s{j}.jpg"
                    reel_slide(pt, "", pj, small=True)
                    slides.append((pj, pt))
                if is_al:
                    pj = f"{work}/s_ctx.jpg"
                    reel_slide(AL_CONTEXT, "", pj, small=True)
                    slides.append((pj, AL_CONTEXT))
                pe = f"{work}/s_end.jpg"
                reel_slide("সূত্র: " + item["source"] + "। আরও খবরের জন্য পেজটি ফলো করুন।", "", pe, small=True)
                slides.append((pe, "সূত্র " + item["source"] + "। আরও খবরের জন্য পেজটি ফলো করুন।"))
                reel_path = f"{OUT}/reel{n}.mp4"
                record["reel_seconds"] = round(make_reel(slides, reel_path, work))
                record["reel"] = reel_path
                reels_made += 1
            except Exception as e:
                print("  Reel failed:", e)
                reel_path = None

        if live:
            try:
                post_photo(card, text)
                print("  posted news card")
                if reel_path:
                    post_reel(reel_path, text)
                    print("  posted reel")
                seen.add(key_of(item["link"]))
                save_state(seen)
                record["posted"] = True
            except FBError as e:
                print("  Facebook problem:", e)
                record["posted"] = False
                results.append(record)
                break
            time.sleep(20)
        results.append(record)

    json.dump(results, open(f"{OUT}/posts.json", "w"), ensure_ascii=False, indent=2)
    print(f"Done. {len(results)} news items prepared, {reels_made} reels.")


if __name__ == "__main__":
    main()
