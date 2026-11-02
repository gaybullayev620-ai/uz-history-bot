"""
Instagram'ga Reels joylash (Upload-Post API orqali).

Kerakli GitHub Secrets:
- UPLOAD_POST_API_KEY
- UPLOAD_POST_USER  (Upload-Post profil nomi, masalan: default)
"""

import os
import time
import uuid

import requests

API_BASE = "https://api.upload-post.com/api"

UPLOAD_POST_KEY = os.getenv("UPLOAD_POST_API_KEY")
UPLOAD_POST_USER = os.getenv("UPLOAD_POST_USER") or "default"

HASHTAGS = "#faktlar #bilim #qiziqarli #reels"

UPLOAD_TRIES = 3


def make_caption(data):
    title = str(data.get("title") or data.get("topic") or "").strip()
    hook = str(data.get("hook") or "").strip()

    parts = [p for p in (title, hook) if p]
    caption = "\n\n".join(parts)

    if caption:
        caption += "\n\n"

    caption += HASHTAGS

    # Instagram izohi 2200 belgidan oshmasligi kerak
    return caption[:2000]


def _headers():
    return {"Authorization": f"Apikey {UPLOAD_POST_KEY}"}


def _upload_exists(request_id):
    """Shu request_id bilan yuklash Upload-Post'da allaqachon bormi?"""
    try:
        response = requests.get(
            f"{API_BASE}/uploadposts/status",
            headers=_headers(),
            params={"request_id": request_id},
            timeout=(20, 60),
        )

        if not response.ok:
            return False

        info = response.json()
        return bool(info.get("status") or info.get("results"))

    except Exception:
        return False


def _wait_for_result(request_id, timeout=900):
    """Upload-Post statusini kutadi. Instagram muvaffaqiyatli bo'lsa True."""
    deadline = time.time() + timeout

    while time.time() < deadline:
        time.sleep(10)

        try:
            response = requests.get(
                f"{API_BASE}/uploadposts/status",
                headers=_headers(),
                params={"request_id": request_id},
                timeout=(20, 60),
            )
            info = response.json()
        except Exception as error:
            print(f"Status o'qilmadi, qayta urinamiz: {error}")
            continue

        status = str(info.get("status", "")).lower()
        results = info.get("results") or []

        print(f"Holat: {status or response.status_code}")

        finished = (
            status in ("completed", "failed", "error")
            or (
                results
                and info.get("total")
                and info.get("completed") == info.get("total")
            )
        )

        if not finished:
            continue

        for item in results:
            if str(item.get("platform", "")).lower() == "instagram":
                if item.get("success"):
                    print("Instagram: joylandi!")
                    return True

                raise RuntimeError(
                    f"Instagram xatosi: {item.get('message') or item}"
                )

        raise RuntimeError(
            f"Instagram natijasi topilmadi: {str(info)[:400]}"
        )

    raise RuntimeError("Instagram natijasini kutish vaqti tugadi.")


def _send_video(video_path, form, request_id):
    with open(video_path, "rb") as video_file:
        return requests.post(
            f"{API_BASE}/upload",
            headers={
                **_headers(),
                "X-Request-Id": request_id,
            },
            data=form,
            files={"video": ("reels.mp4", video_file, "video/mp4")},
            timeout=(30, 300),
        )


def post_to_instagram(video_path, data):
    if not UPLOAD_POST_KEY:
        print(
            "UPLOAD_POST_API_KEY yo'q. "
            "Instagram'ga joylash o'tkazib yuborildi."
        )
        return False

    if not os.path.exists(video_path):
        raise RuntimeError(f"Video topilmadi: {video_path}")

    caption = make_caption(data)
    request_id = str(uuid.uuid4())

    size_mb = os.path.getsize(video_path) / (1024 * 1024)

    print("\nInstagram'ga yuborilmoqda...")
    print(f"Profil: {UPLOAD_POST_USER}")
    print(f"Video hajmi: {size_mb:.1f} MB")

    form = {
        "user": UPLOAD_POST_USER,
        "title": caption,
        "instagram_title": caption,
        "platform[]": "instagram",
        "async_upload": "true",
        "request_id": request_id,
    }

    last_error = None

    for attempt in range(1, UPLOAD_TRIES + 1):
        try:
            print(f"Yuklash urinishi {attempt}/{UPLOAD_TRIES}...")

            response = _send_video(video_path, form, request_id)

            if response.ok:
                print("Video yuborildi.")
                last_error = None
                break

            # 5xx xatolarda qayta urinamiz, boshqalarida to'xtaymiz
            message = (
                f"Upload-Post HTTP {response.status_code}: "
                f"{response.text[:400]}"
            )

            if response.status_code in (500, 502, 503, 504):
                last_error = RuntimeError(message)
                print(message)
            else:
                raise RuntimeError(message)

        except requests.RequestException as error:
            last_error = error
            print(f"Yuklashda uzilish: {error}")

            # Ehtimol server videoni qabul qilgan. Ikki marta
            # joylamaslik uchun avval holatni tekshiramiz.
            time.sleep(10)

            if _upload_exists(request_id):
                print("Server videoni qabul qilgan. Natijani kutamiz.")
                last_error = None
                break

        if attempt < UPLOAD_TRIES:
            wait = 15 * attempt
            print(f"{wait}s kutib, qayta urinamiz...")
            time.sleep(wait)

    if last_error is not None:
        raise RuntimeError(
            f"Instagram'ga yuklab bo'lmadi: {last_error}"
        )

    print("Instagram natijasi kutilmoqda...")

    return _wait_for_result(request_id)
