#!/usr/bin/env python3
"""Generate avatar for Event Finder Bot. Run directly: python generate_avatar.py"""

import os

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise SystemExit("Pillow is required: pip install Pillow")


def generate_avatar(output_path: str | None = None) -> str:
    if output_path is None:
        output_path = os.path.join(os.path.dirname(__file__), "avatar.png")

    SIZE = 640
    CENTER = SIZE // 2

    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for y in range(SIZE):
        r = int(102 + (118 - 102) * y / SIZE)
        g = int(126 + (75 - 126) * y / SIZE)
        b = int(234 + (162 - 234) * y / SIZE)
        draw.ellipse([0, 0, SIZE - 1, SIZE - 1], fill=(r, g, b))

    cal_x = CENTER - 140
    cal_y = CENTER - 120
    cal_w = 280
    cal_h = 260
    cal_r = 20

    draw.rounded_rectangle(
        [cal_x, cal_y, cal_x + cal_w, cal_y + cal_h],
        radius=cal_r,
        fill=(255, 255, 255, 255),
    )

    header_h = 50
    draw.rounded_rectangle(
        [cal_x, cal_y, cal_x + cal_w, cal_y + header_h],
        radius=cal_r,
        fill=(255, 255, 255, 255),
    )
    draw.rectangle(
        [cal_x, cal_y + header_h - cal_r, cal_x + cal_w, cal_y + header_h],
        fill=(255, 255, 255, 255),
    )

    draw.rounded_rectangle(
        [cal_x, cal_y, cal_x + cal_w, cal_y + header_h],
        radius=cal_r,
        fill=(255, 107, 107, 255),
    )
    draw.rectangle(
        [cal_x, cal_y + header_h - cal_r, cal_x + cal_w, cal_y + header_h],
        fill=(255, 107, 107, 255),
    )

    ring_y = cal_y + 15
    ring_r = 12
    draw.ellipse(
        [cal_x + 50 - ring_r, ring_y - ring_r, cal_x + 50 + ring_r, ring_y + ring_r],
        fill=(255, 255, 255, 255),
    )
    draw.ellipse(
        [
            cal_x + cal_w - 50 - ring_r,
            ring_y - ring_r,
            cal_x + cal_w - 50 + ring_r,
            ring_y + ring_r,
        ],
        fill=(255, 255, 255, 255),
    )

    try:
        font_header = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32
        )
    except Exception:
        font_header = ImageFont.load_default()

    draw.text(
        (CENTER - 30, cal_y + 8), "EV", fill=(255, 255, 255, 255), font=font_header
    )

    grid_y = cal_y + header_h + 20
    line_color = (200, 200, 200, 255)

    for i in range(3):
        y = grid_y + i * 45
        draw.line([(cal_x + 15, y), (cal_x + cal_w - 15, y)], fill=line_color, width=2)

    for i in range(4):
        x = cal_x + 20 + i * 70
        draw.line([(x, grid_y), (x, grid_y + 95)], fill=line_color, width=2)

    dot_positions = [
        (cal_x + 40, grid_y + 22),
        (cal_x + 110, grid_y + 22),
        (cal_x + 180, grid_y + 67),
        (cal_x + 250, grid_y + 22),
        (cal_x + 75, grid_y + 67),
    ]

    dot_color = (102, 126, 234, 255)
    for dx, dy in dot_positions:
        draw.ellipse([dx - 8, dy - 8, dx + 8, dy + 8], fill=dot_color)

    highlight_dot = (cal_x + 180, grid_y + 67)
    draw.ellipse(
        [
            highlight_dot[0] - 10,
            highlight_dot[1] - 10,
            highlight_dot[0] + 10,
            highlight_dot[1] + 10,
        ],
        fill=(255, 107, 107, 255),
    )

    search_x = CENTER + 80
    search_y = CENTER + 100
    search_r = 35

    draw.ellipse(
        [
            search_x - search_r,
            search_y - search_r,
            search_x + search_r,
            search_y + search_r,
        ],
        fill=(255, 255, 255, 220),
        outline=(102, 126, 234, 255),
        width=5,
    )

    handle_start_x = search_x + int(search_r * 0.7)
    handle_start_y = search_y + int(search_r * 0.7)
    handle_end_x = handle_start_x + 30
    handle_end_y = handle_start_y + 30
    draw.line(
        [(handle_start_x, handle_start_y), (handle_end_x, handle_end_y)],
        fill=(102, 126, 234, 255),
        width=6,
    )

    img.save(output_path, "PNG")
    return output_path


if __name__ == "__main__":
    path = generate_avatar()
    print(f"Avatar saved to {path}")
    print("Size: 640x640")
