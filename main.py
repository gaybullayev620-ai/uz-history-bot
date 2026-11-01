import os, json, asyncio, time, hashlib, requests
import edge_tts
from moviepy import (VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip,
                     concatenate_videoclips, vfx)

E = os.environ
W, H = 1080, 1920
VOICE = "uz-UZ-SardorNeural"          # yoki uz-UZ-MadinaNeural
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
os.makedirs("out", exist_ok=True)

# 1) Ssenariy (Gemini)
used = json.load(open("used_topics.json"))
prompt = f"""O'zbekiston qadimgi yoki o'rta asrlar tarixi haqida ko'pchilik bilmaydigan yoki noto'g'ri o'ylaydigan
BITTA qiziq mavzu tanla. Faqat yaxshi hujjatlashtirilgan, ishonchli faktlar yoz. Aniq ishonching komil bo'lmagan
sana, raqam va iqtibos yozma. Hech narsa to'qima.
Bu mavzularni takrorlama: {used}
Qisqa video uchun ssenariy yoz (taxminan 45-55 soniya): 7-9 ta qisqa gap, o'zbek tilida (lotin).
Birinchi gap qiziqtiruvchi "hook" bo'lsin. Oxirgi gap izoh yozishga undasin.
Faqat JSON qaytar: {{"topic": "...", "caption": "izoh + 5 ta hashtag",
"sentences": [{{"text": "o'zbekcha gap", "keywords": "inglizcha 2-3 so'z, stok video qidirish uchun"}}]}}"""
r = requests.post(
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
    params={"key": E["GEMINI_API_KEY"]},
    json={"contents": [{"parts": [{"text": prompt}]}],
          "generationConfig": {"responseMimeType": "application/json"}}, timeout=120)
r.raise_for_status()
data = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
print("Mavzu:", data["topic"])

# 2) Pexels video
def pexels(q, i):
    res = requests.get("https://api.pexels.com/videos/search",
        headers={"Authorization": E["PEXELS_API_KEY"]},
        params={"query": q, "orientation": "portrait", "per_page": 5}, timeout=60).json()
    vids = res.get("videos") or requests.get("https://api.pexels.com/videos/search",
        headers={"Authorization": E["PEXELS_API_KEY"]},
        params={"query": "ancient architecture", "orientation": "portrait", "per_page": 5}, timeout=60).json()["videos"]
    files = sorted(vids[i % len(vids)]["video_files"], key=lambda f: f["width"])
    f = next((x for x in files if x["width"] >= 720), files[-1])
    path = f"out/v{i}.mp4"
    open(path, "wb").write(requests.get(f["link"], timeout=120).content)
    return path

# 3) Ovoz + klip har bir gap uchun
async def tts(text, path):
    await edge_tts.Communicate(text, VOICE).save(path)

clips = []
for i, s in enumerate(data["sentences"]):
    ap = f"out/a{i}.mp3"
    asyncio.run(tts(s["text"], ap))
    audio = AudioFileClip(ap)
    d = audio.duration + 0.25
    v = VideoFileClip(pexels(s["keywords"], i)).without_audio()
    v = v.resized(height=H) if v.w / v.h < W / H else v.resized(width=W)
    v = v.cropped(x_center=v.w / 2, y_center=v.h / 2, width=W, height=H)
    v = v.with_effects([vfx.Loop(duration=d)]).with_duration(d)
    txt = TextClip(font=FONT, text=s["text"], font_size=64, color="white",
                   stroke_color="black", stroke_width=4, method="caption",
                   size=(W - 120, 600), text_align="center").with_duration(d)
    txt = txt.with_position(("center", 1050))
    clips.append(CompositeVideoClip([v, txt], size=(W, H)).with_audio(audio))

final = concatenate_videoclips(clips)
final.write_videofile("out/final.mp4", fps=30, codec="libx264", audio_codec="aac", preset="veryfast")

if E.get("DRY_RUN", "true").lower() == "true":
    print("DRY_RUN: Instagramga joylanmadi. Videoni Artifacts'dan ko'ring.")
    raise SystemExit

# 4) Cloudinary (ochiq URL uchun)
ts = str(int(time.time()))
sig = hashlib.sha1(f"timestamp={ts}{E['CLOUDINARY_SECRET']}".encode()).hexdigest()
up = requests.post(f"https://api.cloudinary.com/v1_1/{E['CLOUDINARY_CLOUD']}/video/upload",
    data={"timestamp": ts, "api_key": E["CLOUDINARY_KEY"], "signature": sig},
    files={"file": open("out/final.mp4", "rb")}, timeout=600)
up.raise_for_status()
url = up.json()["secure_url"]

# 5) Instagram Reels
G = "https://graph.facebook.com/v21.0"
c = requests.post(f"{G}/{E['IG_USER_ID']}/media", data={
    "media_type": "REELS", "video_url": url,
    "caption": data["caption"], "access_token": E["IG_TOKEN"]}).json()
cid = c["id"]
for _ in range(40):
    st = requests.get(f"{G}/{cid}", params={"fields": "status_code", "access_token": E["IG_TOKEN"]}).json()
    if st.get("status_code") == "FINISHED": break
    if st.get("status_code") == "ERROR": raise SystemExit(f"IG xato: {st}")
    time.sleep(15)
p = requests.post(f"{G}/{E['IG_USER_ID']}/media_publish",
    data={"creation_id": cid, "access_token": E["IG_TOKEN"]}).json()
print("Joylandi:", p)

used.append(data["topic"])
json.dump(used, open("used_topics.json", "w"), ensure_ascii=False, indent=1)
