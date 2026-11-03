import os
import re
import sys
import base64
import json
import time
import random
import asyncio
import traceback
import textwrap
import subprocess

import requests
import numpy as np
import edge_tts
from instagram_post import post_to_instagram
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
# Ovoz: biroz qalin (pitch past) va tabiiyroq tezlik
VOICE_RATE = "-3%"
VOICE_PITCH = "-6Hz"

# Gemini TTS (asosiy ovoz). Xato bo'lsa yuqoridagi Sardor ovozi ishlaydi.
USE_GEMINI_TTS = True
GEMINI_TTS_MODEL = "gemini-3.8-flash-tts"
GEMINI_TTS_VOICE = "Bodi"
# Bo'sh qoldirilsa, standart ovoz ishlatiladi (tavsiya etiladi).
GEMINI_TTS_STYLE = ""

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE_DIR, "output")
TEMP = os.path.join(OUT, "temp")

VIDEO_PATH = os.path.join(OUT, "reels.mp4")
SOURCES_PATH = os.path.join(OUT, "sources.txt")
SCRIPT_PATH = os.path.join(OUT, "script.json")
USED_TOPICS_PATH = os.path.join(BASE_DIR, "used_topics.json")

os.makedirs(OUT, exist_ok=True)
os.makedirs(TEMP, exist_ok=True)

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY")

if not GEMINI_KEY:
    raise RuntimeError("GEMINI_API_KEY topilmadi!")

if not PIXABAY_KEY:
    raise RuntimeError("PIXABAY_API_KEY topilmadi!")

GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
]

GEMINI_BASE = (
    "https://generativelanguage.googleapis.com/v1beta/models"
)

PIXABAY_URL = "https://pixabay.com/api/videos/"

# Cloudflare Workers AI (FLUX.1 schnell) - tekin AI rasm yaratish.
# Kalitlar bo'lmasa yoki xato bo'lsa, bot Pixabay videosiga o'tadi.
CF_TOKEN = (os.getenv("CLOUDFLARE_API_TOKEN") or "").strip()
CF_ACCOUNT = (os.getenv("CLOUDFLARE_ACCOUNT_ID") or "").strip()

CF_MODEL = "@cf/black-forest-labs/flux-1-schnell"

IMAGE_STYLE = (
    "photorealistic, cinematic lighting, highly detailed, "
    "vertical composition, no text, no letters, no watermark"
)

# Cloudflare limiti tugasa yoki token xato bo'lsa, qolgan sahnalarda
# qayta urinmaslik uchun o'chirib qo'yiladi.
CF_DISABLED = False

# Ssenariydagi sahnalarning asosiy ko'rinadigan so'zlari.
# main() ichida to'ldiriladi, video topilmaganda zaxira sifatida ishlatiladi.
SCENE_FALLBACKS = []


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

CATEGORIES = [
    "inson tanasi haqida hayratlanarli va kutilmagan faktlar",
    "miya va psixologiya: odamlar nega shunday qiladi",
    "uyqu, ovqat va salomatlik haqidagi mif va haqiqatlar",
    "pul, boylik va boy odamlarning odatlari haqida faktlar",
    "hayvonlar olamidagi ajoyib va kutilmagan faktlar",
    "koinot haqida tasavvurga sig'maydigan faktlar",
    "nima bo'lardi agar... (qiziqarli ilmiy savollar)",
    "dunyo rekordlari va g'alati, lekin haqiqiy voqealar",
    "Amir Temur, Ulug'bek, Bobur va Turon tarixidagi shok voqealar",
    "qadimgi dunyo sirlari va yechilmagan jumboqlar",
    "mashhur kashfiyotlar va tasodifan qilingan ixtirolar",
    "texnologiya va sun'iy intellekt haqida hayratlanarli faktlar",
    "dunyoning eng g'alati joylari va mamlakatlari",
    "sevgi, do'stlik va munosabatlar psixologiyasi",
]


def build_prompt():
    previous = load_used_topics()

    previous_text = "\n".join(
        f"- {x}" for x in previous[-30:]
    )

    category = random.choice(CATEGORIES)

    return f"""
Sen professional o'zbek Instagram Reels ssenariy muallifisan.
Matnni diktor ovozda o'qiydi, shuning uchun u og'zaki nutqqa mos bo'lsin.

AUDITORIYA:
O'zbek tilida gaplashadigan oddiy, keng ommaviy auditoriya.
"Voy, rostdanmi?" deb hayratlanadigan narsalarni yaxshi ko'radi.

BUGUNGI KATEGORIYA:
{category}

VAZIFA:
Shu kategoriyada ko'pchilik qiziqadigan, tushunarli va
haqiqiy faktga asoslangan 35-45 soniyalik ssenariy yarat.

MAVZU QOIDALARI:
- Odamning o'z hayoti, tanasi, puli, qo'rquvi yoki odatlari
  bilan bog'liq mavzu afzal.
- Juda tor ilmiy mavzu tanlama.
- Mavzu shunday bo'lsinki, odam do'stiga yuborsin.
- Raqam, taqqoslash va kutilmagan burilish ishlat.

OVOZ UCHUN MATN QOIDALARI (juda muhim):
- Birinchi sahna: shok fakt yoki savol bilan kuchli hook.
- Tomoshabinga "siz" deb murojaat qil.
- Gaplar qisqa bo'lsin (8-12 so'z), tirik suhbatdek.
- Raqamlarni so'z bilan yoz: "2500" emas, "ikki ming besh yuz".
- Qisqartma, belgi (%, $, &), qavs va tirnoq ishlatma.
  "foiz", "dollar" deb yoz.
- Chet el nomlarini o'qilishi bo'yicha yoz.
- Vergul va nuqtani to'g'ri qo'y, ovoz shu joylarda to'xtaydi.
- Matn lotin yozuvida, o' va g' harflari bilan yozilsin.
- Oxirida kutilmagan yakun yoki munozara savoli bo'lsin.
- Soxta fakt va uydirma manba ishlatma.

VIDEO QIDIRUV SO'ZLARI QOIDALARI (juda muhim):
- Keywords oddiy ingliz tilida, Pixabay uchun yoziladi.
- Har sahnada ovoz nima haqida gapirayotgan bo'lsa,
  video aynan shuni ko'rsatsin.
- Birinchi keyword: sahnadagi asosiy ko'rinadigan narsa,
  1-2 so'z (masalan: "human brain", "pyramid", "gold coins").
- Har sahnada keywords ichida shu sahnaning asosiy
  narsasi nomi bo'lsin (mavzu nima haqida bo'lsa, o'sha narsa).
- Mavhum so'z ishlatma (fact, history, secret, idea, mystery).
- Faqat ko'z bilan ko'rinadigan narsalarni yoz.
- Har sahnaga 3 ta keyword ber, eng aniqdan umumiyga qarab.

RASM TA'RIFI QOIDALARI (juda muhim):
- Har sahnaga "image_prompt" yoz: ingliz tilida, 1-2 gap.
- Sahnada ovoz gapirayotgan narsani aniq ko'rsat
  (masalan: "an ancient stone pyramid at sunset, golden light").
- Qayerda, qanday yorug'lik, qanday kayfiyat ekanini yoz.
- Rasmda yozuv, harf, raqam bo'lmasin.
- Odamning yaqin yuzini chizma. Hayvon, narsa, manzara,
  koinot, bino, qo'l yoki orqadan ko'rinish afzal.
- Tarixiy shaxs bo'lsa, uning davri manzarasi, qal'a,
  qurol-yarog' yoki otliqlarni orqadan chiz.

AVVAL ISHLATILGAN MAVZULAR (takrorlama):
{previous_text}

JSON FORMAT:
{{
  "topic": "Mavzu nomi",
  "title": "Qisqa va qiziqarli sarlavha",
  "hook": "Videoning birinchi jumlasi",
  "category": "kategoriya nomi",
  "scenes": [
    {{
      "text": "Ovoz uchun o'zbekcha matn",
      "image_prompt": "detailed English visual description of this scene",
      "keywords": [
        "main visible subject",
        "related visual",
        "general visual"
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
- Har sahna 1-2 qisqa gapdan iborat bo'lsin.
- JSON'dan tashqari hech narsa yozma.
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

                if not scene.get("image_prompt"):
                    scene["image_prompt"] = str(
                        scene["keywords"][0]
                    )

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

    # Zaxira 1: shu ssenariyning boshqa sahnalaridagi asosiy
    # so'zlar (mavzuga aloqador bo'lib qoladi)
    queries.extend(SCENE_FALLBACKS)

    # Zaxira 2: eng oxirgi umumiy zaxira
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

ONES = [
    "nol", "bir", "ikki", "uch", "to'rt",
    "besh", "olti", "yetti", "sakkiz", "to'qqiz"
]

TENS = [
    "", "o'n", "yigirma", "o'ttiz", "qirq",
    "ellik", "oltmish", "yetmish", "sakson", "to'qson"
]


def _under_thousand(n):
    parts = []

    hundreds = n // 100
    rest = n % 100

    if hundreds:
        parts.append(ONES[hundreds])
        parts.append("yuz")

    if rest >= 10:
        parts.append(TENS[rest // 10])

    if rest % 10:
        parts.append(ONES[rest % 10])

    return parts


def number_to_words(n):
    """Butun sonni o'zbekcha so'zga aylantiradi (ovoz uchun)."""
    if n == 0:
        return ONES[0]

    scales = [
        (10 ** 9, "milliard"),
        (10 ** 6, "million"),
        (10 ** 3, "ming"),
    ]

    parts = []

    for value, name in scales:
        chunk = n // value

        if chunk:
            if n >= 10 ** 12:
                return str(n)

            parts.extend(_under_thousand(chunk))
            parts.append(name)
            n %= value

    if n:
        parts.extend(_under_thousand(n))

    return " ".join(parts)


def clean_voice_text(text):
    """
    Ovoz uchun matnni tozalaydi: belgilar va raqamlar
    so'zga aylanadi, shunda talaffuz to'g'ri chiqadi.
    Subtitrlar asl matnda qoladi.
    """
    text = str(text)

    text = text.replace("%", " foiz ")
    text = text.replace("$", " dollar ")
    text = text.replace("&", " va ")
    text = text.replace("€", " yevro ")

    # Kasr sonlar: 3.5 yoki 3,5
    def decimal(match):
        whole = number_to_words(int(match.group(1)))
        frac = " ".join(
            ONES[int(d)] for d in match.group(2)
        )
        return f"{whole} butun {frac}"

    text = re.sub(
        r"(\d+)[.,](\d+)",
        decimal,
        text
    )

    # Bo'shliq bilan yozilgan minglar: 1 000 000
    text = re.sub(
        r"(?<=\d) (?=\d{3}(?!\d))",
        "",
        text
    )

    text = re.sub(
        r"\d+",
        lambda m: number_to_words(int(m.group(0))),
        text
    )

    # Qavs, tirnoq va boshqa ortiqcha belgilar
    text = re.sub(r"[()\[\]{}\"«»“”*_#~^<>|/\\]", " ", text)
    text = text.replace("—", ", ").replace("–", ", ")

    text = re.sub(r"\s+", " ", text).strip()

    return text


async def create_voice(text, path):
    speaker = edge_tts.Communicate(
        text=clean_voice_text(text),
        voice=VOICE,
        rate=VOICE_RATE,
        pitch=VOICE_PITCH
    )

    await speaker.save(path)


GEMINI_TTS_FAILS = 0


def gemini_tts(text, path):
    """
    Gemini TTS bilan ovoz yaratadi (WAV).
    Muvaffaqiyatli bo'lsa fayl yo'lini, aks holda None qaytaradi.
    """
    global GEMINI_TTS_FAILS

    if not USE_GEMINI_TTS or GEMINI_TTS_FAILS >= 2:
        return None

    content = {
        "type": "text",
        "text": clean_voice_text(text),
    }

    if GEMINI_TTS_STYLE.strip():
        content["annotations"] = [{
            "type": "speech_metadata",
            "style": GEMINI_TTS_STYLE.strip(),
        }]

    payload = {
        "model": GEMINI_TTS_MODEL,
        "input": [{
            "type": "user_input",
            "content": [content],
        }],
        "response_format": {"type": "audio"},
        "generation_config": {
            "speech_config": [
                {"voice": GEMINI_TTS_VOICE}
            ]
        },
    }

    try:
        response = request_retry(
            "POST",
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            tries=3,
            headers={
                "x-goog-api-key": GEMINI_KEY,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=(20, 180)
        )

        try:
            if not response.ok:
                raise RuntimeError(
                    f"HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                )

            data = response.json()

        finally:
            response.close()

        audio_b64 = None

        for step in data.get("steps", []):
            if step.get("type") != "model_output":
                continue

            for part in step.get("content", []):
                if part.get("type") == "audio" and part.get("data"):
                    audio_b64 = part["data"]

        if not audio_b64:
            raise RuntimeError(
                f"Audio qaytmadi: {str(data)[:300]}"
            )

        wav_path = os.path.splitext(path)[0] + "_gemini.wav"

        with open(wav_path, "wb") as f:
            f.write(base64.b64decode(audio_b64))

        if os.path.getsize(wav_path) < 2000:
            raise RuntimeError("Audio fayli juda kichik.")

        GEMINI_TTS_FAILS = 0
        print("Gemini ovozi tayyor.")

        time.sleep(1)

        return wav_path

    except Exception as error:
        GEMINI_TTS_FAILS += 1
        print(f"Gemini TTS xatosi: {error}")
        print("Sardor ovoziga o'tildi.")
        return None


def synthesize_voice(text, path):
    # 1) Asosiy: Gemini TTS
    gemini_path = gemini_tts(text, path)

    if gemini_path:
        return gemini_path

    # 2) Zaxira: edge-tts (Sardor)
    asyncio.run(
        create_voice(text, path)
    )

    if not os.path.exists(path):
        raise RuntimeError("Ovoz fayli yaratilmadi.")

    return path


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

def strip_extra_streams(path):
    """
    Pixabay videolaridagi ortiqcha oqimlarni (timecode, data) olib
    tashlaydi, faqat video qoladi. Xato bo'lsa, asl fayl qaytariladi.
    """
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        out = os.path.splitext(path)[0] + "_clean.mp4"

        result = subprocess.run(
            [
                exe, "-y", "-i", path,
                "-map", "0:v:0", "-c", "copy", "-an",
                out
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120
        )

        if (
            result.returncode == 0
            and os.path.exists(out)
            and os.path.getsize(out) > 30000
        ):
            return out

    except Exception as error:
        print(f"Video tozalash xatosi: {error}")

    return path


def prepare_video(path, duration):
    path = strip_extra_streams(path)
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
# 8b. AI RASM (CLOUDFLARE) VA HARAKAT EFFEKTI
# =========================================================

def generate_ai_image(prompt, scene_number):
    """
    Cloudflare FLUX bilan rasm yaratadi.
    Muvaffaqiyatli bo'lsa fayl yo'lini, aks holda None qaytaradi.
    """
    global CF_DISABLED

    if not CF_TOKEN or not CF_ACCOUNT:
        print(
            "Cloudflare kalitlari topilmadi "
            "(CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID)."
        )
        return None

    if CF_DISABLED:
        return None

    prompt = str(prompt or "").strip()

    if not prompt:
        return None

    full_prompt = f"{prompt}, {IMAGE_STYLE}"[:2000]

    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{CF_ACCOUNT}/ai/run/{CF_MODEL}"
    )

    try:
        response = request_retry(
            "POST",
            url,
            tries=2,
            headers={
                "Authorization": f"Bearer {CF_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"prompt": full_prompt},
            timeout=(20, 120)
        )

        try:
            if response.status_code in (401, 403, 429):
                CF_DISABLED = True
                print(
                    f"Cloudflare HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )
                print("Cloudflare o'chirildi, Pixabay ishlatiladi.")
                return None

            if not response.ok:
                print(
                    f"Cloudflare HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )
                return None

            payload = response.json()

        finally:
            response.close()

        result = payload.get("result", payload)
        image_b64 = (
            result.get("image")
            if isinstance(result, dict)
            else None
        )

        if not image_b64:
            print(f"Cloudflare rasm qaytarmadi: {str(payload)[:200]}")
            return None

        path = os.path.join(
            TEMP,
            f"image_{scene_number}.jpg"
        )

        with open(path, "wb") as f:
            f.write(base64.b64decode(image_b64))

        # Rasm buzilmaganini tekshirish
        with Image.open(path) as check:
            check.verify()

        print(f"AI rasm tayyor: sahna {scene_number}")

        time.sleep(1)

        return path

    except Exception as error:
        print(f"AI rasm xatosi: {error}")
        return None


def make_image_clip(path, duration, index):
    """Rasmni sekin yaqinlashtirib/uzoqlashtirib video qiladi."""
    img = Image.open(path).convert("RGB")

    scale = max(W / img.width, H / img.height)

    img = img.resize(
        (
            int(img.width * scale) + 2,
            int(img.height * scale) + 2
        ),
        Image.LANCZOS
    )

    duration = max(float(duration), 0.1)

    base = ImageClip(np.array(img)).with_duration(duration)

    if index % 2 == 0:
        def zoom(t):
            return 1.0 + 0.14 * (t / duration)
    else:
        def zoom(t):
            return 1.14 - 0.14 * (t / duration)

    moving = base.resized(zoom).with_position("center")

    return CompositeVideoClip(
        [moving],
        size=(W, H)
    ).with_duration(duration)


# =========================================================
# 9. SAHNA YARATISH
# =========================================================

def build_scene(scene, index, total):
    print(
        f"\nSahna {index + 1}/{total}"
    )

    text = scene["text"]
    keywords = scene["keywords"]

    audio_path = os.path.join(
        TEMP,
        f"voice_{index + 1}.mp3"
    )

    audio_path = synthesize_voice(
        text,
        audio_path
    )

    audio = AudioFileClip(audio_path)
    duration = audio.duration

    video = None
    source_url = None

    # 1) Asosiy usul: AI rasm + harakat effekti
    image_path = generate_ai_image(
        scene.get("image_prompt"),
        index + 1
    )

    if image_path:
        try:
            video = make_image_clip(
                image_path,
                duration,
                index
            )
            source_url = "AI rasm (Cloudflare FLUX.1 schnell)"
        except Exception as error:
            print(f"Rasm effekti xatosi: {error}")
            video = None

    # 2) Zaxira: Pixabay videosi
    if video is None:
        last_error = None

        for attempt in range(4):
            try:
                video_path, source_url = get_video(
                    keywords,
                    index + 1
                )

                video = prepare_video(
                    video_path,
                    duration
                )
                break

            except Exception as error:
                last_error = error
                video = None
                print(
                    f"Video ishlamadi ({attempt + 1}/4): "
                    f"{str(error)[:150]}"
                )

        if video is None:
            raise RuntimeError(
                f"Sahna uchun video topilmadi: {last_error}"
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
        "VIDEO / RASM MANBALARI:"
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


def check_cloudflare():
    print("\nCloudflare tekshiruvi:")
    print("Token uzunligi:", len(CF_TOKEN))
    print("Account ID uzunligi:", len(CF_ACCOUNT), "(32 bo'lishi kerak)")
    print("Account ID da 'http' bormi:", "http" in CF_ACCOUNT.lower())

    if not CF_TOKEN:
        return

    headers = {"Authorization": f"Bearer {CF_TOKEN}"}
    base = "https://api.cloudflare.com/client/v4"

    # Hisob tokenlari (account-owned) uchun
    if CF_ACCOUNT:
        try:
            r = requests.get(
                f"{base}/accounts/{CF_ACCOUNT}/tokens/verify",
                headers=headers,
                timeout=30
            )
            print("Account verify:", r.status_code, r.text[:300])
        except Exception as error:
            print("Account verify xatosi:", error)

    # Foydalanuvchi tokenlari (user) uchun
    try:
        r = requests.get(
            f"{base}/user/tokens/verify",
            headers=headers,
            timeout=30
        )
        print("User verify:", r.status_code, r.text[:300])
    except Exception as error:
        print("User verify xatosi:", error)


# =========================================================
# 12. ASOSIY JARAYON
# =========================================================

def main():
    print("\n================================")
    print("PROFESSIONAL UZBEK REELS BOT")
    print("================================\n")

    clean_temp()
    check_cloudflare()

    # Gemini ssenariy yaratadi
    data = generate_script()

    save_script(data)

    # Video topilmaganda mavzuga aloqador zaxira so'zlar
    SCENE_FALLBACKS.clear()

    for scene in data["scenes"]:
        keywords = scene.get("keywords") or []

        if keywords:
            word = str(keywords[0]).strip()

            if word and word not in SCENE_FALLBACKS:
                SCENE_FALLBACKS.append(word)

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

    if os.getenv("DRY_RUN", "").strip().lower() == "true":
        print("\nSINOV REJIMI: Instagram'ga joylash o'tkazib yuborildi.")
    else:
        post_to_instagram(VIDEO_PATH, data)


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
