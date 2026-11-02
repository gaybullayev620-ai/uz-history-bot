#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Uzbek Reels bot
Gemini (ssenariy) -> Pexels (video) -> edge-tts (ovoz) -> MoviePy 2.x (montaj) -> Telegram

Talab qilinadi: moviepy>=2.1, edge-tts, requests, pillow, ffmpeg
"""

import os
import re
import sys
import json
import math
import time
import random
import asyncio
import traceback

import requests
import edge_tts

from moviepy import (
    VideoFileClip, AudioFileClip, TextClip,
    CompositeVideoClip, concatenate_videoclips,
    vfx,
)

# ==========================================
# 1. SOZLAMALAR
# ==========================================

W, H = 1080, 1920
FPS = 30
VOICE = "uz-UZ-SardorNeural"
VOICE_RATE = "+7%"

OUT = "out"
USED_FILE = "used_topics.json"

SHOT_SEC = 2.4          # bitta kadr o'rtacha davomiyligi (tez almashuv)
ZOOM = os.environ.get("ZOOM", "1") == "1"   # kadrlarda yengil yaqinlashish
ZOOM_AMOUNT = 0.06

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
# Model nomlarini GitHub'da GEMINI_MODELS (vergul bilan) orqali almashtirish mumkin.
GEMINI_MODELS = [
    m.strip() for m in os.environ.get(
        "GEMINI_MODELS",
        "gemini-flash-latest,gemini-2.5-flash,gemini-2.5-flash-lite,gemini-2.0-flash"
    ).split(",") if m.strip()
]

PEXELS_URL = "https://api.pexels.com/videos/search"

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]

os.makedirs(OUT, exist_ok=True)


def need(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Muhit o'zgaruvchisi topilmadi: {name}")
    return value


GEMINI_KEY = need("GEMINI_API_KEY")
PEXELS_KEY = need("PEXELS_API_KEY")
TG_TOKEN = need("TELEGRAM_BOT_TOKEN")
TG_CHAT = need("TELEGRAM_CHAT_ID")

FONT = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
if not FONT:
    raise SystemExit(
        "Shrift topilmadi. Workflow'ga qo'shing: "
        "sudo apt-get install -y fonts-dejavu-core"
    )

CATEGORIES = [
    "Psychology and the human brain",
    "Space and the universe",
    "Science and strange discoveries",
    "Unusual facts about the human body",
    "Ancient civilizations and history",
    "Animals and nature",
    "Technology and artificial intelligence",
    "Geography and mysterious places",
    "Incredible real-life events",
    "Everyday things with surprising origins",
    "Archaeology and forgotten discoveries",
    "Physics and strange natural phenomena",
]

# Pexels topilmasa ishlatiladigan umumiy zaxira so'rovlar
GENERIC_FALLBACKS = [
    "nature landscape", "city timelapse", "clouds sky",
    "ocean waves", "forest aerial", "night city lights",
]


# ==========================================
# 2. UMUMIY YORDAMCHILAR
# ==========================================

def http_retry(method, url, tries=4, backoff=3, retry_status=(429, 500, 502, 503, 504), **kwargs):
    """HTTP so'rov: vaqtinchalik xatolarda (503, 429, timeout) qayta urinadi."""
    last_error = "noma'lum xato"

    for attempt in range(1, tries + 1):
        wait = backoff * (2 ** (attempt - 1)) + random.random()

        try:
            response = requests.request(method, url, **kwargs)

            if response.status_code not in retry_status:
                return response

            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                wait = float(retry_after)

        except (requests.Timeout, requests.ConnectionError) as error:
            last_error = repr(error)

        if attempt < tries:
            wait = min(wait, 60)
            print(f"  qayta uriniladi ({attempt}/{tries - 1}), {wait:.0f}s kutiladi: {last_error[:120]}")
            time.sleep(wait)

    raise RuntimeError(f"{url.split('?')[0]} — {tries} urinishdan keyin ham xato: {last_error}")


def normalize(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", str(text).lower())).strip()


def load_used():
    if os.path.exists(USED_FILE):
        try:
            with open(USED_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception as error:
            print("used_topics.json o'qilmadi, yangidan boshlanadi:", error)
    return []


def save_used(used, topic):
    used.append(topic)
    with open(USED_FILE, "w", encoding="utf-8") as f:
        json.dump(used[-200:], f, ensure_ascii=False, indent=2)


# ==========================================
# 3. GEMINI: SSENARIY
# ==========================================

def build_prompt(category, used):
    return f"""
You are an elite short-form documentary scriptwriter
and visual storytelling director.

Create ONE original, highly engaging Uzbek Instagram
Reels video.

TODAY'S CATEGORY: {category}

Choose a fascinating, real and well-documented topic.
It must make an ordinary viewer curious immediately.

IMPORTANT RULES:

1. FACTUAL ACCURACY:
- Never invent facts, dates, statistics or quotations.
- Use established, verifiable information.
- Avoid myths presented as facts.
- Provide 2-3 reliable source URLs (real, well-known sites).
- If uncertain, choose another topic.

2. RETENTION:
- First sentence must be a powerful hook.
- Start with a surprising question, contradiction
  or unexpected fact.
- Do not start with greetings or introductions.
- Every sentence must create curiosity for the next.
- Include a surprising reveal near the end.
- The last sentence must be a specific question
  inviting comments.

3. SCRIPT:
- Uzbek language, Latin alphabet.
- 8-10 short, natural sentences.
- Total length about 450-600 characters
  (35-45 seconds when spoken).
- Conversational, cinematic storytelling.
- Write numbers and years in words (for correct
  voice pronunciation), no emojis, no hashtags,
  no brackets inside the sentences.
- No unnecessary introductions or filler.

4. VISUALS (stock footage from Pexels):
For every sentence provide 3 concrete English
search queries (2-4 words each).
Queries must describe visible objects, locations,
animals, environments or actions that real stock
footage would contain.
Each query must directly relate to that sentence.
Never use abstract concepts, names of specific
people, or brand names as visual searches.

5. EDITING:
Suggest a short, memorable opening title (max 6 words, Uzbek).
Suggest a final question for the screen (Uzbek).

6. ORIGINALITY:
Do not imitate an existing video's script.
Learn from effective storytelling techniques,
but create original content.

PREVIOUS TOPICS (DO NOT REPEAT):
{json.dumps(used[-100:], ensure_ascii=False)}

Return ONLY valid JSON, nothing else:

{{
  "topic": "Topic name",
  "category": "Category",
  "hook": "Opening hook",
  "title": "Short opening title",
  "caption": "Interesting description and hashtags",
  "ending_question": "Question for viewers",
  "sources": [
    {{"name": "Source name", "url": "https://..."}}
  ],
  "sentences": [
    {{
      "text": "Uzbek sentence",
      "keywords": [
        "concrete visual search",
        "concrete visual search",
        "concrete visual search"
      ]
    }}
  ]
}}
"""


class ModelSkip(Exception):
    """Bu modelni o'tkazib yuborish kerak (mavjud emas / ruxsat yo'q)."""


def call_gemini(model, prompt):
    response = http_retry(
        "POST",
        f"{GEMINI_URL}/{model}:generateContent",
        tries=3,
        backoff=5,
        headers={
            "x-goog-api-key": GEMINI_KEY,
            "Content-Type": "application/json",
        },
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.8,
                "maxOutputTokens": 8192,
            },
        },
        timeout=(15, 150),
    )

    if response.status_code in (400, 401, 403, 404):
        raise ModelSkip(f"HTTP {response.status_code}: {response.text[:250]}")

    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:250]}")

    body = response.json()
    candidates = body.get("candidates") or []

    if not candidates:
        raise RuntimeError(f"Bo'sh javob: {str(body.get('promptFeedback'))[:200]}")

    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))

    if not text.strip():
        raise RuntimeError(f"Matn yo'q, finishReason={candidates[0].get('finishReason')}")

    return text


def parse_json(raw):
    raw = raw.strip()

    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.S).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            return json.loads(raw[start:end + 1])
        raise


def validate_script(data, used):
    """Ssenariyni tekshiradi va tozalab qaytaradi. Xato bo'lsa ValueError."""
    if not isinstance(data, dict):
        raise ValueError("JSON obyekt emas")

    for key in ("topic", "title", "caption", "ending_question"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"'{key}' maydoni yo'q yoki bo'sh")
        data[key] = data[key].strip()

    if normalize(data["topic"]) in {normalize(u) for u in used}:
        raise ValueError(f"Mavzu takrorlandi: {data['topic']}")

    # Manbalar
    sources = []
    for s in data.get("sources") or []:
        if isinstance(s, dict) and str(s.get("url", "")).startswith("http"):
            sources.append({
                "name": str(s.get("name", "Manba")).strip() or "Manba",
                "url": str(s["url"]).strip(),
            })
    if not sources:
        raise ValueError("Manbalar yo'q")
    data["sources"] = sources

    # Gaplar
    sentences = data.get("sentences")
    if not isinstance(sentences, list) or not (6 <= len(sentences) <= 12):
        raise ValueError("Gaplar soni 6–12 oralig'ida emas")

    clean = []
    for i, item in enumerate(sentences):
        if not isinstance(item, dict):
            raise ValueError(f"{i + 1}-gap obyekt emas")

        text = re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
        if len(text) < 8:
            raise ValueError(f"{i + 1}-gap juda qisqa")

        keywords = [
            re.sub(r"\s+", " ", str(k)).strip()
            for k in (item.get("keywords") or [])
            if isinstance(k, str) and k.strip()
        ]
        if not keywords:
            raise ValueError(f"{i + 1}-gap uchun kalit so'zlar yo'q")

        clean.append({"text": text, "keywords": keywords[:5]})

    total_chars = sum(len(s["text"]) for s in clean)
    if not (250 <= total_chars <= 1100):
        raise ValueError(f"Matn uzunligi mos emas: {total_chars} belgi")

    data["sentences"] = clean
    return data


def generate_script(category, used):
    prompt = build_prompt(category, used)

    for round_no in range(1, 3):
        for model in GEMINI_MODELS:
            for attempt in range(1, 3):
                try:
                    print(f"Gemini: {model} (urinish {attempt}, davra {round_no})")
                    raw = call_gemini(model, prompt)
                    data = validate_script(parse_json(raw), used)
                    print(f"Gemini OK: {model}")
                    return data

                except ModelSkip as error:
                    print(f"  {model} o'tkazib yuborildi: {error}")
                    break

                except (ValueError, json.JSONDecodeError) as error:
                    print(f"  {model} noto'g'ri javob: {error}")

                except Exception as error:
                    print(f"  {model} xato: {str(error)[:250]}")
                    break  # keyingi modelga

        if round_no == 1:
            print("Barcha modellar muvaffaqiyatsiz. 25s kutib, qayta uriniladi...")
            time.sleep(25)

    raise RuntimeError("Gemini ssenariy yarata olmadi")


# ==========================================
# 4. PEXELS: VIDEO QIDIRISH
# ==========================================

search_cache = {}
used_video_ids = set()
video_counter = 0


def pexels_search(query, portrait=True):
    key = (query, portrait)
    if key in search_cache:
        return search_cache[key]

    params = {"query": query, "per_page": 15, "size": "medium"}
    if portrait:
        params["orientation"] = "portrait"

    response = http_retry(
        "GET",
        PEXELS_URL,
        headers={"Authorization": PEXELS_KEY},
        params=params,
        timeout=(10, 40),
    )

    if response.status_code in (401, 403):
        raise RuntimeError("Pexels API kaliti noto'g'ri (PEXELS_API_KEY)")

    response.raise_for_status()
    videos = response.json().get("videos", [])
    search_cache[key] = videos
    return videos


def pick_file(video):
    """Pexels video fayllaridan 1080 kenglikka eng yaqin mp4 ni tanlaydi."""
    best, best_score = None, None

    for f in video.get("video_files", []):
        if f.get("file_type") != "video/mp4" or not f.get("link"):
            continue

        width, height = f.get("width") or 0, f.get("height") or 0
        if width < 500 or width > 1600:
            continue

        score = abs(width - 1080) + (0 if height >= width else 600)
        if best_score is None or score < best_score:
            best, best_score = f, score

    return best["link"] if best else None


def download(url, path):
    response = http_retry("GET", url, tries=3, timeout=(10, 180))
    response.raise_for_status()

    if len(response.content) < 30000:
        raise RuntimeError("Yuklangan fayl juda kichik")

    with open(path, "wb") as f:
        f.write(response.content)


def candidate_queries(keywords, index, category):
    rotated = keywords[index % len(keywords):] + keywords[:index % len(keywords)]
    simple = [" ".join(k.split()[:2]) for k in rotated]
    return list(dict.fromkeys(rotated + simple + [category] + GENERIC_FALLBACKS))


def get_video(keywords, index, category):
    global video_counter

    for query in candidate_queries(keywords, index, category):
        for portrait in (True, False):
            try:
                hits = list(pexels_search(query, portrait))
            except RuntimeError as error:
                if "kaliti" in str(error):
                    raise
                print(f"  Pexels xato ({query}): {str(error)[:150]}")
                continue
            except requests.RequestException as error:
                print(f"  Pexels xato ({query}): {str(error)[:150]}")
                continue

            random.shuffle(hits)

            for hit in hits:
                if hit.get("id") in used_video_ids:
                    continue

                link = pick_file(hit)
                if not link:
                    continue

                video_counter += 1
                path = f"{OUT}/video_{video_counter}.mp4"

                try:
                    download(link, path)
                except Exception as error:
                    print(f"  Yuklab bo'lmadi: {str(error)[:150]}")
                    continue

                used_video_ids.add(hit.get("id"))
                return path

        print(f"  '{query}' bo'yicha mos kadr topilmadi, keyingisi...")

    raise RuntimeError(f"Pexels'da mos kadr topilmadi: {keywords}")


# ==========================================
# 5. OVOZ
# ==========================================

async def _tts(text, path):
    await edge_tts.Communicate(text, VOICE, rate=VOICE_RATE, pitch="+0Hz").save(path)


def synth_voice(text, path, tries=4):
    for attempt in range(1, tries + 1):
        try:
            asyncio.run(_tts(text, path))
            if os.path.exists(path) and os.path.getsize(path) > 1000:
                return
            raise RuntimeError("Audio fayl bo'sh")
        except Exception as error:
            print(f"  TTS xato ({attempt}/{tries}): {str(error)[:150]}")
            if attempt == tries:
                raise
            time.sleep(2 * attempt)


# ==========================================
# 6. VIDEO / MATN YORDAMCHILARI
# ==========================================

def fill_video(clip):
    """Kadrni 1080x1920 ni to'ldiradigan qilib kattalashtirib, markazdan kesadi."""
    scale = max(W / clip.w, H / clip.h)
    clip = clip.resized((math.ceil(clip.w * scale), math.ceil(clip.h * scale)))
    return clip.cropped(x_center=clip.w / 2, y_center=clip.h / 2, width=W, height=H)


def make_shot(path, duration):
    clip = VideoFileClip(path).without_audio()

    if clip.duration >= duration + 0.1:
        start = random.uniform(0, clip.duration - duration - 0.05)
        clip = clip.subclipped(start, start + duration)
    else:
        clip = clip.with_effects([vfx.Loop(duration=duration)])

    clip = fill_video(clip).with_duration(duration)

    if ZOOM:
        zoomed = clip.resized(lambda t: 1 + ZOOM_AMOUNT * t / duration)
        clip = CompositeVideoClip(
            [zoomed.with_position("center")], size=(W, H)
        ).with_duration(duration)

    return clip


def make_chunks(text, limit=22):
    chunks, current = [], ""

    for word in text.split():
        if current and len(current) + len(word) + 1 > limit:
            chunks.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()

    if current:
        chunks.append(current)

    return chunks


def make_text(text, font_size, duration, start, y, slide=True):
    clip = TextClip(
        font=FONT,
        text=text,
        font_size=font_size,
        color="#FFFFFF",
        stroke_color="#111111",
        stroke_width=6,
        method="caption",
        size=(W - 150, None),
        text_align="center",
        margin=(20, 30),
    )

    clip = clip.with_duration(duration)
    clip = clip.with_effects([vfx.FadeIn(0.16), vfx.FadeOut(0.12)])

    if slide:
        clip = clip.with_position(
            lambda t: ("center", y + int(30 * max(0.0, 1 - t / 0.2)))
        )
    else:
        clip = clip.with_position(("center", y))

    return clip.with_start(start)


def build_scene(index, sentence, total, data):
    text = sentence["text"]
    keywords = sentence["keywords"]

    audio_path = f"{OUT}/audio_{index}.mp3"
    synth_voice(text, audio_path)

    audio = AudioFileClip(audio_path)
    duration = audio.duration + 0.12

    shot_count = max(1, math.ceil(duration / SHOT_SEC))
    shot_duration = duration / shot_count

    shots = []
    for j in range(shot_count):
        path = get_video(keywords, j, data.get("category") or "nature")
        shots.append(make_shot(path, shot_duration))

    background = concatenate_videoclips(shots)
    layers = [background]

    # Subtitrlar (gapni bo'laklarga bo'lib, ovoz davomiyligiga mos)
    chunks = make_chunks(text)
    total_chars = sum(len(c) for c in chunks) or 1
    current = 0.0

    for chunk in chunks:
        chunk_duration = max(0.4, duration * len(chunk) / total_chars)
        layers.append(make_text(chunk, 78, chunk_duration, current, 1120))
        current += chunk_duration

    # Ochilish sarlavhasi (birinchi sahnada)
    if index == 0:
        layers.append(make_text(data["title"], 84, min(2.6, duration), 0, 280, slide=False))

    # Yakuniy savol (oxirgi sahnada)
    if index == total - 1:
        q_dur = min(3.5, duration)
        layers.append(make_text(data["ending_question"], 64, q_dur, duration - q_dur, 430, slide=False))

    scene = CompositeVideoClip(layers, size=(W, H)).with_duration(duration)
    return scene.with_audio(audio)


# ==========================================
# 7. TELEGRAM
# ==========================================

def tg_message(text):
    response = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": text[:4000]},
        timeout=60,
    )
    response.raise_for_status()


def tg_send_video(path, caption, duration, tries=3):
    last_error = None

    for attempt in range(1, tries + 1):
        try:
            with open(path, "rb") as video:
                response = requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendVideo",
                    data={
                        "chat_id": TG_CHAT,
                        "caption": caption[:950],
                        "supports_streaming": "true",
                        "width": W,
                        "height": H,
                        "duration": int(duration),
                    },
                    files={"video": ("daily_reels.mp4", video, "video/mp4")},
                    timeout=600,
                )

            response.raise_for_status()

            if not response.json().get("ok"):
                raise RuntimeError(response.text)
            return

        except Exception as error:
            last_error = error
            print(f"  Telegram video xato ({attempt}/{tries}): {str(error)[:200]}")
            time.sleep(5 * attempt)

    raise RuntimeError(f"Telegramga video yuborilmadi: {last_error}")


# ==========================================
# 8. ASOSIY JARAYON
# ==========================================

def main():
    used = load_used()
    category = random.choice(CATEGORIES)
    print("CATEGORY:", category)

    # --- Ssenariy ---
    data = generate_script(category, used)
    data.setdefault("category", category)
    print("TOPIC:", data["topic"])

    # --- Sahnalar ---
    parts = []
    total = len(data["sentences"])

    for i, sentence in enumerate(data["sentences"]):
        print(f"Sahna {i + 1}/{total} tayyorlanmoqda...")
        parts.append(build_scene(i, sentence, total, data))

    # --- Render ---
    print("Yakuniy video render qilinmoqda...")
    final = concatenate_videoclips(parts)
    final_duration = final.duration
    print(f"Davomiylik: {final_duration:.1f}s")

    final_path = f"{OUT}/final.mp4"
    final.write_videofile(
        final_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        threads=4,
        ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        logger="bar",
    )
    final.close()

    size_mb = os.path.getsize(final_path) / 1024 / 1024
    print(f"Fayl hajmi: {size_mb:.1f} MB")
    if size_mb > 49:
        raise RuntimeError("Video Telegram limitidan (50 MB) katta")

    # --- Telegram: video ---
    caption = (
        f"🎬 {data['title']}\n\n"
        f"{data['caption']}\n\n"
        f"💬 {data['ending_question']}"
    )
    tg_send_video(final_path, caption, final_duration)

    # --- Telegram: manbalar ---
    source_text = "📚 FACT-CHECK MANBALARI\n\n"
    for source in data["sources"]:
        source_text += f"• {source['name']}\n{source['url']}\n\n"
    source_text += "\n⚠️ Nashr qilishdan oldin manbalarni tekshiring."
    tg_message(source_text)

    # --- Mavzuni saqlash ---
    save_used(used, data["topic"])

    print("SUCCESS: Video Telegram'ga yuborildi!")
    print("TOPIC:", data["topic"])


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        traceback.print_exc()
        try:
            tg_message(f"❌ Reels bot xatosi:\n{str(error)[:500]}")
        except Exception:
            pass
        sys.exit(1)
