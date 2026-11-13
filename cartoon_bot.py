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

CHARACTERS = {
    "dad": ("Sam, a friendly chubby adult man about 35 years old, light brown skin, "
            "short black hair, short trimmed black beard, kind brown eyes, "
            "always wearing a green t-shirt and grey sweatpants"),
    "girl": ("Lily, a cute 3-year-old toddler girl with a big round head, light brown skin, "
             "big brown eyes, chubby cheeks, two black pigtails with yellow hair ties, "
             "small round toddler body, always wearing a yellow t-shirt and light blue "
             "overalls, fully covered, child proportions"),
}

STYLE = ("3D animated Pixar-style family movie still, soft cinematic lighting, "
         "cozy colorful home, wholesome children's cartoon, everyone fully clothed "
         "in modest clothes, vertical 9:16, no text, no watermark")

SITUATIONS = [
    "Lily gives sleepy Sam a funny makeover with toy makeup",
    "Lily cooks a pretend dinner in the kitchen and Sam has to eat it",
    "Sam tries to nap on the sofa but Lily keeps sneaking toys onto him",
    "Lily plays doctor and treats Sam's pretend injuries",
    "Sam and Lily build a blanket fort that collapses",
    "Lily opens a pretend ice cream shop in the living room",
    "Sam tries to hide a cookie from hungry Lily",
    "Lily teaches Sam to dance and he trips",
    "Lily paints Sam's face while he plays a video game",
    "Sam and Lily have a tea party with stuffed toys",
    "Lily becomes the boss of a pretend hair salon and Sam is the customer",
    "Sam tries to fix a toy car and Lily gives silly advice",
    "Lily plays teacher and gives Sam homework",
    "Sam and Lily plant a garden and a funny thing grows",
    "Lily pretends to be a firefighter and rescues Sam from the sofa",
    "Sam tries to read a story but Lily changes every ending",
    "Lily and Sam have a living room race with toy cars",
    "Lily runs a pretend pizza shop and Sam orders",
    "Sam tries to give the toy dinosaur a picnic in the garden and Lily has other plans",
    "Lily is a magic show host and Sam is the volunteer",
    "Sam tries to hide a surprise cake for a party while Lily keeps nearly telling",
    "Lily pretends to be a superhero and Sam is the villain who stole her teddy",
    "Sam and Lily play hide and seek in the house",
    "Lily becomes a little painter and decorates the living room wall with washable paint",
    "Sam tries to teach Lily to ride a tiny bike in the garden",
    "Lily plays shopkeeper and sells Sam invisible vegetables",
    "Sam and Lily build a tower of cups that keeps falling",
    "Lily pretends to be a puppy and Sam has to walk her",
    "Sam cooks pancakes and Lily flips one onto his head",
    "Lily plays a pretend concert with pots and pans and Sam is the audience",
]

TWISTS = [
    "a surprise at the very end",
    "Sam loses patience for a second, then laughs",
    "Lily outsmarts Sam in a cute way",
    "a funny mess everywhere",
    "a pet or toy comes alive in Lily's imagination",
    "Sam ends up looking silly",
    "a sweet hug at the end",
    "the plan goes wrong in a funny way",
]


def pick_idea():
    day = int(time.time() // 86400)
    situation = SITUATIONS[day % len(SITUATIONS)]
    twist = random.choice(TWISTS)
    return f"{situation}. Twist: {twist}"


PROMPT = """You write wholesome, funny 25-30 second family cartoon shorts shown as a
3D animated mini movie for kids. These are episodes of one series, with the SAME two
characters every time: "Sam" (a chubby, kind dad) and "Lily" (his 3-year-old toddler
daughter). Every episode must be a NEW, original story - not the obvious version of the
idea. Use a surprising funny moment and a satisfying ending. The story takes place in
the living room, kitchen or garden of their home.
Starting idea for this episode: {idea}

Return ONLY JSON with this exact shape:
{{
  "title": "catchy YouTube Shorts title, max 80 chars, 1-2 emojis",
  "description": "2-3 sentence description, then a call to like and subscribe",
  "hashtags": ["#cartoon", "#family", "#shorts"],
  "thumbnail_shot": 0,
  "thumbnail_text": "max 3 words, curiosity-grabbing, for the thumbnail",
  "shots": [
    {{
      "who": ["dad", "girl"],
      "image_prompt": "camera shot type (wide / medium / close-up on face) + the ONE funny action happening + facial expressions + the room and props. Do not describe the characters' looks or clothes.",
      "speaker": "girl or dad or narrator or none",
      "line": "spoken line, max 8 words, or empty string",
      "caption": "big on-screen text, max 4 words, or empty string"
    }}
  ]
}}
Rules:
- 10 to 12 shots. Each shot shows a DIFFERENT action or place, easy to understand without sound.
- Mix wide shots and close-ups of faces. Start with a wide shot of the room.
- Build up to a funny punchline, then a cute ending. The last shot has the caption.
- thumbnail_shot is the 0-based number of the funniest close-up shot (a face with a big expression).
- In spoken lines and captions use the names Sam and Lily.
- Everyone is always fully dressed. Never write about bathing, bedrooms, swimming, underwear or anything revealing.
- Safe and wholesome for kids. No scary or sad content.
- Do not reuse these titles: {used}"""


def make_story():
    used = []
    if os.path.exists("used_titles.json"):
        used = json.load(open("used_titles.json"))
    body = {
        "contents": [{"parts": [{"text": PROMPT.format(
            idea=pick_idea(), used=used[-20:])}]}],
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
                assert story["shots"]
                used.append(story["title"])
                json.dump(used, open("used_titles.json", "w"))
                print("Story made with model:", model)
                return story
            except Exception as e:
                last_err = e
                print(f"Gemini model {model} failed: {e}")
        time.sleep(20 * (rnd + 1))
    raise RuntimeError(f"Story generation failed: {last_err}")


def build_prompt(shot):
    who = [k for k in shot.get("who", []) if k in CHARACTERS] or ["dad", "girl"]
    looks = "; ".join(f"{k}: {CHARACTERS[k]}" for k in who)
    return f"{shot['image_prompt']}. Characters: {looks}. {STYLE}"


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
    base = load_image(img_path)

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
    img = load_image(img_path)
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

    clips, prev_img, shot_imgs = [], None, []
    colors = [(255, 255, 255), (255, 225, 60), (255, 130, 200)]
    failed = 0

    for i, shot in enumerate(story["shots"]):
        img = f"{OUT}/shot{i}.jpg"
        ok = make_image(build_prompt(shot), img)
        if not ok:
            failed += 1
            print(f"  WARNING: shot {i + 1} image failed, reusing the previous one")
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

    print(f"Images that failed and were reused: {failed}")
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
