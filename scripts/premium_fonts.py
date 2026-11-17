"""Extra OFL font metadata; no downloads or system-font installation at runtime."""

PREMIUM_FONT_CHOICES = [
    {'value': 'Inter', 'label': 'Inter · 精准现代 / 英文'},
    {'value': 'Manrope', 'label': 'Manrope · 高级几何 / 英文'},
    {'value': 'Space Grotesk', 'label': 'Space Grotesk · 数码科技 / 英文'},
    {'value': 'Bebas Neue', 'label': 'Bebas Neue · 海报窄体 / 英文'},
    {'value': 'Outfit', 'label': 'Outfit · 圆润时尚 / 英文'},
    {'value': 'ZCOOL QingKe HuangYou', 'label': '站酷庆科黄油体 · 醒目圆角 / 中文'},
    {'value': 'ZCOOL XiaoWei', 'label': '站酷小薇体 · 精致宋黑 / 中文'},
    {'value': 'Ma Shan Zheng', 'label': '马善政楷书 · 人文笔触 / 中文'},
]

# Paths are relative to the project root. Single-weight display fonts deliberately
# use the same source twice; libass may synthesize bold when explicitly requested.
PRIVATE_FONT_FILES = {
    'inter': ('assets/fonts/Inter/Inter-Regular.ttf', 'assets/fonts/Inter/Inter-Bold.ttf'),
    'manrope': ('assets/fonts/Manrope/Manrope-Regular.ttf', 'assets/fonts/Manrope/Manrope-Bold.ttf'),
    'space grotesk': ('assets/fonts/SpaceGrotesk/SpaceGrotesk-Regular.ttf', 'assets/fonts/SpaceGrotesk/SpaceGrotesk-Bold.ttf'),
    'bebas neue': ('assets/fonts/BebasNeue/BebasNeue-Regular.ttf', 'assets/fonts/BebasNeue/BebasNeue-Regular.ttf'),
    'outfit': ('assets/fonts/Outfit/Outfit-Regular.ttf', 'assets/fonts/Outfit/Outfit-Bold.ttf'),
    'zcool qingke huangyou': ('assets/fonts/ZCOOLQingKeHuangYou/ZCOOLQingKeHuangYou-Regular.ttf', 'assets/fonts/ZCOOLQingKeHuangYou/ZCOOLQingKeHuangYou-Regular.ttf'),
    'zcool xiaowei': ('assets/fonts/ZCOOLXiaoWei/ZCOOLXiaoWei-Regular.ttf', 'assets/fonts/ZCOOLXiaoWei/ZCOOLXiaoWei-Regular.ttf'),
    'ma shan zheng': ('assets/fonts/MaShanZheng/MaShanZheng-Regular.ttf', 'assets/fonts/MaShanZheng/MaShanZheng-Regular.ttf'),
}

# Opt-in styling only: never apply a preset to saved preferences automatically.
FONT_PRESETS = [
    {'id': 'modern', 'label': '现代商业', 'description': '干净有秩序，适合通用商品。',
     'style': {'brand_font': 'Manrope', 'title_font': 'Manrope', 'description_font': 'Source Han Sans CN', 'brand_bold': True, 'title_bold': True, 'description_bold': False}},
    {'id': 'tech', 'label': '数码科技', 'description': '醒目的型号与清晰的中文类别。',
     'style': {'brand_font': 'Space Grotesk', 'title_font': 'Space Grotesk', 'description_font': 'Source Han Sans CN', 'brand_bold': True, 'title_bold': True, 'description_bold': False}},
    {'id': 'poster', 'label': '海报大片', 'description': '窄体英文与沉稳中文，适合音频器材。',
     'style': {'brand_font': 'Bebas Neue', 'title_font': 'Bebas Neue', 'description_font': 'Source Han Serif CN', 'brand_bold': False, 'title_bold': False, 'description_bold': False}},
    {'id': 'soft', 'label': '轻盈生活', 'description': '圆润英文配精致中文，适合生活方式商品。',
     'style': {'brand_font': 'Outfit', 'title_font': 'Outfit', 'description_font': 'ZCOOL XiaoWei', 'brand_bold': True, 'title_bold': True, 'description_bold': False}},
    {'id': 'playful', 'label': '活力标题', 'description': '有识别度的中文标题，适合短标题和封面。',
     'style': {'brand_font': 'Inter', 'title_font': 'ZCOOL QingKe HuangYou', 'description_font': 'Source Han Sans CN', 'brand_bold': True, 'title_bold': False, 'description_bold': False}},
]
