import asyncio
import json
import os
import random
import time
import urllib.parse

import edge_tts
import numpy as np
import requests
from moviepy.editor import AudioFileClip, CompositeAudioClip, VideoClip, afx, concatenate_videoclips
from PIL import Image, ImageDraw, ImageFilter, ImageFont

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODELS = []
for _m in (os.environ.get("GEMINI_MODEL"), "gemini-flash-latest",
           "gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-lite-latest"):
    if _m and _m not in MODELS:
        MODELS.append(_m)

OUT = "output_food"
W, H = 1080, 1920
FPS = 24
SHOT_SECONDS = 5.0
VOICE = "en-US-GuyNeural"

FOODS = [
    "Kolkata chicken biryani", "jalebi", "samosa", "gulab jamun", "masala dosa",
    "pani puri", "butter chicken", "pav bhaji", "idli with sambar", "rasgulla",
    "aloo paratha", "kachori", "vada pav", "chole bhature", "rice kheer",
    "gajar ka halwa", "garlic naan", "tandoori chicken", "masala chai",
    "mishti doi", "rasmalai", "khichuri", "hilsa fish curry", "luchi with aloo dum",
    "chicken tikka", "dal makhani", "paneer butter masala", "mutton rogan josh",
    "sandesh", "steamed momos", "patishapta pitha", "puri with aloo sabzi",
]

STYLE = ("ultra-photorealistic cinematic macro photograph of a miniature world, "
         "a gigantic food centerpiece, dozens of extremely tiny realistic adult Indian "
         "workers who look like real humans (not toys, not dolls, not cartoons), "
         "premium macro cinema lens, extremely shallow depth of field, soft natural "
         "lighting, steam and warm highlights, realistic textures, vertical 9:16, "
         "no text, no watermark")

PROMPT = """You plan a satisfying 3-minute "miniature world" cooking video.
Concept: an entire tiny Indian community is making ONE gigantic portion of {food}.
The food and its vessels are huge; the workers are tiny realistic adult humans who
climb ladders, stir, carry, pour and serve.

First work out how {food} is REALLY made traditionally (ingredients, vessels, utensils,
techniques, order of steps). Do not invent a different cooking method.

Return ONLY JSON with this exact shape:
{{
  "food": "name of the dish",
  "title": "catchy YouTube title, max 90 chars, 1-2 emojis, mentions giant and tiny people",
  "description": "3-4 sentence description, then a call to like and subscribe",
  "hashtags": ["#miniature", "#indianfood", "#shorts"],
  "reel_caption": "short caption for a Facebook/Instagram reel, max 150 chars",
  "environment": "one sentence describing the miniature setting that fits this dish (village, street stall, kitchen, festival...)",
  "thumbnail_text": "max 3 words, curiosity-grabbing",
  "stages": [
    {{
      "step_title": "max 4 words, e.g. Mix the batter",
      "narration": "one calm informative sentence, max 18 words, about this step",
      "shots": [
        "scene 1: what is visible, which tiny workers do what, which giant vessel or ingredient, camera angle",
        "scene 2: a different angle or activity of the same step",
        "scene 3: another different angle or activity of the same step"
      ]
    }}
  ]
}}
Rules:
- Exactly 12 stages in the real traditional order. The last stage is serving the finished giant {food}.
- Exactly 3 shots per stage. Every shot looks different (wide, close-up, low angle, high angle).
- Each shot describes the same gigantic food and the same miniature setting, so the video feels like one world.
- Workers are adults in culturally appropriate Indian work clothing, with different poses and tasks.
- Do not describe text, signs or logos.
- Wholesome and safe for all ages.
"""


def pick_food():
    forced = (os.environ.get("FOOD_NAME") or "").strip()
    if forced:
        return forced
    day = int(time.time() // 86400)
    return FOODS[day % len(FOODS)]


def make_story(food):
    body = {
        "contents": [{"parts": [{"text": PROMPT.format(food=food)}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    last_err = None
    for rnd in range(4):
        for model in MODELS:
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{model}:generateContent?key={GEMINI_KEY}")
            try:
                r = requests.post(url, json=body, timeout=180)
                r.raise_for_status()
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                story = json.loads(text)
                assert len(story["stages"]) >= 6
                print("Plan made with model:", model)
                return story
            except Exception as e:
                last_err = e
                print(f"Gemini model {model} failed: {e}")
        time.sleep(20 * (rnd + 1))
    raise RuntimeError(f"Plan generation failed: {last_err}")


def plan_shots(story):
    shots = []
    n = len(story["stages"])
    for si, stage in enumerate(story["stages"]):
        scenes = [s for s in stage.get("shots", []) if str(s).strip()] or ["wide view of the giant dish"]
        for k, scene in enumerate(scenes):
            shots.append({
                "stage": si,
                "first": k == 0,
                "scene": str(scene),
                "label": f"STEP {si + 1}/{n}: {stage.get('step_title', '')}",
                "narration": (stage.get("narration") or "").strip() if k == 0 else "",
            })
    return shots


def pick_reel_indexes(shots, count=9):
    firsts = [i for i, s in enumerate(shots) if s["first"]]
    if len(firsts) <= count:
        return firsts
    picked = []
    for j in range(count):
        picked.append(firsts[round(j * (len(firsts) - 1) / (count - 1))])
    return sorted(set(picked))


def build_prompt(shot, story):
    return (f"{shot['scene']}. The gigantic dish: {story.get('food', '')}. "
            f"Setting: {story.get('environment', '')}. {STYLE}")


def make_image(prompt, path):
    for attempt in range(1, 6):
        seed = random.randint(1, 999999)
        url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
               + f"?width={W}&height={H}&nologo=true&model=flux&seed={seed}")
        try:
            r = requests.get(url, timeout=240)
            if r.ok and r.headers.get("content-type", "").startswith("image"):
                open(path, "wb").write(r.content)
                return True
            print(f"  image try {attempt}: HTTP {r.status_code}")
        except requests.RequestException as e:
            print(f"  image try {attempt}: {e}")
        time.sleep(5 * attempt)
    return False


def load_image(path):
    img = Image.open(path).convert("RGB")
    w, h = img.size
    img = img.crop((0, 0, w, int(h * 0.94)))
    return img.resize((W, H), Image.LANCZOS)


async def make_voice(text, path):
    await edge_tts.Communicate(text, VOICE, rate="-5%").save(path)


def find_font(size):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
              "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def caption_layer(text, color, size=(W, H), font_size=120, y_frac=0.70,
                  line_h=150, stroke=12):
    w, h = size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if not text.strip():
        return layer
    d = ImageDraw.Draw(layer)
    font = find_font(font_size)
    words, lines, cur = text.upper().split(), [], ""
    for word in words:
        test = (cur + " " + word).strip()
        if d.textlength(test, font=font) <= w - 120 or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    y = int(h * y_frac) - (len(lines) - 1) * line_h // 2
    for ln in lines:
        x = (w - d.textlength(ln, font=font)) / 2
        d.text((x, y), ln, font=font, fill=color,
               stroke_width=stroke, stroke_fill=(20, 10, 0))
        y += line_h
    return layer


def make_thumbnails(img_path, text):
    base = load_image(img_path)

    tall = base.convert("RGBA")
    tall.alpha_composite(caption_layer(text, (255, 225, 60), y_frac=0.80,
                                       font_size=135, line_h=160, stroke=13))
    tall.convert("RGB").save(f"{OUT}/thumbnail.jpg", quality=92)

    bg = base.resize((1280, int(1280 * H / W)), Image.LANCZOS)
    top = (bg.height - 720) // 2
    bg = bg.crop((0, top, 1280, top + 720)).filter(ImageFilter.GaussianBlur(22))
    bg = Image.blend(bg, Image.new("RGB", (1280, 720), (0, 0, 0)), 0.25)
    fg = base.resize((405, 720), Image.LANCZOS)
    bg.paste(fg, ((1280 - 405) // 2, 0))
    wide = bg.convert("RGBA")
    wide.alpha_composite(caption_layer(text, (255, 225, 60), size=(1280, 720),
                                       font_size=100, y_frac=0.80,
                                       line_h=115, stroke=10))
    wide.convert("RGB").save(f"{OUT}/thumbnail_wide.jpg", quality=92)


def make_vignette():
    ys = np.linspace(-1, 1, H, dtype=np.float32)[:, None]
    xs = np.linspace(-1, 1, W, dtype=np.float32)[None, :]
    d = np.sqrt(xs ** 2 * 0.8 + ys ** 2 * 0.5)
    return (1.0 - 0.28 * np.clip(d - 0.35, 0, 1) ** 1.5)[:, :, None].astype(np.float32)


VIGNETTE = make_vignette()


def camera_clip(img_path, dur, label_img):
    img = load_image(img_path)
    z0, z1 = random.choice([(1.0, 1.14), (1.14, 1.0), (1.03, 1.16), (1.0, 1.1)])
    x0, y0 = random.uniform(0.35, 0.65), random.uniform(0.35, 0.65)
    x1, y1 = random.uniform(0.35, 0.65), random.uniform(0.35, 0.65)
    phase = random.uniform(0, 6.28)

    def frame(t):
        p = min(max(t / dur, 0), 1)
        p = p * p * (3 - 2 * p)
        z = z0 + (z1 - z0) * p
        cw, ch = W / z, H / z
        cx = x0 + (x1 - x0) * p
        cy = y0 + (y1 - y0) * p
        left, top = (W - cw) * cx, (H - ch) * cy
        view = img.resize((W, H), Image.BILINEAR,
                          box=(left, top, left + cw, top + ch))
        view.paste(label_img, (0, 0), label_img)
        fade = min(1.0, t / 0.3, max(dur - t, 0) / 0.3)
        flicker = 1.0 + 0.02 * np.sin(2 * np.pi * 1.6 * t + phase)
        arr = np.asarray(view, dtype=np.float32) * (VIGNETTE * (fade * flicker))
        return np.clip(arr, 0, 255).astype(np.uint8)

    return VideoClip(frame, duration=dur)


def main():
    os.makedirs(OUT, exist_ok=True)
    food = pick_food()
    print("Food:", food)
    story = make_story(food)
    print("Title:", story["title"])
    shots = plan_shots(story)
    print(f"Planned {len(shots)} shots in {len(story['stages'])} steps")

    clips, shot_imgs, prev_img, failed = [], [], None, 0
    for i, shot in enumerate(shots):
        img = f"{OUT}/shot{i}.jpg"
        if not make_image(build_prompt(shot, story), img):
            failed += 1
            print(f"  WARNING: shot {i + 1} image failed, reusing the previous one")
            if prev_img is None:
                raise RuntimeError("Image generation failed for the first shot")
            img = prev_img
        prev_img = img
        shot_imgs.append(img)

        audio, dur = None, SHOT_SECONDS
        if shot["narration"]:
            aud = f"{OUT}/shot{i}.mp3"
            asyncio.run(make_voice(shot["narration"], aud))
            audio = AudioFileClip(aud)
            dur = max(SHOT_SECONDS, audio.duration + 0.4)

        label = caption_layer(shot["label"], (255, 255, 255), font_size=62,
                              y_frac=0.07, line_h=76, stroke=7)
        clip = camera_clip(img, dur, label)
        if audio is not None:
            clip = clip.set_audio(audio)
        clips.append(clip)
        print(f"Shot {i + 1}/{len(shots)} done")
        time.sleep(2)

    print(f"Images that failed and were reused: {failed}")

    def finish(clip_list, path):
        video = concatenate_videoclips(clip_list, method="compose")
        if os.path.exists("music.mp3"):
            music = AudioFileClip("music.mp3").volumex(0.10)
            music = afx.audio_loop(music, duration=video.duration)
            tracks = [video.audio, music] if video.audio else [music]
            video = video.set_audio(CompositeAudioClip(tracks))
        video.write_videofile(path, fps=FPS, codec="libx264",
                              audio_codec="aac", preset="veryfast")
        return video.duration

    long_len = finish(clips, f"{OUT}/video_full.mp4")
    print(f"Full video length: {long_len:.0f} s")

    reel_idx = pick_reel_indexes(shots)
    reel_len = finish([clips[i] for i in reel_idx], f"{OUT}/video_reel.mp4")
    print(f"Reel length: {reel_len:.0f} s")

    hero = max(0, len(shots) - 3)
    make_thumbnails(shot_imgs[hero], story.get("thumbnail_text") or "GIANT FOOD")

    meta = {k: story.get(k) for k in ("food", "title", "description", "hashtags", "reel_caption")}
    meta["full_video_seconds"] = round(long_len)
    meta["reel_seconds"] = round(reel_len)
    json.dump(meta, open(f"{OUT}/meta.json", "w"), ensure_ascii=False, indent=2)
    print("Done")


if __name__ == "__main__":
    main()
