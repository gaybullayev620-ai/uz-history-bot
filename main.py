
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
# 1. ASOSIY SOZLAMALAR
# =========================================================

W = 1080
H = 1920
FPS = 30

VOICE = "uz-UZ-SardorNeural"
VOICE_RATE = "+5%"

VIDEO_DURATION_TARGET = 40

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE_DIR, "output")
TEMP = os.path.join(OUT, "temp")
DATA_DIR = os.path.join(BASE_DIR, "data")

VIDEO_PATH = os.path.join(OUT, "reels.mp4")
USED_TOPICS_FILE = os.path.join(DATA_DIR, "used_topics.json")

os.makedirs(OUT, exist_ok=True)
os.makedirs(TEMP, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)


# =========================================================
# 2. GITHUB SECRETS
# =========================================================

def need(name):
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"GitHub Secrets ichida {name} topilmadi!"
        )

    return value.strip()


GEMINI_KEY = need("GEMINI_API_KEY")
PIXABAY_KEY = need("PIXABAY_API_KEY")
TG_TOKEN = need("TELEGRAM_BOT_TOKEN")
TG_CHAT = need("TELEGRAM_CHAT_ID")


# =========================================================
# 3. API MANZILLARI VA MODELLAR
# =========================================================

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

PIXABAY_URL = "https://pixabay.com/api/videos/"

TELEGRAM_URL = (
    f"https://api.telegram.org/bot{TG_TOKEN}"
)


# =========================================================
# 4. HTTP SO'ROVLAR
# =========================================================

def http_retry(
    method,
    url,
    tries=4,
    backoff=3,
    **kwargs
):
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
                response.close()

                if attempt < tries - 1:
                    wait = backoff * (attempt + 1)
                    print(
                        f"  Server band. {wait} soniya kutamiz..."
                    )
                    time.sleep(wait)
                    continue

            return response

        except requests.RequestException as error:
            last_error = error

            if attempt < tries - 1:
                time.sleep(backoff * (attempt + 1))

    raise RuntimeError(
        f"Internet/API xatosi: {last_error}"
    )


# =========================================================
# 5. ISHLATILGAN MAVZULAR
# =========================================================

def load_used_topics():
    if not os.path.exists(USED_TOPICS_FILE):
        return []

    try:
        with open(
            USED_TOPICS_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            return json.load(file)

    except Exception:
        return []


def save_used_topic(topic):
    topics = load_used_topics()

    topics.append(topic)
    topics = topics[-100:]

    with open(
        USED_TOPICS_FILE,
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            topics,
            file,
            ensure_ascii=False,
            indent=2
        )


# =========================================================
# 6. GEMINI ORQALI MAVZU VA SSENARIY YARATISH
# =========================================================

def build_prompt():
    old_topics = load_used_topics()

    previous = "\n".join(
        f"- {topic}" for topic in old_topics[-30:]
    )

    return f"""
Sen professional o'zbekcha Instagram Reels ssenariy muallifisan.

Vazifang:
1. Odamlarni hayratga soladigan, qiziqarli va HAQIQIY fakt tanla.
2. Mavzu avvalgi mavzularga o'xshamasin.
3. 35-45 soniyalik o'zbekcha video ssenariysi yoz.
4. Birinchi jumla kuchli HOOK bo'lsin.
5. Matn sodda, ravon va tabiiy o'zbek tilida bo'lsin.
6. Har bir sahna uchun Pixabay'dan video topishga mos inglizcha kalit so'zlar yoz.
7. Faktlarni to'qib chiqarma. Ishonchli, ma'lum faktlardan foydalan.

Avval ishlatilgan mavzular:
{previous}

Javob FAQAT quyidagi JSON formatida bo'lsin:

{{
  "topic": "Mavzu nomi",
  "title": "Videoning qisqa sarlavhasi",
  "category": "science",
  "scenes": [
    {{
      "text": "O'zbekcha ovoz uchun matn.",
      "keywords": [
        "deep ocean",
        "underwater animals"
      ]
    }}
  ],
  "sources": [
    "Faktni tekshirish uchun manba nomi yoki URL"
  ]
}}

Talablar:
- 6 ta sahna yarat.
- Har bir sahnada 1-2 ta qisqa gap bo'lsin.
- Umumiy matn 35-45 soniyaga mos bo'lsin.
- keywords ingliz tilida bo'lsin.
- Har bir sahna uchun 2-3 ta qidiruv so'zi ber.
- JSON'dan tashqari izoh yozma.
"""


def extract_json(text):
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

    if start == -1 or end == -1:
        raise RuntimeError(
            "Gemini JSON formatida javob bermadi."
        )

    return json.loads(text[start:end + 1])


def call_gemini(model):
    url = f"{GEMINI_BASE}/{model}:generateContent"

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": build_prompt()
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.9,
            "responseMimeType": "application/json"
        }
    }

    response = http_retry(
        "POST",
        url,
        tries=3,
        backoff=5,
        params={"key": GEMINI_KEY},
        json=payload,
        timeout=(20, 120)
    )

    try:
        if not response.ok:
            raise RuntimeError(
                f"Gemini HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        data = response.json()

    finally:
        response.close()

    try:
        answer = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(
            f"Gemini javobini o'qib bo'lmadi: {str(data)[:500]}"
        )

    return extract_json(answer)


def validate_script(data):
    if not isinstance(data, dict):
        raise RuntimeError("Ssenariy formati noto'g'ri.")

    if not data.get("topic"):
        raise RuntimeError("Mavzu topilmadi.")

    scenes = data.get("scenes", [])

    if len(scenes) < 4:
        raise RuntimeError(
            "Gemini kam sahna yaratdi."
        )

    for scene in scenes:
        if not scene.get("text"):
            raise RuntimeError(
                "Sahnada matn mavjud emas."
            )

        if not scene.get("keywords"):
            scene["keywords"] = ["nature", "science"]

    data["scenes"] = scenes
    return data


def generate_script():
    last_error = None

    for model in GEMINI_MODELS:
        try:
            print(f"\nGemini modeli: {model}")

            data = call_gemini(model)
            data = validate_script(data)

            print("Mavzu:", data["topic"])
            print("Sahnalar:", len(data["scenes"]))

            return data

        except Exception as error:
            last_error = error

            print(
                f"Gemini {model} xatosi: "
                f"{str(error)[:300]}"
            )

            time.sleep(3)

    raise RuntimeError(
        f"Barcha Gemini modellari xato berdi: {last_error}"
    )


# =========================================================
# 7. PIXABAY VIDEO QIDIRISH
# =========================================================

search_cache = {}
used_video_ids = set()


def pixabay_search(query):
    query = query.strip().lower()

    if query in search_cache:
        return search_cache[query]

    response = http_retry(
        "GET",
        PIXABAY_URL,
        tries=3,
        backoff=4,
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
        f"  Pixabay '{query}': {len(hits)} ta video"
    )

    return hits


def pick_file(hit):
    videos = hit.get("videos", {})
    candidates = []

    for quality in (
        "large",
        "medium",
        "small",
        "tiny"
    ):
        video = videos.get(quality)

        if not isinstance(video, dict):
            continue

        url = video.get("url")
        width = video.get("width", 0)
        height = video.get("height", 0)

        if not url or not width or not height:
            continue

        portrait = height > width

        score = (
            (100000 if portrait else 0)
            + min(width * height, 8000000)
        )

        candidates.append((score, video))

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True
    )

    return candidates[0][1]


def download_video(url, path):
    response = http_retry(
        "GET",
        url,
        tries=3,
        backoff=3,
        stream=True,
        timeout=(20, 180)
    )

    total = 0

    try:
        response.raise_for_status()

        with open(path, "wb") as file:
            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):
                if chunk:
                    file.write(chunk)
                    total += len(chunk)

    finally:
        response.close()

    if total < 30000:
        if os.path.exists(path):
            os.remove(path)

        raise RuntimeError(
            "Yuklangan video juda kichik."
        )


def get_video(keywords, index):
    queries = []

    for keyword in keywords:
        keyword = str(keyword).strip()

        if keyword:
            queries.append(keyword)

            words = keyword.split()

            if len(words) > 2:
                queries.append(" ".join(words[:2]))

    queries.extend([
        "nature",
        "science",
        "space",
        "ocean"
    ])

    queries = list(dict.fromkeys(queries))

    for query in queries:
        try:
            hits = list(pixabay_search(query))

        except Exception as error:
            print(
                f"Pixabay qidiruv xatosi: {error}"
            )
            continue

        random.shuffle(hits)

        for hit in hits:
            video_id = hit.get("id")

            if video_id in used_video_ids:
                continue

            video = pick_file(hit)

            if not video:
                continue

            path = os.path.join(
                TEMP,
                f"scene_{index}.mp4"
            )

            try:
                download_video(
                    video["url"],
                    path
                )

                used_video_ids.add(video_id)

                source_url = hit.get(
                    "pageURL",
                    "https://pixabay.com/videos/"
                )

                print(
                    f"  Video yuklandi: {video_id}"
                )

                return path, source_url

            except Exception as error:
                print(
                    f"Video yuklash xatosi: {error}"
                )

                if os.path.exists(path):
                    os.remove(path)

    raise RuntimeError(
        f"Pixabay'dan video topilmadi: {keywords}"
    )


# =========================================================
# 8. O'ZBEKCHA OVOZ YARATISH
# =========================================================

async def make_voice(text, output_path):
    communicate = edge_tts.Communicate(
        text=text,
        voice=VOICE,
        rate=VOICE_RATE
    )

    await communicate.save(output_path)


def synth_voice(text, output_path):
    asyncio.run(
        make_voice(text, output_path)
    )

    if not os.path.exists(output_path):
        raise RuntimeError(
            "Ovoz fayli yaratilmadi."
        )


# =========================================================
# 9. SUBTITR YARATISH
# =========================================================

def get_font(size):
    possible_fonts = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
    ]

    for path in possible_fonts:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)

    return ImageFont.load_default()


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = ""

    for word in words:
        test = (
            current + " " + word
        ).strip()

        width = draw.textbbox(
            (0, 0),
            test,
            font=font
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


def make_subtitle(text, duration, output_path):
    image = Image.new(
        "RGBA",
        (W, H),
        (0, 0, 0, 0)
    )

    draw = ImageDraw.Draw(image)

    font = get_font(62)

    lines = wrap_text(
        draw,
        text,
        font,
        900
    )

    line_height = 82
    total_height = len(lines) * line_height

    start_y = int(H * 0.72) - total_height // 2

    for line in lines:
        bbox = draw.textbbox(
            (0, 0),
            line,
            font=font,
            stroke_width=3
        )

        text_width = bbox[2] - bbox[0]
        x = (W - text_width) // 2

        draw.text(
            (x, start_y),
            line,
            font=font,
            fill="white",
            stroke_width=4,
            stroke_fill="black"
        )

        start_y += line_height

    return ImageClip(
        np.array(image)
    ).with_duration(duration)


# =========================================================
# 10. VERTICAL VIDEO TAYYORLASH
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

    clip = clip.with_effects([
        vfx.Loop(duration=duration)
    ])

    clip = clip.with_duration(duration)

    return clip


# =========================================================
# 11. HAR BIR SAHNANI YIG'ISH
# =========================================================

def build_scene(scene, index):
    print(
        f"\nSahna {index + 1} tayyorlanmoqda..."
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

    synth_voice(
        text,
        audio_path
    )

    audio = AudioFileClip(audio_path)
    duration = audio.duration

    video = prepare_video(
        video_path,
        duration
    )

    subtitle = make_subtitle(
        text,
        duration,
        os.path.join(
            TEMP,
            f"subtitle_{index + 1}.png"
        )
    )

    final_scene = CompositeVideoClip(
        [video, subtitle],
        size=(W, H)
    ).with_audio(audio)

    return final_scene, source_url


# =========================================================
# 12. TELEGRAM XABAR YUBORISH
# =========================================================

def tg_message(text):
    url = f"{TELEGRAM_URL}/sendMessage"

    response = http_retry(
        "POST",
        url,
        tries=3,
        backoff=3,
        data={
            "chat_id": TG_CHAT,
            "text": text
        },
        timeout=(15, 60)
    )

    try:
        if not response.ok:
            raise RuntimeError(
                f"Telegram xatosi: {response.text[:300]}"
            )

        result = response.json()

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram xatosi: {result}"
            )

    finally:
        response.close()


def tg_send_video(path, caption):
    url = f"{TELEGRAM_URL}/sendVideo"

    with open(path, "rb") as video_file:
        response = requests.post(
            url,
            data={
                "chat_id": TG_CHAT,
                "caption": caption
            },
            files={
                "video": (
                    "reels.mp4",
                    video_file,
                    "video/mp4"
                )
            },
            timeout=(30, 600)
        )

    try:
        if not response.ok:
            raise RuntimeError(
                f"Telegram video xatosi: "
                f"{response.text[:500]}"
            )

        result = response.json()

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram video yuborilmadi: {result}"
            )

    finally:
        response.close()


# =========================================================
# 13. MANBALAR RO'YXATI
# =========================================================

def make_sources_message(data, video_sources):
    lines = [
        "📚 VIDEO MANBALARI",
        "",
        f"🎬 Mavzu: {data['topic']}",
        "",
        "🎥 Pixabay videolari:"
    ]

    for index, url in enumerate(
        video_sources,
        start=1
    ):
        lines.append(
            f"{index}. {url}"
        )

    sources = data.get("sources", [])

    if sources:
        lines.extend([
            "",
            "📖 Faktlar uchun manbalar:"
        ])

        for index, source in enumerate(
            sources,
            start=1
        ):
            lines.append(
                f"{index}. {source}"
            )

    return "\n".join(lines)


# =========================================================
# 14. ESKI VAQTINCHALIK FAYLLARNI TOZALASH
# =========================================================

def clean_temp():
    for filename in os.listdir(TEMP):
        path = os.path.join(TEMP, filename)

        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass


# =========================================================
# 15. ASOSIY JARAYON
# =========================================================

def main():
    print("\n================================")
    print("UZBEK REELS BOT ISHGA TUSHDI")
    print("================================\n")

    clean_temp()

    tg_message(
        "⏳ Bugungi Reels tayyorlanmoqda..."
    )

    # 1. Gemini ssenariy yaratadi
    data = generate_script()

    print("\nMavzu:", data["topic"])

    # 2. Har bir sahna uchun video va ovoz
    scenes = []
    video_sources = []

    for index, scene in enumerate(
        data["scenes"]
    ):
        final_scene, source_url = build_scene(
            scene,
            index
        )

        scenes.append(final_scene)
        video_sources.append(source_url)

    # 3. Barcha sahnalarni birlashtirish
    print("\nSahnalar birlashtirilmoqda...")

    final_video = concatenate_videoclips(
        scenes,
        method="compose"
    )

    # 4. MP4 render
    print("\nVideo render qilinmoqda...")

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

    for scene in scenes:
        scene.close()

    # 5. Telegram'ga video yuborish
    print("\nTelegram'ga yuborilmoqda...")

    caption = (
        f"🎬 {data['title']}\n\n"
        f"📌 {data['topic']}\n\n"
        "#bilim #faktlar #uzbekistan #reels"
    )

    tg_send_video(
        VIDEO_PATH,
        caption
    )

    # 6. Manbalarni alohida yuborish
    sources_message = make_sources_message(
        data,
        video_sources
    )

    tg_message(sources_message)

    # 7. Mavzuni tarixga saqlash
    save_used_topic(
        data["topic"]
    )

    print("\n================================")
    print("REELS MUVAFFAQIYATLI YUBORILDI!")
    print("================================")


# =========================================================
# 16. XATOLARNI USHLASH
# =========================================================

if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        traceback.print_exc()

        try:
            tg_message(
                "❌ Reels bot xatosi:\n"
                + str(error)[:1000]
            )
        except Exception:
            pass

        sys.exit(1)
