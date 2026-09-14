from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / 'app/main.py'
TELEGRAM = ROOT / 'app/telegram.py'
AI = ROOT / 'app/ai.py'


def _extract_function(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return node


def _exec_main_helpers():
    tree = ast.parse(MAIN.read_text(encoding='utf-8'))
    wanted = {'_normalize_chain_digits', '_chain_kind_from_label', 'extract_chain_items', 'render_chain_output'}
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in wanted]
    const_nodes = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {'CHAIN_KIND_LABELS','CHAIN_DAY_RE','CHAIN_TOOL_RE'} for t in n.targets)]
    ns = {'re': __import__('re'), 'html': __import__('html'), 'settings': type('S', (), {'bot_username': 'mohtavachi_ai_bot'})()}
    exec(compile(ast.Module(body=const_nodes + nodes, type_ignores=[]), str(MAIN), 'exec'), ns)
    return ns


def _exec_telegram_actions():
    node = _extract_function(TELEGRAM, 'inline_generation_actions')
    ns = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(TELEGRAM), 'exec'), ns)
    return ns['inline_generation_actions']


def test_weekly_pack_sections_become_clickable_and_extractable():
    ns = _exec_main_helpers()
    output = (
        'روز 1: پست — آگاهی از مشکل\nمتن پست روز اول.\n\n'
        'روز 2: Reel — ادامه داستان\nسناریوی روز دوم.\n\n'
        'روز 3: استوری\nسه اسلاید تعامل.'
    )
    items = ns['extract_chain_items'](output, 'pack')
    assert [x['kind'] for x in items] == ['post', 'reel', 'story']
    assert items[1]['source'] == 'ادامه داستان\nسناریوی روز دوم.'
    rendered = ns['render_chain_output'](output, 42, 'pack')
    assert 'https://t.me/mohtavachi_ai_bot?start=chain_42_0_0' in rendered
    assert 'https://t.me/mohtavachi_ai_bot?start=chain_42_1_0' in rendered
    assert '<a href=' in rendered


def test_campaign_tool_headings_become_clickable_and_extractable():
    ns = _exec_main_helpers()
    output = 'پست: هوک کمپین\nمتن پست.\n\nسناریوی Reel: روایت و CTA\nبدنه سناریو.\n\nمعرفی محصول: محصول\nجزئیات.'
    items = ns['extract_chain_items'](output, 'campaign')
    assert [x['kind'] for x in items] == ['post', 'reel', 'product']
    assert items[0]['source'] == 'هوک کمپین\nمتن پست.'
    rendered = ns['render_chain_output'](output, 7, 'campaign', premium=True)
    assert 'chain_7_0_1' in rendered
    assert 'chain_7_1_1' in rendered
    assert rendered.endswith('⭐')


def test_pack_and_campaign_no_attached_action_panel():
    actions = _exec_telegram_actions()
    for kind in ('pack', 'campaign'):
        rows = actions(10, kind)['inline_keyboard']
        texts = [b['text'] for row in rows for b in row]
        assert texts == ['👍', '👎']


def test_admin_back_is_state_aware_and_does_not_fall_through_to_user_home():
    main = MAIN.read_text(encoding='utf-8')
    assert 'async def handle_admin_back(' in main
    assert 'if is_admin(user) and state and state.get("step", "").startswith("admin_"):' in main
    for marker in (
        'step == "admin_broadcast_message"',
        'step == "admin_broadcast_confirm"',
        'step == "admin_coupon_value"',
        'step == "admin_coupon_expiry"',
        'step == "admin_reply"',
    ):
        assert marker in main


def test_studio_plus_menu_includes_weekly_pack_and_maps_to_pack():
    telegram = TELEGRAM.read_text(encoding='utf-8')
    main = MAIN.read_text(encoding='utf-8')
    assert '"📦 بسته هفتگی Premium"' in telegram
    assert '"📦 بسته هفتگی Premium":"pack"' in main
    assert 'PACK_REQUEST_MESSAGE = "برای بسته ۷ روزه، محصول، موضوع یا هدف اصلی را بنویس."' in main
    assert '"pack": PACK_REQUEST_MESSAGE' in main
    assert 'await safe_send(chat_id, tool_request_message(kind) + " ⭐", back_home_menu())' in main
