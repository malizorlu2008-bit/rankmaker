"""Headless port of app.js's Canvas compositing (drawTitle, drawList,
drawVideoFrame). Layout ratios/constants kept identical to the browser
version so output matches the existing tool's look.
"""
import os
from PIL import Image, ImageDraw, ImageFont

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
FONT_DIR = os.path.join(ASSETS_DIR, "fonts")

FONT_FILES = {
    400: os.path.join(FONT_DIR, "Poppins-Regular.ttf"),
    600: os.path.join(FONT_DIR, "Poppins-SemiBold.ttf"),
    700: os.path.join(FONT_DIR, "Poppins-Bold.ttf"),
    800: os.path.join(FONT_DIR, "Poppins-ExtraBold.ttf"),
    900: os.path.join(FONT_DIR, "Poppins-Black.ttf"),
}

EMOJI_FONT_PATH = "/System/Library/Fonts/Apple Color Emoji.ttc"
EMOJI_STRIKES = [160, 96, 64, 48, 40, 32, 20]

CANVAS_W = 1080
CANVAS_H = 1920
RANK_COLORS = ["#ffd400", "#e8e8f0", "#ff9a3c"]
RANK_COLOR_DEFAULT = "#ffffff"
# 0.20'den 0.16'ya dusuruldu (2026-08-09): 0.20 = 384px'lik siyah bant, iki
# satir baslik oraya ~170px yer kapliyordu, gerisi bos siyahti. 0.16 klibin
# gorunen alanini ~%5 buyutuyor. Tarayici surumundeki (app.js) ayni sabit
# bilerek degistirilmedi — yuklenen videolari ureten yol bu dosya.
TITLE_BAR_RATIO = 0.16
STROKE_COLOR = (0, 0, 0, 230)

_FONT_CACHE = {}
_EMOJI_CACHE = {}
_EMOJI_AVAILABLE = os.path.exists(EMOJI_FONT_PATH)


def get_font(weight, size):
    size = max(1, round(size))
    key = (weight, size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(FONT_FILES[weight], size)
    return _FONT_CACHE[key]


def fit_font_size(draw, text, max_width, start_size, weight):
    size = start_size
    while size > 24:
        font = get_font(weight, size)
        if draw.textlength(text, font=font) <= max_width:
            break
        size -= 2
    return size


def _render_emoji_glyph(emoji_char, target_size):
    best_strike = min(EMOJI_STRIKES, key=lambda s: abs(s - target_size))
    key = (emoji_char, best_strike)
    if key not in _EMOJI_CACHE:
        font = ImageFont.truetype(EMOJI_FONT_PATH, best_strike)
        canvas_size = best_strike * 2
        layer = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.text((canvas_size // 2, canvas_size // 2), emoji_char, font=font, embedded_color=True, anchor="mm")
        bbox = layer.getbbox()
        if bbox:
            layer = layer.crop(bbox)
        _EMOJI_CACHE[key] = layer
    glyph = _EMOJI_CACHE[key]
    if not glyph.height:
        return glyph
    scale = target_size / glyph.height
    new_w = max(1, round(glyph.width * scale))
    new_h = max(1, round(glyph.height * scale))
    return glyph.resize((new_w, new_h), Image.LANCZOS)


def paste_emoji(img, emoji_char, x, y, target_size):
    """x = left edge, y = vertical middle (matches text anchor='lm' convention)."""
    if not _EMOJI_AVAILABLE:
        return
    try:
        glyph = _render_emoji_glyph(emoji_char, target_size)
    except Exception:
        return
    px, py = round(x), round(y - glyph.height / 2)
    img.alpha_composite(glyph, (px, py))


def draw_stroked_segments(draw, segments, center_x, y, font_size, weight):
    segments = [s for s in segments if s.get("text")]
    if not segments:
        return
    font = get_font(weight, font_size)
    full_text = " ".join(s["text"] for s in segments)
    total_width = draw.textlength(full_text, font=font)
    x = center_x - total_width / 2
    stroke_w = max(1, round(font_size * 0.11))
    for i, seg in enumerate(segments):
        word = seg["text"] + (" " if i < len(segments) - 1 else "")
        draw.text((x, y), word, font=font, fill=seg["color"], stroke_width=stroke_w, stroke_fill=STROKE_COLOR, anchor="lm")
        x += draw.textlength(word, font=font)


def draw_title(draw, title_cfg):
    bar_height = CANVAS_H * TITLE_BAR_RATIO
    draw.rectangle([0, 0, CANVAS_W, bar_height], fill=(0, 0, 0, 255))

    max_width = CANVAS_W * 0.9
    line1 = [
        {"text": title_cfg.get("prefix", ""), "color": title_cfg.get("prefixColor", "#ffffff")},
        {"text": title_cfg.get("adjective", ""), "color": title_cfg.get("adjectiveColor", "#ff3b30")},
    ]
    line2 = [
        {"text": title_cfg.get("topic", ""), "color": title_cfg.get("topicColor", "#ffd400")},
        {"text": title_cfg.get("suffix", ""), "color": title_cfg.get("suffixColor", "#ffffff")},
    ]
    line1 = [s for s in line1 if s["text"]]
    line2 = [s for s in line2 if s["text"]]
    line1_text = " ".join(s["text"] for s in line1)
    line2_text = " ".join(s["text"] for s in line2)

    size1 = fit_font_size(draw, line1_text, max_width, 78, 800)
    size2 = fit_font_size(draw, line2_text, max_width, 78, 800)
    font_size = min(size1, size2)

    center_x = CANVAS_W / 2
    line_gap = font_size * 1.15

    # Merak satiri ("(Wait for #1)" gibi). Milyonlarca izlenen ayni format
    # kanallari bunu basligin icine koyuyor — "( The last one is hilarious )",
    # "(Wait For Last)" — seyirciyi sonuna kadar tutmak icin. Varsa iki satirlik
    # basligi yukari kaydirip altina sigdiriyoruz.
    hook_line = (title_cfg.get("hookLine") or "").strip()
    hook_size = font_size * 0.5 if hook_line else 0
    # Baslik fontuna oranli bosluk: 2. satirin alt uzantilarindan (g, y, p)
    # net ayrilmasi icin. Daha kucuk bir oran denendi ve yazilar cakisti.
    hook_gap = font_size * 1.25 if hook_line else 0

    # Iki baslik satiri + merak satiri tek blok olarak dikeyde ortalanir.
    block_h = line_gap + hook_gap
    top = (bar_height - block_h) / 2
    line1_y = top + font_size * 0.5
    line2_y = line1_y + line_gap
    draw_stroked_segments(draw, line1, center_x, line1_y, font_size, 800)
    draw_stroked_segments(draw, line2, center_x, line2_y, font_size, 800)
    if hook_line:
        draw_stroked_segments(
            draw,
            [{"text": hook_line, "color": title_cfg.get("hookLineColor", "#9fe8ff")}],
            center_x, line2_y + hook_gap, hook_size, 600,
        )
    return bar_height


def draw_video_frame(img, draw, bar_height, video_frame_pil):
    area_y = bar_height
    area_h = CANVAS_H - bar_height
    area_w = CANVAS_W
    draw.rectangle([0, area_y, area_w, CANVAS_H], fill=(17, 17, 20, 255))
    if video_frame_pil is None:
        return
    vw, vh = video_frame_pil.size
    if not vw or not vh:
        return
    scale = max(area_w / vw, area_h / vh)
    dw, dh = vw * scale, vh * scale
    resized = video_frame_pil.resize((max(1, round(dw)), max(1, round(dh))))
    dx = (area_w - dw) / 2
    dy = area_y + (area_h - dh) / 2
    img.paste(resized, (round(dx), round(dy)))


def draw_list(img, draw, items, current_index, is_playing, bar_height,
              acilanlar=None):
    padding_left = CANVAS_W * 0.07
    start_y = bar_height + 90
    line_height = min(120, (CANVAS_H - bar_height - 120) / max(len(items), 1))
    font_size = max(38, min(58, line_height * 0.55))

    for index, item in enumerate(items):
        rank = index + 1
        y = start_y + index * line_height
        # acilanlar: o ana kadar OYNATILMIS siralarin kumesi. Eskiden kural
        # "index >= current_index" idi ve geri sayim sirasina (5,4,3,2,1) gore
        # dogru calisiyordu. Oynatma sirasi 2-4-3-5-1 olunca ayni kural 2'yi
        # oynatirken 3,4,5'i de aciyordu — kullanici bildirdi (2026-08-16).
        if acilanlar is not None:
            revealed = index in acilanlar or not is_playing
        else:
            revealed = index >= current_index or not is_playing
        rank_color = RANK_COLORS[index] if index < 3 else RANK_COLOR_DEFAULT

        number_text = f"{rank}."
        font_num = get_font(900, font_size)
        stroke_w = max(1, round(font_size * 0.14))
        draw.text((padding_left, y), number_text, font=font_num, fill=rank_color, stroke_width=stroke_w, stroke_fill=STROKE_COLOR, anchor="lm")

        label = item.get("label")
        emoji = item.get("emoji")
        if revealed and (label or emoji):
            number_width = draw.textlength(number_text, font=font_num)
            x = padding_left + number_width + font_size * 0.35
            if label:
                label_font_size = font_size * 0.72
                font_label = get_font(700, label_font_size)
                label_stroke_w = max(1, round(label_font_size * 0.16))
                draw.text((x, y), label, font=font_label, fill="#ffffff", stroke_width=label_stroke_w, stroke_fill=STROKE_COLOR, anchor="lm")
                x += draw.textlength(label, font=font_label) + font_size * 0.25
            if emoji:
                emoji_font_size = font_size * 0.85
                paste_emoji(img, emoji, x, y, emoji_font_size)


def compose_frame(items, title_cfg, current_index, is_playing, video_frame_pil,
                  acilanlar=None):
    """video_frame_pil: PIL.Image (RGB) of the currently-playing clip's frame, or None."""
    img = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    bar_height = CANVAS_H * TITLE_BAR_RATIO
    draw_video_frame(img, draw, bar_height, video_frame_pil)
    draw_list(img, draw, items, current_index, is_playing, bar_height, acilanlar)
    draw_title(draw, title_cfg)
    return img
