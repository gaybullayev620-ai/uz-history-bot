# O'zbekiston tarixi - avtomatik Reels

## Sozlash
1. GitHub'da yangi repo oching, bu fayllarni yuklang.
2. Settings > Secrets and variables > Actions > **Secrets**:
   GEMINI_API_KEY, PEXELS_API_KEY, CLOUDINARY_CLOUD, CLOUDINARY_KEY,
   CLOUDINARY_SECRET, IG_USER_ID, IG_TOKEN
3. Avval DRY_RUN=true (standart): Actions > Run workflow > video yasaladi, lekin
   Instagramga joylanmaydi. Artifacts'dan "reel"ni yuklab, faktlarni tekshiring.
4. Yoqimli bo'lsa: Settings > Variables > DRY_RUN = false.

## Kalitlar
- Gemini: aistudio.google.com (bepul)
- Pexels: pexels.com/api (bepul)
- Cloudinary: cloudinary.com (bepul tarif)
- Instagram: Business/Creator akkaunt + Facebook Page + Meta developer app,
  ruxsatlar: instagram_content_publish. IG_TOKEN - uzoq muddatli token.
