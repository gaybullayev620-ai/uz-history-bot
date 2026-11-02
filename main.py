
import os
import json
import math
import time
import random
import asyncio
import requests
import edge_tts

from moviepy import (
    VideoFileClip, AudioFileClip, TextClip,
    CompositeVideoClip, concatenate_videoclips,
    vfx
)

# ==========================================
# 1. SETTINGS
# ==========================================

E = os.environ

W, H = 1080, 1920
FPS = 30
VOICE = "uz-UZ-SardorNeural"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

OUT = "out"
USED_FILE = "used_topics.json"

os.makedirs(OUT, exist_ok=True)

GEMINI_KEY = E["GEMINI_API_KEY"]
PEXELS_KEY = E["PEXELS_API_KEY"]
TG_TOKEN = E["TELEGRAM_BOT_TOKEN"]
TG_CHAT = E["TELEGRAM_CHAT_ID"]

# ==========================================
# 2. TOPIC GENERATION
# ==========================================

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
    "Physics and strange natural phenomena"
]

if os.path.exists(USED_FILE):
    with open(USED_FILE, encoding="utf-8") as f:
        used = json.load(f)
else:
    used = []

category = random.choice(CATEGORIES)

prompt = f"""
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
- Provide 2-3 reliable source URLs.
- If uncertain, choose another topic.

2. RETENTION:
- First sentence must be a powerful hook.
- Start with a surprising question, contradiction
  or unexpected fact.
- Do not start with greetings or introductions.
- Every sentence must create curiosity for the next.
- Include a surprising reveal near the end.
- End with a specific question inviting comments.

3. SCRIPT:
- Uzbek language, Latin alphabet.
- 8-10 short, natural sentences.
- Target duration: 35-45 seconds.
- Conversational, cinematic storytelling.
- No unnecessary introductions or filler.

4. VISUALS:
For every sentence provide 3 concrete English
Pixabay search queries.
Queries must describe visible objects, locations,
people, environments or actions.
Each query must directly relate to that sentence.
Never use abstract concepts as visual searches.

5. EDITING:
Suggest a short, memorable opening title.
Suggest a final question for the screen.

6. ORIGINALITY:
Do not imitate an existing video's script.
Learn from effective storytelling techniques,
but create original content.

PREVIOUS TOPICS (DO NOT REPEAT):
{json.dumps(used[-100:], ensure_ascii=False)}

Return ONLY valid JSON:

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

def generate_script():
    models = [
                "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash"
    ]

    for model in models:
        try:
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": GEMINI_KEY},
                json={
                    "contents": [{
                        "parts": [{"text": prompt}]
                    }],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.9
                    }
                },
                timeout=180
            )

            if not response.ok:
                print(model, response.status_code)
                continue

            raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(raw)

            if len(result["sentences"]) < 6:
                continue

            if not result.get("sources"):
                continue

            return result

        except Exception as error:
            print("Gemini error:", error)

    raise RuntimeError("Gemini ssenariy yarata olmadi")


data = generate_script()

print("TOPIC:", data["topic"])

# ==========================================
# 3. PIXABAY VIDEO SEARCH
# ==========================================

cache = {}
used_links = set()
video_counter = 0


def search_pixabay(query):
    if query not in cache:
        response = requests.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": PEXELS_KEY,
                "q": query,
                "per_page": 20,
                "safesearch": "true"
            },
            timeout=60
        )
        response.raise_for_status()
        cache[query] = response.json().get("hits", [])

    return cache[query]


def get_video(query):
    global video_counter

    hits = search_pixabay(query)

    random.shuffle(hits)

    for hit in hits:
        videos = hit.get("videos", {})

        video = (
            videos.get("medium")
            or videos.get("small")
            or videos.get("large")
        )

        if not video:
            continue

        url = video["url"]

        if url in used_links:
            continue

        response = requests.get(url, timeout=180)
        response.raise_for_status()

        if len(response.content) < 30000:
            continue

        video_counter += 1
        path = f"{OUT}/video_{video_counter}.mp4"

        with open(path, "wb") as f:
            f.write(response.content)

        used_links.add(url)
        return path

    raise RuntimeError(
        f"Pixabay'da mos kadr topilmadi: {query}"
    )


# ==========================================
# 4. VIDEO PROCESSING
# ==========================================

def fill_video(clip):
    scale = max(W / clip.w, H / clip.h)

    clip = clip.resized(
        (math.ceil(clip.w * scale),
         math.ceil(clip.h * scale))
    )

    return clip.cropped(
        x_center=clip.w / 2,
        y_center=clip.h / 2,
        width=W,
        height=H
    )


def make_chunks(text, limit=24):
    words = text.split()
    result = []
    current = ""

    for word in words:
        if len(current) + len(word) + 1 > limit:
            if current:
                result.append(current)
            current = word
        else:
            current = (current + " " + word).strip()

    if current:
        result.append(current)

    return result


async def make_voice(text, path):
    tts = edge_tts.Communicate(
        text,
        VOICE,
        rate="+7%",
        pitch="+0Hz"
    )
    await tts.save(path)


# ==========================================
# 5. DYNAMIC TEXT
# ==========================================

def animated_subtitle(text, duration):
    clip = TextClip(
        font=FONT,
        text=text,
        font_size=78,
        color="#FFFFFF",
        stroke_color="#111111",
        stroke_width=6,
        method="caption",
        size=(W - 150, None),
        text_align="center"
    )

    clip = clip.with_duration(duration)

    clip = clip.with_effects([
        vfx.FadeIn(0.16),
        vfx.FadeOut(0.12)
    ])

    clip = clip.with_position(
        lambda t: (
            "center",
            1120 - int(25 * max(0, 1 - t / 0.2))
        )
    )

    return clip


# ==========================================
# 6. CREATE SENTENCE SCENES
# ==========================================

parts = []

for i, sentence in enumerate(data["sentences"]):

    print(f"Creating scene {i+1}...")

    audio_path = f"{OUT}/audio_{i}.mp3"

    asyncio.run(
        make_voice(sentence["text"], audio_path)
    )

    audio = AudioFileClip(audio_path)
    duration = audio.duration + 0.12

    keywords = sentence["keywords"]

    if not isinstance(keywords, list) or not keywords:
        raise RuntimeError("Visual keywords mavjud emas")

    shot_count = max(1, math.ceil(duration / 2.5))
    shot_duration = duration / shot_count

    shots = []

    for j in range(shot_count):

        query = keywords[j % len(keywords)]
        video_path = get_video(query)

        video = VideoFileClip(video_path).without_audio()

        if video.duration > shot_duration + 0.3:
            start = random.uniform(
                0,
                video.duration - shot_duration
            )

            video = video.subclipped(
                start,
                start + shot_duration
            )
        else:
            video = video.with_effects([
                vfx.Loop(duration=shot_duration)
            ])

        video = fill_video(video)
        video = video.with_duration(shot_duration)

        shots.append(video)

    background = concatenate_videoclips(shots)

    # Dynamic subtitles
    text_layers = [background]
    chunks = make_chunks(sentence["text"])

    total_chars = sum(len(c) for c in chunks)
    current_time = 0

    for chunk in chunks:
        chunk_duration = (
            duration * len(chunk) / total_chars
        )

        subtitle = animated_subtitle(
            chunk,
            chunk_duration
        )

        subtitle = subtitle.with_start(current_time)

        text_layers.append(subtitle)
        current_time += chunk_duration

    scene = CompositeVideoClip(
        text_layers,
        size=(W, H)
    ).with_duration(duration)

    scene = scene.with_audio(audio)

    parts.append(scene)


# ==========================================
# 7. FINAL VIDEO
# ==========================================

print("Rendering final video...")

final = concatenate_videoclips(parts)

final.write_videofile(
    f"{OUT}/final.mp4",
    fps=FPS,
    codec="libx264",
    audio_codec="aac",
    preset="medium",
    threads=4,
    logger="bar"
)

final.close()

# ==========================================
# 8. SEND TO TELEGRAM
# ==========================================

video_path = f"{OUT}/final.mp4"

caption = (
    f"🎬 {data['title']}\n\n"
    f"{data['caption']}\n\n"
    f"💬 {data['ending_question']}"
)

# Telegram video caption limit
video_caption = caption[:950]

with open(video_path, "rb") as video:
    response = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendVideo",
        data={
            "chat_id": TG_CHAT,
            "caption": video_caption,
            "supports_streaming": "true"
        },
        files={
            "video": (
                "daily_reels.mp4",
                video,
                "video/mp4"
            )
        },
        timeout=600
    )

response.raise_for_status()

if not response.json().get("ok"):
    raise RuntimeError(response.text)

# Send sources separately for manual fact-checking
source_text = "📚 FACT-CHECK MANBALARI\n\n"

for source in data["sources"]:
    source_text += (
        f"• {source['name']}\n"
        f"{source['url']}\n\n"
    )

source_text += (
    "\n⚠️ Nashr qilishdan oldin manbalarni tekshiring."
)

requests.post(
    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
    json={
        "chat_id": TG_CHAT,
        "text": source_text
    },
    timeout=60
).raise_for_status()

# Save topic history
used.append(data["topic"])

with open(USED_FILE, "w", encoding="utf-8") as f:
    json.dump(
        used[-200:],
        f,
        ensure_ascii=False,
        indent=2
    )

print("SUCCESS: Video Telegram'ga yuborildi!")
print("TOPIC:", data["topic"])
