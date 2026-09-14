from pathlib import Path

MAIN = Path("app/main.py").read_text(encoding="utf-8")

EXPECTED = {
    "/home": "خانه",
    "/content": "✍️ تولید محتوا",
    "/ideas": "💡 ایده محتوا",
    "/campaign": "🚀 کمپین",
    "/image": "🖼️ تولید عکس",
    "/profile": "👤 پروفایل برند",
    "/history": "🕘 اخیر",
    "/favorites": "⭐ ذخیره‌شده‌ها",
    "/credits": "📊 وضعیت من",
    "/referral": "🎁 دعوت دوستان",
    "/support": "🆘 پشتیبانی",
}

for command, destination in EXPECTED.items():
    assert f'"{command}": "{destination}"' in MAIN
assert 'if command == "/rewrite":' in MAIN
print("Command routing static checks: PASS")
