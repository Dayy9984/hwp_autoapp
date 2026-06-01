"""인스톨러 NSIS 자산 BMP 생성기.

디자인 시스템 토큰:
  - --bg            #FFFFFF (라이트 모드 기본)
  - --bg-secondary  #F9FAFB
  - --bg-tertiary   #F3F4F6
  - --text          #1F2937
  - --text-tertiary #9CA3AF
  - --accent        #E86B45 (오렌지)
  - --accent-light  #FDF3F0

산출물:
  build/installer-splash.bmp        540 x 380   24bit BMP   (NSIS splash, 1-2초 노출)
  build/installer-sidebar.bmp       164 x 314   24bit BMP   (MUI Welcome/Finish)
  build/installer-header.bmp        150 x  57   24bit BMP   (MUI 헤더)
"""

from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import os

BUILD = Path(__file__).parent

BG = (255, 255, 255)
BG_SOFT = (249, 250, 251)
TEXT = (31, 41, 55)
TEXT_TERTIARY = (156, 163, 175)
ACCENT = (232, 107, 69)
ACCENT_LIGHT = (253, 243, 240)


def load_font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    candidates = [
        r"C:\Windows\Fonts\malgunbd.ttf" if weight == "bold" else r"C:\Windows\Fonts\malgun.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf" if weight == "bold" else r"C:\Windows\Fonts\segoeui.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def rounded_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill, outline=None, width=1):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=fill, outline=outline, width=width)


def _circle(draw, cx, cy, r, fill):
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)


# 메인 앱의 brand logo 를 그대로 paste.
# (PIL primitives 로 직접 그리려 했더니 집 모양처럼 보였던 이전 버전 폐기)
_LOGO_SOURCE = BUILD.parent / "public" / "logo.png"


def paste_logo(target_img: Image.Image, center_xy, target_height: int = 64):
    """public/logo.png 을 target_height 높이로 비율 유지 paste."""
    cx, cy = center_xy
    src = Image.open(_LOGO_SOURCE).convert("RGBA")
    sw, sh = src.size
    scale = target_height / sh
    new_w = int(sw * scale)
    logo = src.resize((new_w, target_height), Image.LANCZOS)
    target_img.paste(logo, (cx - new_w // 2, cy - target_height // 2), logo)


def draw_logo(draw, center_xy, size: int = 64):
    paste_logo(draw._image, center_xy, target_height=size)


def make_splash():
    """Splash 1.6s — 브랜드 로고 (public/logo.png) + 제품명 + 부제."""
    W, H = 540, 380
    img = Image.new("RGB", (W, H), BG)

    # 펜 로고 (public/logo.png) 그대로 paste, 적정 크기로 조정
    LOGO_TARGET_H = 120
    logo_src = Image.open(_LOGO_SOURCE).convert("RGBA")
    lw, lh = logo_src.size
    scale = LOGO_TARGET_H / lh
    lw_scaled = int(lw * scale)
    logo = logo_src.resize((lw_scaled, LOGO_TARGET_H), Image.LANCZOS)
    img.paste(logo, ((W - lw_scaled) // 2, 70), logo)

    draw = ImageDraw.Draw(img)

    # 제품명
    f_title = load_font(34, "bold")
    title = "Inserty AI"
    tw = draw.textlength(title, font=f_title)
    draw.text(((W - tw) // 2, 215), title, fill=TEXT, font=f_title)

    # 부제
    f_sub = load_font(13)
    sub = "AI 기반 문서 편집 솔루션"
    sw = draw.textlength(sub, font=f_sub)
    draw.text(((W - sw) // 2, 263), sub, fill=TEXT_TERTIARY, font=f_sub)

    out = BUILD / "installer-splash.bmp"
    img.save(out, "BMP")
    print(f"  ✓ {out.name} ({W}x{H})")


def make_sidebar():
    # MUI Welcome/Finish 사이드바: 164 x 314
    W, H = 164, 314
    img = Image.new("RGB", (W, H), BG_SOFT)
    draw = ImageDraw.Draw(img)

    # 좌측 액센트 띠
    draw.rectangle((0, 0, 4, H), fill=ACCENT)

    # 상단 로고
    draw_logo(draw, (W // 2, 80), size=72)

    # 제품명
    f_title = load_font(18, "bold")
    title = "Inserty AI"
    tw = draw.textlength(title, font=f_title)
    draw.text(((W - tw) // 2, 132), title, fill=TEXT, font=f_title)

    # 부제
    f_sub = load_font(10)
    sub = "AI 문서 편집"
    sw = draw.textlength(sub, font=f_sub)
    draw.text(((W - sw) // 2, 162), sub, fill=TEXT_TERTIARY, font=f_sub)

    out = BUILD / "installer-sidebar.bmp"
    img.save(out, "BMP")
    print(f"  ✓ {out.name} ({W}x{H})")


def make_header():
    # MUI 헤더: 150 x 57
    W, H = 150, 57
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # 우측 액센트 가는 띠
    draw.rectangle((W - 4, 0, W, H), fill=ACCENT)

    # 좌측 로고 (작은 사이즈)
    draw_logo(draw, (28, H // 2), size=36)

    # 제품명
    f_title = load_font(14, "bold")
    draw.text((58, 12), "Inserty AI", fill=TEXT, font=f_title)
    f_sub = load_font(9)
    draw.text((58, 32), "Installing…", fill=TEXT_TERTIARY, font=f_sub)

    out = BUILD / "installer-header.bmp"
    img.save(out, "BMP")
    print(f"  ✓ {out.name} ({W}x{H})")


if __name__ == "__main__":
    print("Generating NSIS installer BMP assets...")
    make_splash()
    make_sidebar()
    make_header()
    print("Done.")
