# Pricing & Revenue Math

## Pricing
- Free: 3 generations/day + 15 starter credits
- Starter: 299,000 تومان/month — 120 credits
- Pro: 599,000 تومان/month — 350 credits + 1 free image/day
- Studio: 1,190,000 تومان/month — 1,000 credits + 2 free images/day
- Image generation outside the daily free allowance costs 10 credits.

## مثال مسیر 100M+
40 Starter = 11.96M
115 Pro = 68.885M
25 Studio = 29.75M
Total = 110.595M تومان/month

=> 180 paying accounts.

این یک هدف عملیاتی است، نه تضمین conversion.

## حساسیت
- اگر ARPU واقعی 350k باشد: ~286 paying accounts
- اگر ARPU واقعی 500k باشد: 200 paying accounts
- اگر ARPU واقعی 700k باشد: ~143 paying accounts

اولویت باید افزایش ARPU با Content Pack، Brand Voice و Studio باشد؛ نه فقط افزایش user count.

## Studio+

Studio+ is the premium tier. Current defaults:
- Price: 2,490,000 toman/month
- Credits: 1,600
- Free images: 2/day
- Text model: `gpt-5.6-terra`
- Image model: `gpt-image-1-mini`
- Text generation: routed to `STUDIO_PLUS_AI_MODEL`
- Image generation: routed to `STUDIO_PLUS_IMAGE_MODEL`

Studio+ credit costs:
- Caption: 4
- Post: 7
- Story: 7
- Reel: 8
- Product: 7
- Ideas: 3
- Rewrite: 6
- Brief: 4
- Campaign: 15
- Pack: 15
- Image after the daily free allowance: 5

The premium model names are configurable through environment variables so the deployment can use models actually supported by the selected AI gateway.
