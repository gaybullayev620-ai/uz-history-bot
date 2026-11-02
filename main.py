
import os
import re
import sys
import json
import time
import random
import asyncio
import traceback
import textwrap

import requests
import numpy as np
import edge_tts

from PIL import Image, ImageDraw, ImageFont

from moviepy import (
    VideoFileClip,
    AudioFileClip,
    ImageClip,
    CompositeVideoClip,
    concatenate_videoclips,
    vfx,
)


# =========================================================
# 1. SOZLAMALAR
# =========================================================

W, H = 1080, 1920
FPS = 30

VOICE = "uz-UZ-SardorNeural"
VOICE_RATE = "+5%"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE_DIR, "output")
TEMP = os.path.join(OUT, "temp")

VIDEO_PATH = os.path.join(OUT, "reels.mp4")
SOURCES_PATH = os.path.join(OUT, "sources.txt")
SCRIPT_PATH = os.path.join(OUT, "script.json")
USED_TOPICS_PATH = os.path.join(OUT, "used_topics.json")

os.makedirs(OUT, exist_ok=True)
os.makedirs(TEMP, exist_ok=True)

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY")

if not GEMINI_KEY:
    raise RuntimeError("GEMINI_API_KEY topilmadi!")

if not PIXABAY_KEY:
    raise RuntimeError("PIXABAY_API_KEY topilmadi!")

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

GEMINI_BASE = (
    "https://generativelanguage.googleapis.com/v1beta/models"
)

PIXABAY_URL = "https://pixabay.com/api/videos/"


# =========================================================
# 2. HTTP SO'ROVLAR
# =========================================================

def request_retry(method, url, tries=4, **kwargs):
    last_error = None

    for attempt in range(tries):
        try:
            response = requests.request(
                method,
                url,
                **kwargs
            )

            if response.status_code in (
                429, 500, 502, 503, 504
            ):
                if attempt < tries - 1:
                    response.close()
                    wait = 3 * (attempt + 1)
                    print(f"Server band. {wait}s kutamiz...")
                    time.sleep(wait)
                    continue

            return response

        except requests.RequestException as error:
            last_error = error

            if attempt < tries - 1:
                time.sleep(3 * (attempt + 1))

    raise RuntimeError(
        f"So'rov bajarilmadi: {last_error}"
    )


# =========================================================
# 3. ISHLATILGAN MAVZULAR
# =========================================================

def load_used_topics():
    if not os.path.exists(USED_TOPICS_PATH):
        return []

    try:
        with open(
            USED_TOPICS_PATH,
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except Exception:
        return []


def save_topic(topic):
    topics = load_used_topics()
    topics.append(topic)

    with open(
        USED_TOPICS_PATH,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            topics[-100:],
            f,
            ensure_ascii=False,
            indent=2
        )


# =========================================================
# 4. GEMINI SSENARIY
# =========================================================

def build_prompt():
    previous = load_used_topics()

    previous_text = "\n".join(
        f"- {x}" for x in previous[-30:]
    )

    return f"""
Sen professional Instagram Reels ssenariy muallifisan.

AUDITORIYA:
O'zbek tilida gaplashadigan, qiziqarli faktlar,
ilm-fan, tarix, tabiat va koinotga qiziquvchi odamlar.

VAZIFA:
Har safar yangi, hayratlanarli va tekshiriladigan
haqiqiy fakt asosida 35-45 soniyalik video ssenariysi yarat.

PROFESSIONAL USLUB:
- Birinchi 2 soniyada kuchli HOOK.
- Tomoshabinda savol va qiziqish uyg'ot.
- Keraksiz kirish va salomlashish bo'lmasin.
- Har bir gap keyingi sahnani ko'rishga undasin.
- Matn tabiiy, sodda va ravon o'zbek tilida bo'lsin.
- Oxirida qisqa, esda qoladigan yakun bo'lsin.
- Soxta fakt, yolg'on statistika va uydirma manba ishlatma.

AVVAL ISHLATILGAN MAVZULAR:
{previous_text}

JSON FORMAT:
{{
  "topic": "Mavzu nomi",
  "title": "Qisqa va qiziqarli sarlavha",
  "hook": "Videoning birinchi jumlasi",
  "category": "science",
  "scenes": [
    {{
      "text": "Ovoz uchun o'zbekcha matn",
      "keywords": [
        "english video search keyword",
        "another english keyword"
      ]
    }}
  ],
  "sources": [
    "Faktni tekshirish uchun haqiqiy manba nomi yoki URL"
  ]
}}

TALABLAR:
- 6 ta sahna yarat.
- Umumiy matn 35-45 soniyaga mos bo'lsin.
- Birinchi sahna eng kuchli hook bo'lsin.
- Har bir sahna 1-2 qisqa gapdan iborat bo'lsin.
- Keywords ingliz tilida bo'lsin.
- Har sahnaga 2-3 ta aniq qidiruv iborasi ber.
- JSON'dan tashqari hech qanday izoh yozma.
"""


def parse_json(text):
    text = text.strip()

    text = re.sub(
        r"^```(?:json)?",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(r"```$", "", text).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start < 0 or end < 0:
        raise RuntimeError("Gemini JSON qaytarmadi.")

    return json.loads(text[start:end + 1])


def call_gemini(model):
    url = f"{GEMINI_BASE}/{model}:generateContent"

    payload = {
        "contents": [{
            "parts": [{
                "text": build_prompt()
            }]
        }],
        "generationConfig": {
            "temperature": 0.85,
            "responseMimeType": "application/json"
        }
    }

    response = request_retry(
        "POST",
        url,
        tries=3,
        params={"key": GEMINI_KEY},
        json=payload,
        timeout=(20, 120)
    )

    try:
        if not response.ok:
            raise RuntimeError(
                f"Gemini HTTP {response.status_code}: "
                f"{response.text[:400]}"
            )

        data = response.json()

    finally:
        response.close()

    try:
        answer = (
            data["candidates"][0]
            ["content"]["parts"][0]["text"]
        )
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(
            f"Gemini javobi noto'g'ri: {str(data)[:400]}"
        )

    return parse_json(answer)


def generate_script():
    last_error = None

    for model in GEMINI_MODELS:
        try:
            print(f"\nGemini: {model}")

            data = call_gemini(model)

            if not data.get("topic"):
                raise RuntimeError("Mavzu yo'q.")

            if not data.get("scenes"):
                raise RuntimeError("Sahnalar yo'q.")

            if len(data["scenes"]) < 4:
                raise RuntimeError("Sahnalar soni yetarli emas.")

            for scene in data["scenes"]:
                if not scene.get("text"):
                    raise RuntimeError("Sahna matni yo'q.")

                if not scene.get("keywords"):
                    scene["keywords"] = ["nature", "science"]

            print("Mavzu:", data["topic"])

            return data

        except Exception as error:
            last_error = error
            print(f"Gemini xatosi: {error}")
            time.sleep(3)

    raise RuntimeError(
        f"Gemini ishlamadi: {last_error}"
    )


# =========================================================
# 5. PIXABAY VIDEO QIDIRISH
# =========================================================

search_cache = {}
used_video_ids = set()


def pixabay_search(query):
    query = query.strip().lower()

    if query in search_cache:
        return search_cache[query]

    response = request_retry(
        "GET",
        PIXABAY_URL,
        tries=3,
        params={
            "key": PIXABAY_KEY,
            "q": query,
            "per_page": 20,
            "safesearch": "true"
        },
        timeout=(20, 60)
    )

    try:
        if not response.ok:
            raise RuntimeError(
                f"Pixabay HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

        data = response.json()

    finally:
        response.close()

    if "hits" not in data:
        raise RuntimeError(
            f"Pixabay javobi noto'g'ri: {str(data)[:300]}"
        )

    hits = data.get("hits", [])
    search_cache[query] = hits

    print(
        f"Pixabay '{query}': {len(hits)} ta video"
    )

    return hits


def select_video_file(hit):
    videos = hit.get("videos", {})
    candidates = []

    for quality in (
        "large",
        "medium",
        "small",
        "tiny"
    ):
        item = videos.get(quality)

        if not isinstance(item, dict):
            continue

        url = item.get("url")
        width = item.get("width", 0)
        height = item.get("height", 0)

        if not url or not width or not height:
            continue

        portrait_bonus = (
            100000 if height > width else 0
        )

        score = (
            portrait_bonus
            + min(width * height, 8000000)
        )

        candidates.append((score, item))

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return candidates[0][1]


def download_video(url, path):
    response = request_retry(
        "GET",
        url,
        tries=3,
        stream=True,
        timeout=(20, 180)
    )

    total = 0

    try:
        response.raise_for_status()

        with open(path, "wb") as f:
            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):
                if chunk:
                    f.write(chunk)
                    total += len(chunk)

    finally:
        response.close()

    if total < 30000:
        if os.path.exists(path):
            os.remove(path)

        raise RuntimeError("Video fayli juda kichik.")


def get_video(keywords, scene_index):
    queries = []

    for keyword in keywords:
        keyword = str(keyword).strip()

        if not keyword:
            continue

        queries.append(keyword)

        words = keyword.split()

        if len(words) > 2:
            queries.append(" ".join(words[:2]))

    queries.extend([
        "cinematic nature",
        "science",
        "space",
        "ocean"
    ])

    queries = list(dict.fromkeys(queries))

    for query in queries:
        try:
            hits = list(pixabay_search(query))
        except Exception as error:
            print(f"Qidiruv xatosi: {error}")
            continue

        random.shuffle(hits)

        for hit in hits:
            video_id = hit.get("id")

            if video_id in used_video_ids:
                continue

            selected = select_video_file(hit)

            if not selected:
                continue

            path = os.path.join(
                TEMP,
                f"scene_{scene_index}.mp4"
            )

            try:
                download_video(
                    selected["url"],
                    path
                )

                used_video_ids.add(video_id)

                source_url = hit.get(
                    "pageURL",
                    "https://pixabay.com/videos/"
                )

                return path, source_url

            except Exception as error:
                print(f"Video yuklash xatosi: {error}")

                if os.path.exists(path):
                    os.remove(path)

    raise RuntimeError(
        f"Mos Pixabay videosi topilmadi: {keywords}"
    )


# =========================================================
# 6. O'ZBEKCHA OVOZ
# =========================================================

async def create_voice(text, path):
    speaker = edge_tts.Communicate(
        text=text,
        voice=VOICE,
        rate=VOICE_RATE
    )

    await speaker.save(path)


def synthesize_voice(text, path):
    asyncio.run(
        create_voice(text, path)
    )

    if not os.path.exists(path):
        raise RuntimeError("Ovoz fayli yaratilmadi.")


# =========================================================
# 7. SUBTITR DIZAYNI
# =========================================================

def get_font(size):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]

    for path in paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)

    return ImageFont.load_default()


def wrap_lines(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = ""

    for word in words:
        test = (current + " " + word).strip()

        width = draw.textbbox(
            (0, 0),
            test,
            font=font,
            stroke_width=1
        )[2]

        if width <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def make_overlay(text, scene_number, total_scenes, duration):
    image = Image.new(
        "RGBA",
        (W, H),
        (0, 0, 0, 0)
    )

    draw = ImageDraw.Draw(image)

    # Yuqoridagi kichik brend yozuvi
    small_font = get_font(34)

    draw.rounded_rectangle(
        (65, 75, 440, 145),
        radius=25,
        fill=(0, 0, 0, 145)
    )

    draw.text(
        (90, 92),
        "BILIM  /  FAKTLAR",
        font=small_font,
        fill=(255, 255, 255, 240)
    )

    # Sahnalar progress chizig'i
    bar_x = 65
    bar_y = 175
    bar_width = 950
    gap = 8

    segment_width = (
        bar_width - gap * (total_scenes - 1)
    ) / total_scenes

    for i in range(total_scenes):
        x1 = bar_x + i * (segment_width + gap)

        draw.rounded_rectangle(
            (
                int(x1),
                bar_y,
                int(x1 + segment_width),
                bar_y + 9
            ),
            radius=5,
            fill=(
                (255, 255, 255, 235)
                if i < scene_number
                else (255, 255, 255, 80)
            )
        )

    # Subtitr paneli
    font = get_font(60)

    lines = wrap_lines(
        draw,
        text,
        font,
        850
    )

    line_height = 82
    panel_height = max(
        150,
        len(lines) * line_height + 70
    )

    panel_top = int(H * 0.68)

    draw.rounded_rectangle(
        (
            55,
            panel_top,
            1025,
            panel_top + panel_height
        ),
        radius=35,
        fill=(0, 0, 0, 175),
        outline=(255, 255, 255, 45),
        width=2
    )

    y = panel_top + 35

    for line in lines:
        bbox = draw.textbbox(
            (0, 0),
            line,
            font=font,
            stroke_width=2
        )

        text_width = bbox[2] - bbox[0]
        x = (W - text_width) // 2

        draw.text(
            (x, y),
            line,
            font=font,
            fill="white",
            stroke_width=2,
            stroke_fill=(0, 0, 0, 220)
        )

        y += line_height

    return ImageClip(
        np.array(image)
    ).with_duration(duration)


# =========================================================
# 8. VIDEO FORMATINI MOSLASH
# =========================================================

def prepare_video(path, duration):
    clip = VideoFileClip(path)

    scale = max(
        W / clip.w,
        H / clip.h
    )

    clip = clip.resized(
        new_size=(
            int(clip.w * scale),
            int(clip.h * scale)
        )
    )

    clip = clip.cropped(
        x_center=clip.w / 2,
        y_center=clip.h / 2,
        width=W,
        height=H
    )

    # Qisqa lavhalarni kerakli muddatga aylantirish
    if clip.duration < duration:
        clip = clip.with_effects([
            vfx.Loop(duration=duration)
        ])
    else:
        clip = clip.subclipped(0, duration)

    return clip.with_duration(duration)


# =========================================================
# 9. SAHNA YARATISH
# =========================================================

def build_scene(scene, index, total):
    print(
        f"\nSahna {index + 1}/{total}"
    )

    text = scene["text"]
    keywords = scene["keywords"]

    video_path, source_url = get_video(
        keywords,
        index + 1
    )

    audio_path = os.path.join(
        TEMP,
        f"voice_{index + 1}.mp3"
    )

    synthesize_voice(
        text,
        audio_path
    )

    audio = AudioFileClip(audio_path)
    duration = audio.duration

    video = prepare_video(
        video_path,
        duration
    )

    overlay = make_overlay(
        text,
        index + 1,
        total,
        duration
    )

    final_scene = CompositeVideoClip(
        [video, overlay],
        size=(W, H)
    ).with_audio(audio)

    return final_scene, source_url


# =========================================================
# 10. FAYLLARNI SAQLASH
# =========================================================

def save_script(data):
    with open(
        SCRIPT_PATH,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


def save_sources(data, video_sources):
    lines = [
        "VIDEO MANBALARI",
        "=" * 45,
        "",
        f"Mavzu: {data['topic']}",
        f"Sarlavha: {data.get('title', '')}",
        "",
        "PIXABAY VIDEOLARI:"
    ]

    for index, url in enumerate(
        video_sources,
        1
    ):
        lines.append(f"{index}. {url}")

    lines.extend([
        "",
        "FAKT MANBALARI:"
    ])

    for index, source in enumerate(
        data.get("sources", []),
        1
    ):
        lines.append(f"{index}. {source}")

    with open(
        SOURCES_PATH,
        "w",
        encoding="utf-8"
    ) as f:
        f.write("\n".join(lines))


# =========================================================
# 11. TEMP FAYLLARNI TOZALASH
# =========================================================

def clean_temp():
    for name in os.listdir(TEMP):
        path = os.path.join(TEMP, name)

        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass


# =========================================================
# 12. ASOSIY JARAYON
# =========================================================

def main():
    print("\n================================")
    print("PROFESSIONAL UZBEK REELS BOT")
    print("================================\n")

    clean_temp()

    # Gemini ssenariy yaratadi
    data = generate_script()

    save_script(data)

    scenes = []
    video_sources = []

    try:
        total = len(data["scenes"])

        for index, scene in enumerate(data["scenes"]):
            final_scene, source_url = build_scene(
                scene,
                index,
                total
            )

            scenes.append(final_scene)
            video_sources.append(source_url)

        print("\nSahnalar birlashtirilmoqda...")

        final_video = concatenate_videoclips(
            scenes,
            method="compose"
        )

        print("\nMP4 render boshlandi...")

        final_video.write_videofile(
            VIDEO_PATH,
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            bitrate="8000k",
            preset="medium",
            threads=4,
            logger="bar"
        )

        final_video.close()

    finally:
        for scene in scenes:
            try:
                scene.close()
            except Exception:
                pass

    # Manbalarni alohida saqlash
    save_sources(
        data,
        video_sources
    )

    save_topic(
        data["topic"]
    )

    print("\n================================")
    print("VIDEO TAYYOR!")
    print("================================")
    print("Video:", VIDEO_PATH)
    print("Manbalar:", SOURCES_PATH)
    print("Ssenariy:", SCRIPT_PATH)


# =========================================================
# 13. XATOLARNI USHLASH
# =========================================================

if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        traceback.print_exc()
        print("\nBOT XATOSI:", error)
        sys.exit(1)
