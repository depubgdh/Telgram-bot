# Telegram bot + Mini App + Admin

## Ishga tushirish
1. Cloud baza oching (Supabase yoki Neon, bepul) va `DATABASE_URL` ni oling: `postgresql+asyncpg://...`
2. `.env.example` ni `.env` ga nusxalang va to'ldiring.
3. `pip install -r requirements.txt`
4. `python -m app.main` (bot + Mini App bitta jarayonda).
5. Mini App uchun HTTPS kerak: Render / Railway / VPS ga joylang, `https://api.render.com/deploy/srv-dbhqbqqjnfac73alkie0?key=ewiC0JmdZbE` ga manzilni yozing,
   BotFather → `/mybots` → Bot Settings → Menu Button yoki Mini App ga shu manzilni ulang.
6. Bot kanalda **admin** bo'lishi kerak (obunani tekshirish uchun).

## Admin buyruqlari (faqat ADMIN_IDS)
- Kelgan so'rovlar (chek / yechish) ostidagi ✅ / ❌ tugmalari bilan tasdiqlanadi
- `/set stars_uzs 230` — kurs va komissiyalarni qo'lda belgilash (onlayn kurs ishlatilmaydi)
- Rasm yuboring, caption: `/addnft Nomi | narx_so'm | narx_stars` — NFT qo'shish
- `/user <tg_id>` — foydalanuvchi va tarixi · `/ban <tg_id>` · `/credit <tg_id> UZS 50000` · `/stats`

## Hozircha qo'lda bajariladigan qismlar (ochiq aytaman)
- **TON avtomatik qabul qilish** yozilmagan: bot manzil va MEMO ko'rsatadi, to'lovni siz tekshirib `/credit` qilasiz.
  Keyingi qadamda toncenter API orqali avtomatlashtiramiz.
- **NFT orqali to'ldirish** va **NFT/Stars/UC yechish** — adminga so'rov boradi, yuborishni o'zingiz qilasiz.
- Admin konsoli hozir bot ichida (tugmalar + buyruqlar). Alohida veb-panel keyingi bosqichda qo'shiladi.
- Guruh aksiyasi va 3% ish taklifi — bu bot kodi emas, strategiya (qo'lda).
