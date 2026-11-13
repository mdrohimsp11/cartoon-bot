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
from PIL import Image, ImageDraw, ImageFont

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODELS = []
for _m in (os.environ.get("GEMINI_MODEL"), "gemini-flash-latest",
           "gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-lite-latest"):
    if _m and _m not in MODELS:
        MODELS.append(_m)
OUT = "output"
W, H = 1080, 1920
FPS = 24

VOICES = {
    "girl": "en-US-AnaNeural",
    "dad": "en-US-GuyNeural",
    "narrator": "en-US-JennyNeural",
}

STYLE = ("3D animated movie still, Pixar style, cute chubby proportions, "
         "big expressive eyes, soft cinematic lighting, cozy warm modern home, "
         "highly detailed, vertical 9:16 composition, no text, no watermark")

IDEAS = [
    "the little girl gives her sleepy dad a funny makeover with toy makeup",
    "the little girl cooks a pretend dinner and dad has to eat it",
    "dad tries to take a nap but the little girl keeps sneaking in toys",
    "the little girl plays doctor and treats dad's pretend injuries",
    "dad and the little girl build a blanket fort that collapses",
    "the little girl opens a pretend ice cream shop in the living room",
    "dad tries to hide a cookie from the hungry little girl",
    "the little girl teaches dad to dance and he trips",
    "the little girl paints dad's face while he is playing a game",
    "dad and the little girl have a bubble bath adventure",
]

PROMPT = """You write wholesome, funny 25-30 second family cartoon shorts shown as a
3D animated mini movie. Main characters: a chubby friendly dad and his cute toddler
daughter, at home. Story idea to start from: {idea}

Return ONLY JSON with this exact shape:
{{
  "title": "catchy YouTube Shorts title, max 80 chars, 1-2 emojis",
  "description": "2-3 sentence description, then a call to like and subscribe",
  "hashtags": ["#cartoon", "#family", "#shorts"],
  "characters": {{
    "dad": "fixed look: age, skin tone, hairstyle, beard, clothes",
    "girl": "fixed look: toddler age, skin tone, hairstyle, clothes"
  }},
  "thumbnail_shot": 0,
  "thumbnail_text": "max 3 words, curiosity-grabbing, for the thumbnail",
  "shots": [
    {{
      "who": ["dad", "girl"],
      "image_prompt": "camera shot type (wide / medium / close-up on face) + the one funny action + facial expressions + where in the home",
      "speaker": "girl or dad or narrator or none",
      "line": "spoken line, max 8 words, or empty string",
      "caption": "big on-screen text, max 4 words, or empty string"
    }}
  ]
}}
Rules:
- 10 to 12 shots. Each shot is one clear visual action, easy to understand without sound.
- Mix wide shots and close-ups of faces. Start with a wide shot of the home.
- Build up to a funny punchline, then a cute ending. The last shot has the caption.
- thumbnail_shot is the 0-based number of the funniest close-up shot (a face with a big expression).
- Safe and wholesome for kids. No scary or sad content.
- Do not reuse these titles: {used}"""


def make_story():
    used = []
    if os.path.exists("used_titles.json"):
        used = json.load(open("used_titles.json"))
    body = {
        "contents": [{"parts": [{"text": PROMPT.format(
            idea=random.choice(IDEAS), used=used[-20:])}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    last_err = None
    for rnd in range(4):
        for model in MODELS:
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{model}:generateContent?key={GEMINI_KEY}")
            try:
                r = requests.post(url, json=body, timeout=120)
                r.raise_for_status()
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                story = json.loads(text)
                assert story["shots"] and story["characters"]
                used.append(story["title"])
                json.dump(used, open("used_titles.json", "w"))
                print("Story made with model:", model)
                return story
            except Exception as e:
                last_err = e
                print(f"Gemini model {model} failed: {e}")
        time.sleep(20 * (rnd + 1))
    raise RuntimeError(f"Story generation failed: {last_err}")


def build_prompt(shot, chars):
    looks = "; ".join(f"{k}: {chars[k]}" for k in shot.get("who", []) if k in chars)
    return f"{STYLE}. Characters: {looks}. Scene: {shot['image_prompt']}"


def make_image(prompt, seed, path):
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
           + f"?width={W}&height={H}&nologo=true&model=flux&seed={seed}")
    for attempt in range(1, 5):
        try:
            r = requests.get(url, timeout=240)
            if r.ok and r.headers.get("content-type", "").startswith("image"):
                open(path, "wb").write(r.content)
                return True
        except requests.RequestException:
            pass
        time.sleep(5 * attempt)
    return False


async def make_voice(text, speaker, path):
    voice = VOICES.get(speaker, VOICES["narrator"])
    await edge_tts.Communicate(text, voice).save(path)


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
        if d.textlength(test, font=font) <= w - 160 or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    y = int(h * y_frac) - (len(lines) - 1) * line_h // 2
    for ln in lines:
        x = (w - d.textlength(ln, font=font)) / 2
        d.text((x, y), ln, font=font, fill=color,
               stroke_width=stroke, stroke_fill=(30, 0, 40))
        y += line_h
    return layer


def make_thumbnails(img_path, text):
    base = Image.open(img_path).convert("RGB").resize((W, H), Image.LANCZOS)

    tall = base.convert("RGBA")
    tall.alpha_composite(caption_layer(text, (255, 225, 60), y_frac=0.78,
                                       font_size=135, line_h=160, stroke=13))
    tall.convert("RGB").save(f"{OUT}/thumbnail.jpg", quality=92)

    crop_h = int(W * 9 / 16)
    top = int(H * 0.22)
    wide = base.crop((0, top, W, top + crop_h)).resize((1280, 720), Image.LANCZOS)
    wide = wide.convert("RGBA")
    wide.alpha_composite(caption_layer(text, (255, 225, 60), size=(1280, 720),
                                       font_size=100, y_frac=0.72,
                                       line_h=115, stroke=10))
    wide.convert("RGB").save(f"{OUT}/thumbnail_wide.jpg", quality=92)


def camera_clip(img_path, dur, caption_img):
    img = Image.open(img_path).convert("RGB").resize((W, H), Image.LANCZOS)
    z0, z1 = random.choice([(1.0, 1.2), (1.2, 1.0), (1.05, 1.25), (1.0, 1.12)])
    x0, y0 = random.uniform(0.3, 0.7), random.uniform(0.3, 0.7)
    x1, y1 = random.uniform(0.3, 0.7), random.uniform(0.3, 0.7)

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
        view.paste(caption_img, (0, 0), caption_img)
        return np.asarray(view)

    return VideoClip(frame, duration=dur)


def main():
    os.makedirs(OUT, exist_ok=True)
    story = make_story()
    print("Title:", story["title"])
    chars = story["characters"]
    seed = random.randint(1, 99999)

    clips, prev_img, shot_imgs = [], None, []
    colors = [(255, 255, 255), (255, 225, 60), (255, 130, 200)]

    for i, shot in enumerate(story["shots"]):
        img = f"{OUT}/shot{i}.jpg"
        ok = make_image(build_prompt(shot, chars), seed, img)
        if not ok:
            if prev_img is None:
                raise RuntimeError("Image generation failed for the first shot")
            img = prev_img
        prev_img = img
        shot_imgs.append(img)

        audio, dur = None, 2.6
        line = (shot.get("line") or "").strip()
        if line and shot.get("speaker", "none") != "none":
            aud = f"{OUT}/shot{i}.mp3"
            asyncio.run(make_voice(line, shot["speaker"], aud))
            audio = AudioFileClip(aud)
            dur = max(2.6, audio.duration + 0.5)

        cap = caption_layer(shot.get("caption", ""), colors[i % len(colors)])
        clip = camera_clip(img, dur, cap)
        if audio is not None:
            clip = clip.set_audio(audio)
        clips.append(clip)
        print(f"Shot {i + 1}/{len(story['shots'])} done")
        time.sleep(2)

    video = concatenate_videoclips(clips, method="compose")

    if os.path.exists("music.mp3"):
        music = AudioFileClip("music.mp3").volumex(0.12)
        music = afx.audio_loop(music, duration=video.duration)
        tracks = [video.audio, music] if video.audio else [music]
        video = video.set_audio(CompositeAudioClip(tracks))

    video.write_videofile(f"{OUT}/video.mp4", fps=FPS, codec="libx264",
                          audio_codec="aac", preset="veryfast")

    ti = story.get("thumbnail_shot", len(shot_imgs) // 2)
    if not isinstance(ti, int) or not 0 <= ti < len(shot_imgs):
        ti = len(shot_imgs) // 2
    make_thumbnails(shot_imgs[ti], story.get("thumbnail_text") or story["title"][:25])

    meta = {k: story[k] for k in ("title", "description", "hashtags")}
    meta["made_for_kids"] = True
    json.dump(meta, open(f"{OUT}/meta.json", "w"), ensure_ascii=False, indent=2)
    print("Done: output/video.mp4, thumbnail.jpg, meta.json")


if __name__ == "__main__":
    main()
