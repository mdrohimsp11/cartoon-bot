"""Daily Quran recitation post for the Bangla news page.

Arabic text, Bengali translation and the recitation audio all come from the
alquran.cloud service. Nothing about the Quran is written by an AI model.
Reuses the drawing, reel and Facebook helpers from news_bot.py.
"""
import os
import glob
import time
import asyncio
import subprocess
from datetime import datetime

import requests
from PIL import Image, ImageDraw, ImageFont

import news_bot as nb

API = "https://api.alquran.cloud/v1/surah/{n}/editions/quran-uthmani,bn.bengali,ar.alafasy"
RECITER = "মিশারী রাশিদ আলাফাসি"

# (surah number, Bengali name, first ayah, last ayah, special title or None)
PLAN = [
    (1, "ফাতিহা", 1, 7, None),
    (112, "ইখলাস", 1, 4, None),
    (113, "ফালাক", 1, 5, None),
    (114, "নাস", 1, 6, None),
    (103, "আসর", 1, 3, None),
    (108, "কাউসার", 1, 3, None),
    (110, "নাসর", 1, 3, None),
    (109, "কাফিরুন", 1, 6, None),
    (111, "লাহাব", 1, 5, None),
    (105, "ফীল", 1, 5, None),
    (106, "কুরাইশ", 1, 4, None),
    (107, "মাউন", 1, 7, None),
    (104, "হুমাযাহ", 1, 9, None),
    (102, "তাকাসুর", 1, 8, None),
    (94, "ইনশিরাহ", 1, 8, None),
    (95, "তীন", 1, 8, None),
    (97, "কদর", 1, 5, None),
    (99, "যিলযাল", 1, 8, None),
    (2, "বাকারা", 255, 255, "আয়াতুল কুরসি"),
    (2, "বাকারা", 285, 286, "সূরা বাকারার শেষ দুই আয়াত"),
]


# ---------- Arabic font ----------
_AR_PATH = None


def arabic_font(size):
    global _AR_PATH
    if _AR_PATH is None:
        found = None
        for pat in ("NotoNaskhArabic-Regular.ttf", "NotoNaskhArabic*.ttf", "NotoSansArabic-Regular.ttf",
                    "NotoSansArabic*.ttf", "Amiri*.ttf", "FreeSerif.ttf"):
            hits = sorted(glob.glob(f"/usr/share/fonts/**/{pat}", recursive=True))
            if hits:
                found = hits[0]
                break
        if not found:
            try:
                found = subprocess.check_output(["fc-match", "-f", "%{file}", ":lang=ar"], text=True).strip()
            except Exception:
                found = None
        if not found:
            raise RuntimeError("No Arabic font found")
        print("Arabic font:", found)
        _AR_PATH = found
    return ImageFont.truetype(_AR_PATH, size, layout_engine=nb.ENGINE)


def draw_arabic(d, text, box, start, minimum, color=(255, 255, 255), spacing=1.75, vcenter=False):
    """Centered right-to-left block that shrinks until it fits the box."""
    if nb.ENGINE != ImageFont.Layout.RAQM:
        raise RuntimeError("Arabic needs the RAQM text engine")
    x0, y0, x1, y1 = box
    size = start
    while True:
        f = arabic_font(size)
        lines, cur = [], ""
        for word in text.split():
            t = (cur + " " + word).strip()
            if d.textlength(t, font=f, direction="rtl", language="ar") <= x1 - x0 or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        lh = int(size * spacing)
        if len(lines) * lh <= (y1 - y0) or size <= minimum:
            break
        size -= 4
    y = y0 + (max(0, (y1 - y0) - len(lines) * lh) // 2 if vcenter else 0)
    for ln in lines:
        wdt = d.textlength(ln, font=f, direction="rtl", language="ar")
        d.text((x0 + ((x1 - x0) - wdt) // 2, y), ln, font=f, fill=color, direction="rtl", language="ar")
        y += lh
        if y > y1 + lh:
            break
    return y


# ---------- data ----------
def fetch_passage(item):
    n, name, a, b, special = item
    r = requests.get(API.format(n=n), timeout=60)
    r.raise_for_status()
    data = r.json()["data"]
    eds = {e["edition"]["identifier"]: e["ayahs"] for e in data}
    ayahs = []
    for num in range(a, b + 1):
        ar = next(x for x in eds["quran-uthmani"] if x["numberInSurah"] == num)
        bnx = next(x for x in eds["bn.bengali"] if x["numberInSurah"] == num)
        au = next(x for x in eds["ar.alafasy"] if x["numberInSurah"] == num)
        text = ar["text"].strip()
        # this service glues the opening phrase onto ayah 1 of most surahs; remove it
        if num == 1 and n not in (1, 9):
            words = text.split()
            if len(words) > 4:
                text = " ".join(words[4:])
        ayahs.append({"num": num, "ar": text, "bn": bnx["text"].strip(), "audio": au["audio"]})
    return ayahs


def download(url, path):
    r = requests.get(url, timeout=90)
    r.raise_for_status()
    if len(r.content) < 2000:
        raise RuntimeError("audio file too small: " + url)
    with open(path, "wb") as fh:
        fh.write(r.content)


# ---------- images ----------
def base(w, h, label, color=(30, 150, 120)):
    img = nb.gradient(w, h)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 14], fill=color)
    d.rectangle([60, 90, 400, 160], fill=color)
    d.text((92, 98), label, font=nb.font(46), fill=(255, 255, 255))
    return img, d


def title_of(item):
    n, name, a, b, special = item
    return special or f"সূরা {name}"


def ref_of(item):
    n, name, a, b, special = item
    sura = f"সূরা {name}"
    rng = nb.bn(a) if a == b else f"{nb.bn(a)}-{nb.bn(b)}"
    return f"{sura}, আয়াত {rng}"


def passage_card(item, ayahs, path):
    w, h = 1080, 1350
    img, d = base(w, h, "তিলাওয়াত")
    y = nb.draw_block(d, title_of(item), (70, 190, w - 70, 330), 74, 48, (255, 255, 255))
    n, name, a, b, special = item
    rng = nb.bn(a) if a == b else f"{nb.bn(a)}-{nb.bn(b)}"
    sub = ref_of(item) if special else f"আয়াত {rng}"
    d.text((70, y + 4), sub, font=nb.font(38), fill=(255, 210, 90))
    draw_arabic(d, " ".join(x["ar"] for x in ayahs), (70, 370, w - 70, 800), 84, 40)
    nb.draw_block(d, " ".join(x["bn"] for x in ayahs), (70, 840, w - 70, 1200), 42, 28,
                  (200, 215, 235), spacing=1.45)
    d.rectangle([60, 1230, w - 60, 1234], fill=(90, 110, 150))
    d.text((70, 1262), "তিলাওয়াত: " + RECITER, font=nb.font(34), fill=(255, 210, 90))
    img.save(path, quality=92)


def ayah_slide(item, ay, path):
    w, h = 1080, 1920
    img, d = base(w, h, "তিলাওয়াত")
    draw_arabic(d, ay["ar"], (80, 250, w - 80, 1010), 120, 44, vcenter=True)
    nb.draw_block(d, ay["bn"], (80, 1070, w - 80, 1660), 52, 32, (200, 215, 235), center=True, spacing=1.45)
    d.text((90, 1750), f"{title_of(item)}, আয়াত {nb.bn(ay['num'])}", font=nb.font(42), fill=(255, 210, 90))
    img.save(path, quality=92)


# ---------- reel with real audio ----------
def make_reel_audio(slides, out_path, workdir):
    """slides = list of (image, spoken_text) or (image, spoken_text, audio_file).
    With an audio file the recitation plays; otherwise Bengali speech is generated."""
    segs = []
    for i, sl in enumerate(slides):
        img, spoken = sl[0], sl[1]
        audio = sl[2] if len(sl) > 2 else None
        pad = 1.4 if audio else 0.7
        if not audio:
            audio = f"{workdir}/a{i}.mp3"
            asyncio.run(nb.tts(spoken, audio))
        dur = nb.duration(audio) + pad
        frames = int(dur * 24) + 1
        seg = f"{workdir}/seg{i}.mp4"
        vf = (f"zoompan=z='min(zoom+0.0007,1.07)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              f":d={frames}:s=1080x1920:fps=24,format=yuv420p")
        subprocess.check_call(["ffmpeg", "-v", "error", "-y", "-i", img, "-i", audio,
                               "-vf", vf, "-af", "apad", "-t", f"{dur:.2f}",
                               "-r", "24", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
                               "-ar", "44100", "-ac", "2", seg])
        segs.append(seg)
    lst = f"{workdir}/list.txt"
    open(lst, "w").write("".join(f"file '{os.path.abspath(s)}'\n" for s in segs))
    subprocess.check_call(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                           "-i", lst, "-r", "24", "-c:v", "libx264", "-preset", "veryfast",
                           "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", out_path])
    return nb.duration(out_path)


# ---------- package ----------
def quran_package(now, work_dir, out_dir):
    item = PLAN[now.timetuple().tm_yday % len(PLAN)]
    ayahs = fetch_passage(item)
    for ay in ayahs:
        p = f"{work_dir}/ay{ay['num']}.mp3"
        download(ay["audio"], p)
        ay["file"] = p
    card = f"{out_dir}/quran.jpg"
    passage_card(item, ayahs, card)

    lines_ar = "\n".join(x["ar"] for x in ayahs)
    lines_bn = "\n".join(x["bn"] for x in ayahs)
    text = (f"আজকের তিলাওয়াত: {ref_of(item)}\n\n{lines_ar}\n\nঅনুবাদ:\n{lines_bn}\n\n"
            f"তিলাওয়াত: {RECITER}\n\n#কুরআন #তিলাওয়াত #আজকেরসুরা #ইসলাম")

    slides = []
    p0 = f"{work_dir}/q0.jpg"
    nb.reel_slide("আজকের তিলাওয়াত\n" + title_of(item), "", p0)
    slides.append((p0, "আজকের তিলাওয়াত। " + title_of(item)))
    for ay in ayahs:
        pj = f"{work_dir}/q{ay['num']}.jpg"
        ayah_slide(item, ay, pj)
        slides.append((pj, "", ay["file"]))
    pe = f"{work_dir}/q_end.jpg"
    nb.reel_slide("প্রতিদিন তিলাওয়াত পেতে পেজটি ফলো করুন।", "", pe, small=True)
    slides.append((pe, "প্রতিদিন তিলাওয়াত পেতে পেজটি ফলো করুন।"))
    return {"title": title_of(item), "text": text, "card": card, "slides": slides}


def main():
    os.makedirs(nb.OUT, exist_ok=True)
    live = nb.publish_enabled()
    if live and not (nb.FB_TOKEN and nb.FB_PAGE):
        raise SystemExit("Posting is ON but FB_PAGE_TOKEN or FB_PAGE_ID secret is missing.")
    print("MODE:", "POSTING TO FACEBOOK" if live else "DRY RUN (nothing will be posted)")
    nb.make_reel = make_reel_audio  # finish_item uses this name

    seen = nb.load_state()
    now = datetime.now(nb.DHAKA)
    key = f"quran-{now.strftime('%Y-%m-%d')}"
    if key in seen and os.environ.get("FORCE") != "yes":
        print("quran: already posted today")
        return
    work = f"{nb.OUT}/work_quran"
    os.makedirs(work, exist_ok=True)
    try:
        rec = quran_package(now, work, nb.OUT)
    except Exception as e:
        print("quran: skipped:", e)
        return
    print("-", rec["title"])
    nb.finish_item(rec, "quran", live, seen, key)
    import json
    json.dump([rec], open(f"{nb.OUT}/quran_posts.json", "w"), ensure_ascii=False, indent=2)
    print("Done.")


if __name__ == "__main__":
    main()
