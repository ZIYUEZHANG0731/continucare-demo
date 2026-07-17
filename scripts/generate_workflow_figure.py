from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets" / "figma_workflow_clean_v2.png"

W, H = 1440, 940

C = {
    "bg": "#F6F8FB",
    "white": "#FFFFFF",
    "ink": "#172033",
    "muted": "#667085",
    "line": "#94A3B8",
    "blue": "#246BFE",
    "blue_soft": "#EAF1FF",
    "teal": "#0E9384",
    "teal_soft": "#E6F7F4",
    "green": "#16A34A",
    "green_soft": "#EAF7EE",
    "amber": "#D97706",
    "amber_soft": "#FFF4E5",
    "red": "#D92D20",
    "red_soft": "#FFEDEC",
    "purple": "#7C3AED",
    "purple_soft": "#F1EAFE",
}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


FONT = {
    "title": font(24, True),
    "h1": font(30, True),
    "body": font(14),
    "small": font(12),
    "xs": font(11),
    "label": font(11, True),
    "card_title": font(16, True),
    "lane": font(18, True),
}


def text_wrap(draw: ImageDraw.ImageDraw, value: str, max_width: int, fnt: ImageFont.FreeTypeFont) -> list[str]:
    words = value.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if draw.textlength(candidate, font=fnt) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def pill(draw: ImageDraw.ImageDraw, xy: tuple[int, int], size: tuple[int, int], label: str, fill: str, color: str) -> None:
    x, y = xy
    w, h = size
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=fill)
    tw = draw.textlength(label, font=FONT["label"])
    draw.text((x + (w - tw) / 2, y + 6), label, font=FONT["label"], fill=color)


def card(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    idx: int,
    title: str,
    body: str,
    color: str,
    soft: str,
) -> None:
    draw.rounded_rectangle((x, y, x + w, y + h), radius=10, fill=C["white"], outline="#D9E2EC", width=1)
    draw.rounded_rectangle((x, y, x + 6, y + h), radius=6, fill=color)
    draw.ellipse((x + 18, y + 18, x + 46, y + 46), fill=soft)
    n = str(idx)
    nw = draw.textlength(n, font=FONT["label"])
    draw.text((x + 32 - nw / 2, y + 25), n, font=FONT["label"], fill=color)
    draw.text((x + 58, y + 16), title, font=FONT["card_title"], fill=C["ink"])
    for i, line in enumerate(text_wrap(draw, body, w - 76, FONT["small"])[:2]):
        draw.text((x + 58, y + 52 + i * 16), line, font=FONT["small"], fill=C["muted"])


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (W, H), C["bg"])
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, W, 88), fill=C["white"])
    draw.text((56, 30), "05 End-to-end Care Workflow", font=FONT["title"], fill=C["ink"])
    draw.text((56, 60), "AI Native Doctor Copilot / Continuous Care Operating System", font=FONT["small"], fill=C["muted"])
    pill(draw, (1068, 32), (146, 26), "Human-in-the-loop", C["green_soft"], C["green"])
    pill(draw, (1230, 32), (170, 26), "No autonomous diagnosis", C["red_soft"], C["red"])

    draw.text((56, 122), "The operational story from discharge to follow-up visit", font=FONT["h1"], fill=C["ink"])
    draw.text((56, 160), "The diagram emphasizes where AI assists and where humans remain accountable.", font=FONT["body"], fill=C["muted"])

    lanes = [
        ("Patient side", 220, C["teal"], C["teal_soft"]),
        ("AI care OS", 378, C["blue"], C["blue_soft"]),
        ("Clinical team", 536, C["green"], C["green_soft"]),
        ("Governance", 694, C["red"], C["red_soft"]),
    ]
    for name, y, color, soft in lanes:
        draw.rounded_rectangle((56, y, 1384, y + 118), radius=12, fill=soft)
        draw.text((82, y + 46), name, font=FONT["lane"], fill=color)

    step_xs = [250, 492, 734, 976, 1218]
    labels = ["Discharge", "Days 1-N", "Risk Event", "Human Review", "Follow-up"]
    for i, label in enumerate(labels):
        tw = draw.textlength(label, font=FONT["label"])
        draw.text((step_xs[i] + 95 - tw / 2, 196), label, font=FONT["label"], fill=C["muted"])
        if i < len(labels) - 1:
            x1 = step_xs[i] + 170
            x2 = step_xs[i + 1] + 20
            y = 203
            draw.line((x1, y, x2, y), fill=C["line"], width=2)
            draw.ellipse((x2 - 4, y - 4, x2 + 4, y + 4), fill=C["line"])

    card(draw, step_xs[0], 398, 190, 84, 1, "Discharge", "Pathway assigned", C["blue"], C["blue_soft"])
    card(draw, step_xs[1], 240, 190, 84, 2, "Daily check-in", "Observations captured", C["teal"], C["teal_soft"])
    card(draw, step_xs[2], 398, 190, 84, 3, "Rule hit", "Risk signal generated", C["amber"], C["amber_soft"])
    card(draw, step_xs[3], 556, 190, 84, 4, "Escalation", "Nurse / doctor reviews", C["green"], C["green_soft"])
    card(draw, step_xs[4], 398, 190, 84, 5, "Pre-visit", "Summary with evidence", C["purple"], C["purple_soft"])

    draw.rounded_rectangle((250, 720, 1350, 778), radius=16, fill=C["red_soft"], outline="#F5B7B1", width=1)
    emergency = "Emergency workflow bypasses normal summary flow: red flags route immediately to pre-approved hospital instructions and human review."
    tw = draw.textlength(emergency, font=FONT["body"])
    draw.text((250 + (1100 - tw) / 2, 740), emergency, font=FONT["body"], fill=C["red"])

    note = "Normal flow is ordered by step number; cross-lane movement always means a human-owned workflow transition."
    draw.text((250, 832), note, font=FONT["body"], fill=C["muted"])

    image.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
