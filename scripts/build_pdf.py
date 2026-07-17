from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUT = ROOT / "output" / "pdf" / "doctor_copilot_product_system_design_v0.1.pdf"

PAGE_W, PAGE_H = A4
LEFT = RIGHT = 18 * mm
TOP = 18 * mm
BOTTOM = 18 * mm
CONTENT_W = PAGE_W - LEFT - RIGHT

ACCENT = colors.HexColor("#2563EB")
DARK = colors.HexColor("#111827")
TEXT = colors.HexColor("#243042")
MUTED = colors.HexColor("#667085")
LIGHT_BG = colors.HexColor("#F8FAFC")
BORDER = colors.HexColor("#D9E2EC")
BLUE_50 = colors.HexColor("#EFF6FF")
GREEN_50 = colors.HexColor("#ECFDF5")
AMBER_50 = colors.HexColor("#FFFBEB")
RED_50 = colors.HexColor("#FEF2F2")
SLATE_50 = colors.HexColor("#F1F5F9")


ORDERED_DOCS = [
    "00_product_overview.md",
    "01_prd.md",
    "02_system_architecture.md",
    "03_data_model_fhir.md",
    "04_pathway_engine.md",
    "05_ai_agents.md",
    "06_safety_and_governance.md",
    "07_workbench_and_patient_app.md",
    "08_demo_script.md",
    "09_pitch_and_defense.md",
    "10_roadmap.md",
    "11_wireframes_and_visuals.md",
]

FIGURES_BY_DOC = {
    "02_system_architecture.md": [
        (
            "figma_architecture_clean_v2.png",
            "图 2-1 Figwright中文重绘系统架构图：从患者院外信号到医生工作台的连续照护操作系统。",
        ),
        (
            "figwright_pathway_config_sequence_v1.png",
            "图 2-2 Figwright重绘Pathway配置时序图：AI建议、临床审批、安全审查与发布边界。",
        ),
        (
            "figwright_followup_data_sequence_v1.png",
            "图 2-3 Figwright重绘随访数据流：患者回复如何转化为Observation并触发规则引擎。",
        ),
        (
            "figwright_summary_sequence_v1.png",
            "图 2-4 Figwright重绘复诊摘要流：长期记忆、Summary草稿、安全检查和医生审阅。",
        ),
    ],
    "03_data_model_fhir.md": [
        (
            "figma_data_model_clean_v2.png",
            "图 3-1 Figwright中文重绘FHIR风格资源模型：Patient锚点、核心资源与派生记忆层。",
        )
    ],
    "04_pathway_engine.md": [
        (
            "figma_pathway_engine_clean_v2.png",
            "图 4-1 Figwright中文重绘Clinical Pathway Engine：从路径创建到运行态对象的配置闭环。",
        ),
        (
            "figwright_pathway_lifecycle_v1.png",
            "图 4-2 Figwright重绘Pathway生命周期：Draft、Clinical Review、Pilot、Active与Retired。",
        ),
    ],
    "05_ai_agents.md": [
        (
            "figma_agent_operating_model_clean.png",
            "图 5-1 Figwright中文重绘Agent Operating Model：设计时、运行时与Safety Agent边界。",
        )
    ],
    "06_safety_and_governance.md": [
        (
            "figwright_safety_emergency_v1.png",
            "图 6-1 Figwright重绘安全与应急工作流：规则、置信度、证据链与人工审批边界。",
        )
    ],
    "07_workbench_and_patient_app.md": [
        (
            "figwright_workbench_information_architecture_v1.png",
            "图 7-1 Figwright重绘页面信息架构：医生、护士、患者和Pathway工具的工作流关系。",
        ),
        (
            "figwright_doctor_workbench_v1.png",
            "图 7-2 Figwright重绘医生工作台：风险中心、患者时间线与复诊摘要的页面逻辑。",
        ),
        (
            "figwright_patient_followup_v1.png",
            "图 7-3 Figwright重绘患者端随访：提醒、动态追问、教育与结构化Observation输出。",
        ),
    ],
    "08_demo_script.md": [
        (
            "figwright_care_workflow_v2.png",
            "图 8-1 Figwright重绘完整业务流程：从出院到复诊前Summary的人机协同闭环。",
        )
    ],
    "11_wireframes_and_visuals.md": [
        (
            "figwright_clinician_wireframes_v1.png",
            "图 11-1 Figwright重绘临床工作台线框合集：Dashboard、Patient Detail、Summary、Timeline与Trends。",
        ),
        (
            "figwright_operations_wireframes_v1.png",
            "图 11-2 Figwright重绘运营处理线框合集：Risk Center、Alert Detail与Pathway Studio。",
        ),
        (
            "figwright_patient_wireframes_v1.png",
            "图 11-3 Figwright重绘患者端线框合集：聊天随访、快速打卡与结构化Observation输出。",
        ),
    ],
}


SUPPRESS_MERMAID_DOCS = {
    "02_system_architecture.md",
    "03_data_model_fhir.md",
    "04_pathway_engine.md",
    "05_ai_agents.md",
    "07_workbench_and_patient_app.md",
}
SUPPRESS_TEXT_WIREFRAME_DOCS = {"11_wireframes_and_visuals.md"}


def register_fonts() -> str:
    # Embed a TrueType font so the generated PDF renders consistently in Poppler,
    # Preview, browser PDF viewers, and Windows PDF readers.
    candidates = [
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            pdfmetrics.registerFont(TTFont("DocFont", str(candidate)))
            return "DocFont"
    raise FileNotFoundError("No suitable Unicode TrueType font found")


FONT = register_fonts()


def make_styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            "CoverTitle",
            fontName=FONT,
            fontSize=27,
            leading=34,
            textColor=DARK,
            alignment=TA_LEFT,
            spaceAfter=10,
        )
    )
    styles.add(
        ParagraphStyle(
            "CoverSubtitle",
            fontName=FONT,
            fontSize=13,
            leading=20,
            textColor=MUTED,
            alignment=TA_LEFT,
            spaceAfter=18,
        )
    )
    styles.add(
        ParagraphStyle(
            "DocTitle",
            fontName=FONT,
            fontSize=22,
            leading=28,
            textColor=DARK,
            spaceBefore=6,
            spaceAfter=12,
        )
    )
    styles.add(
        ParagraphStyle(
            "H2",
            fontName=FONT,
            fontSize=15,
            leading=20,
            textColor=DARK,
            spaceBefore=12,
            spaceAfter=7,
        )
    )
    styles.add(
        ParagraphStyle(
            "H3",
            fontName=FONT,
            fontSize=12,
            leading=17,
            textColor=colors.HexColor("#1F2937"),
            spaceBefore=9,
            spaceAfter=5,
        )
    )
    styles.add(
        ParagraphStyle(
            "BodyCN",
            fontName=FONT,
            fontSize=9.6,
            leading=15,
            textColor=TEXT,
            spaceAfter=5,
        )
    )
    styles.add(
        ParagraphStyle(
            "BodySmall",
            fontName=FONT,
            fontSize=8.4,
            leading=12,
            textColor=TEXT,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            "BulletCN",
            fontName=FONT,
            fontSize=9.2,
            leading=14,
            leftIndent=12,
            firstLineIndent=-7,
            bulletIndent=0,
            textColor=TEXT,
            spaceAfter=3,
        )
    )
    styles.add(
        ParagraphStyle(
            "TableCell",
            fontName=FONT,
            fontSize=7.4,
            leading=10,
            textColor=TEXT,
        )
    )
    styles.add(
        ParagraphStyle(
            "TableHead",
            fontName=FONT,
            fontSize=7.6,
            leading=10,
            textColor=colors.white,
        )
    )
    styles.add(
        ParagraphStyle(
            "Caption",
            fontName=FONT,
            fontSize=8,
            leading=11,
            textColor=MUTED,
            alignment=TA_CENTER,
            spaceBefore=3,
            spaceAfter=8,
        )
    )
    return styles


STYLES = make_styles()


def clean_inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`([^`]+)`", r"<font color='#0F766E'>\1</font>", text)
    return text


def plain_text(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("**", "").replace("`", "")
    return text.strip()


class CoverPage(Flowable):
    def __init__(self, width: float, height: float):
        super().__init__()
        self.width = width
        self.height = height

    def wrap(self, availWidth, availHeight):
        return availWidth, availHeight

    def draw(self):
        c = self.canv
        w = self.width
        h = self.height
        c.saveState()
        c.setFillColor(colors.white)
        c.rect(-LEFT, -BOTTOM, PAGE_W, PAGE_H, stroke=0, fill=1)
        c.setFillColor(BLUE_50)
        c.roundRect(0, h - 70 * mm, w, 58 * mm, 10, stroke=0, fill=1)
        c.setFillColor(ACCENT)
        c.roundRect(0, h - 15 * mm, 72 * mm, 5 * mm, 2, stroke=0, fill=1)
        c.setFillColor(DARK)
        c.setFont(FONT, 27)
        c.drawString(0, h - 31 * mm, "AI Native Doctor Copilot")
        c.setFont(FONT, 17)
        c.drawString(0, h - 43 * mm, "产品需求与系统设计说明书")
        c.setFillColor(MUTED)
        c.setFont(FONT, 10.5)
        c.drawString(0, h - 54 * mm, "Continuous Care Operating System / v0.1")

        y = h - 88 * mm
        c.setFillColor(DARK)
        c.setFont(FONT, 15)
        c.drawString(0, y, "核心定位")
        y -= 10 * mm
        c.setFillColor(TEXT)
        c.setFont(FONT, 10.5)
        lines = [
            "面向医院的连续照护操作系统。",
            "不替代医生诊断和治疗，而是在患者离院后持续收集、理解、整理和总结健康状态。",
            "在复诊前向医生提供可审阅、可解释、可追溯的患者连续健康记忆。",
        ]
        for line in lines:
            c.drawString(0, y, line)
            y -= 7 * mm

        y -= 9 * mm
        c.setFillColor(SLATE_50)
        c.roundRect(0, y - 41 * mm, w, 44 * mm, 8, stroke=0, fill=1)
        c.setFillColor(DARK)
        c.setFont(FONT, 12)
        c.drawString(8 * mm, y - 6 * mm, "安全边界")
        c.setFont(FONT, 9.6)
        c.setFillColor(TEXT)
        guards = [
            "AI不诊断、不改药、不决定治疗方案。",
            "医生确认Care Pathway，并负责最终临床决策。",
            "高风险判断由规则引擎和人工流程兜底。",
            "所有临床摘要必须有证据链、置信度和审计记录。",
        ]
        gy = y - 15 * mm
        for item in guards:
            c.circle(10 * mm, gy + 1.5, 1.2, stroke=0, fill=1)
            c.drawString(15 * mm, gy, item)
            gy -= 7 * mm

        c.setFillColor(MUTED)
        c.setFont(FONT, 8.5)
        c.drawString(0, 10 * mm, f"Generated {datetime.now().strftime('%Y-%m-%d')} from docs/*.md")
        c.restoreState()


class SectionDivider(Flowable):
    def __init__(self, title: str, subtitle: str = ""):
        super().__init__()
        self.title = title
        self.subtitle = subtitle
        self.width = CONTENT_W
        self.height = 42 * mm

    def wrap(self, availWidth, availHeight):
        return availWidth, self.height

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(LIGHT_BG)
        c.roundRect(0, 0, self.width, self.height, 8, stroke=0, fill=1)
        c.setFillColor(ACCENT)
        c.roundRect(0, self.height - 6 * mm, self.width, 6 * mm, 4, stroke=0, fill=1)
        c.setFillColor(DARK)
        c.setFont(FONT, 17)
        c.drawString(8 * mm, self.height - 21 * mm, self.title[:60])
        if self.subtitle:
            c.setFillColor(MUTED)
            c.setFont(FONT, 9)
            c.drawString(8 * mm, self.height - 31 * mm, self.subtitle[:85])
        c.restoreState()


class CodeBlock(Flowable):
    def __init__(self, text: str, title: str | None = None, font_size: float = 6.7):
        super().__init__()
        self.raw_lines = text.rstrip("\n").splitlines() or [""]
        self.title = title
        self.font_size = font_size
        self.leading = font_size + 2.2
        self.lines: list[str] = []
        self.width = CONTENT_W
        self.height = 0

    def _wrap_line(self, line: str, max_chars: int) -> list[str]:
        if len(line) <= max_chars:
            return [line]
        out = []
        current = line
        while len(current) > max_chars:
            out.append(current[:max_chars])
            current = "  " + current[max_chars:]
        out.append(current)
        return out

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        max_chars = max(40, int(availWidth / (self.font_size * 0.53)))
        self.lines = []
        for line in self.raw_lines:
            self.lines.extend(self._wrap_line(line, max_chars))
        title_h = 12 if self.title else 0
        self.height = title_h + 10 + len(self.lines) * self.leading
        return availWidth, self.height

    def split(self, availWidth, availHeight):
        self.wrap(availWidth, availHeight)
        min_height = 35
        if self.height <= availHeight or availHeight < min_height:
            return []
        title_h = 12 if self.title else 0
        capacity = int((availHeight - title_h - 12) / self.leading)
        if capacity < 4:
            return []
        first = "\n".join(self.lines[:capacity])
        rest = "\n".join(self.lines[capacity:])
        return [CodeBlock(first, self.title, self.font_size), CodeBlock(rest, None, self.font_size)]

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(colors.HexColor("#F7F9FC"))
        c.setStrokeColor(BORDER)
        c.roundRect(0, 0, self.width, self.height, 5, stroke=1, fill=1)
        y = self.height - 8
        if self.title:
            c.setFillColor(ACCENT)
            c.setFont(FONT, 7.5)
            c.drawString(7, y - 4, self.title)
            y -= 12
        c.setFillColor(colors.HexColor("#334155"))
        c.setFont(FONT, self.font_size)
        for line in self.lines:
            y -= self.leading
            c.drawString(7, y, line)
        c.restoreState()


class DiagramFlowable(Flowable):
    def __init__(self, mermaid: str, caption: str = ""):
        super().__init__()
        self.mermaid = mermaid
        self.caption = caption
        self.width = CONTENT_W
        self.height = self._height_for_kind()

    def _kind(self) -> str:
        m = self.mermaid
        if "Patient[患者端" in m and "PathwayEngine" in m:
            return "architecture"
        if "erDiagram" in m:
            return "er"
        if "stateDiagram-v2" in m:
            return "lifecycle"
        if "Guideline[Guideline Agent]" in m:
            return "agents"
        if "Dashboard[Dashboard]" in m:
            return "pages"
        if "sequenceDiagram" in m:
            return "sequence"
        return "generic"

    def _height_for_kind(self) -> float:
        kind = self._kind()
        if kind == "architecture":
            return 118 * mm
        if kind == "er":
            return 98 * mm
        if kind == "sequence":
            msgs = len([l for l in self.mermaid.splitlines() if "->>" in l or "-->>" in l])
            return max(62 * mm, min(118 * mm, 30 * mm + msgs * 10 * mm))
        if kind in {"agents", "pages"}:
            return 82 * mm
        if kind == "lifecycle":
            return 48 * mm
        return 64 * mm

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        return availWidth, self.height

    def _box(self, c, x, y, w, h, label, fill=BLUE_50, stroke=BORDER, fs=8.2):
        c.setFillColor(fill)
        c.setStrokeColor(stroke)
        c.roundRect(x, y, w, h, 5, stroke=1, fill=1)
        c.setFillColor(DARK)
        c.setFont(FONT, fs)
        lines = wrap_chars(label, max(6, int(w / (fs * 0.7))))
        ty = y + h / 2 + (len(lines) - 1) * fs * 0.55
        for ln in lines[:3]:
            c.drawCentredString(x + w / 2, ty, ln)
            ty -= fs + 2

    def _arrow(self, c, x1, y1, x2, y2, color=ACCENT):
        c.setStrokeColor(color)
        c.setFillColor(color)
        c.setLineWidth(1.1)
        c.line(x1, y1, x2, y2)
        ang = 0
        if abs(x2 - x1) >= abs(y2 - y1):
            ang = 1 if x2 >= x1 else -1
            c.line(x2, y2, x2 - ang * 5, y2 + 3)
            c.line(x2, y2, x2 - ang * 5, y2 - 3)
        else:
            down = 1 if y2 <= y1 else -1
            c.line(x2, y2, x2 - 3, y2 + down * 5)
            c.line(x2, y2, x2 + 3, y2 + down * 5)

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(colors.white)
        c.setStrokeColor(BORDER)
        c.roundRect(0, 0, self.width, self.height, 8, stroke=1, fill=1)
        c.setFillColor(DARK)
        c.setFont(FONT, 9)
        title = self.caption or diagram_title(self.mermaid)
        c.drawString(8, self.height - 25, title)
        c.setStrokeColor(BORDER)
        c.line(8, self.height - 31, self.width - 8, self.height - 31)
        kind = self._kind()
        if kind == "architecture":
            self._draw_architecture(c)
        elif kind == "er":
            self._draw_er(c)
        elif kind == "lifecycle":
            self._draw_lifecycle(c)
        elif kind == "agents":
            self._draw_agents(c)
        elif kind == "pages":
            self._draw_pages(c)
        elif kind == "sequence":
            self._draw_sequence(c)
        else:
            self._draw_generic(c)
        c.restoreState()

    def _draw_architecture(self, c):
        top = self.height - 44
        box_h = 14 * mm
        gap = 7
        box_w = (self.width - 28 - gap * 5) / 6

        main = [
            ("患者端", GREEN_50),
            ("Care Agent", BLUE_50),
            ("Obs/Comm", BLUE_50),
            ("Data Layer", SLATE_50),
            ("Risk / Memory", RED_50),
            ("Workbench", GREEN_50),
        ]
        positions = {}
        y = top - box_h
        for i, (label, fill) in enumerate(main):
            x = 14 + i * (box_w + gap)
            self._box(c, x, y, box_w, box_h, label, fill=fill, fs=7.2)
            positions[label] = (x, y, box_w, box_h)
            if i > 0:
                px, py, pw, ph = positions[main[i - 1][0]]
                self._arrow(c, px + pw, py + ph / 2, x, y + box_h / 2)

        y2 = y - 32
        upper = [
            ("Guideline", BLUE_50, 1),
            ("Pathway", BLUE_50, 2),
            ("Rule Engine", RED_50, 4),
        ]
        for label, fill, col in upper:
            x = 14 + col * (box_w + gap)
            self._box(c, x, y2, box_w, box_h, label, fill=fill, fs=7.2)
            positions[label] = (x, y2, box_w, box_h)
        self._arrow(c, positions["Guideline"][0] + box_w, y2 + box_h / 2, positions["Pathway"][0], y2 + box_h / 2)
        self._arrow(c, positions["Rule Engine"][0] + box_w / 2, y2 + box_h, positions["Risk / Memory"][0] + box_w / 2, y)

        y3 = y2 - 32
        lower = [
            ("HIS/EMR", AMBER_50, 0),
            ("Integration", AMBER_50, 3),
            ("Summary Agent", GREEN_50, 4),
            ("Admin Console", SLATE_50, 5),
        ]
        for label, fill, col in lower:
            x = 14 + col * (box_w + gap)
            self._box(c, x, y3, box_w, box_h, label, fill=fill, fs=7.2)
            positions[label] = (x, y3, box_w, box_h)
        self._arrow(c, positions["HIS/EMR"][0] + box_w, y3 + box_h / 2, positions["Integration"][0], y3 + box_h / 2)
        self._arrow(c, positions["Integration"][0] + box_w / 2, y3 + box_h, positions["Data Layer"][0] + box_w / 2, positions["Data Layer"][1])
        self._arrow(c, positions["Risk / Memory"][0] + box_w / 2, y, positions["Summary Agent"][0] + box_w / 2, y3 + box_h)
        self._arrow(c, positions["Summary Agent"][0] + box_w, y3 + box_h / 2, positions["Workbench"][0], positions["Workbench"][1] + 2)

        guard_y = 15
        c.setFillColor(RED_50)
        c.setStrokeColor(BORDER)
        c.roundRect(14, guard_y, self.width - 28, 13 * mm, 6, stroke=1, fill=1)
        c.setFillColor(DARK)
        c.setFont(FONT, 7.8)
        c.drawCentredString(self.width / 2, guard_y + 5.2 * mm, "Safety & Governance Layer: Evidence · Confidence · Human Approval · Emergency Workflow")

    def _draw_er(self, c):
        center_w = 70
        center_h = 18
        center_x = self.width / 2 - center_w / 2
        center_y = self.height / 2 - 6
        self._box(c, center_x, center_y, center_w, center_h, "Patient", fill=BLUE_50, fs=8)
        nodes = [
            ("Encounter", 24, self.height - 52),
            ("Observation", 24, self.height - 86),
            ("Medication", self.width - 98, self.height - 52),
            ("Communication", self.width - 98, self.height - 86),
            ("Alert / Task", 24, 28),
            ("Timeline", self.width / 2 - 35, 23),
            ("AI Summary", self.width - 98, 28),
            ("CarePathway", self.width / 2 - 43, self.height - 52),
        ]
        c.setStrokeColor(colors.HexColor("#94A3B8"))
        c.setLineWidth(0.9)
        for label, x, y in nodes:
            c.line(center_x + center_w / 2, center_y + center_h / 2, x + 37, y + 8)
        for label, x, y in nodes:
            fill = GREEN_50 if label == "CarePathway" else SLATE_50
            self._box(c, x, y, 74, 16, label, fill=fill, fs=7.3)

    def _draw_lifecycle(self, c):
        labels = ["Draft", "Clinical Review", "Revision", "Pilot", "Active", "Retired"]
        w = (self.width - 32) / len(labels)
        y = 18
        for i, label in enumerate(labels):
            x = 10 + i * w
            fill = GREEN_50 if label == "Active" else BLUE_50 if i < 4 else SLATE_50
            self._box(c, x, y, w - 8, 17 * mm, label, fill=fill, fs=7.6)
            if i < len(labels) - 1:
                self._arrow(c, x + w - 8, y + 8.5 * mm, x + w, y + 8.5 * mm)

    def _draw_agents(self, c):
        w = (self.width - 48) / 4
        h = 15 * mm
        top = self.height - 75
        row_gap = h + 10
        rows = [
            [("Guideline", BLUE_50), ("Pathway", BLUE_50), ("Care", BLUE_50), ("Clinical Data", SLATE_50)],
            [("Memory", BLUE_50), ("Risk", RED_50), ("Summary", GREEN_50), ("Workbench", GREEN_50)],
        ]
        coords = {}
        for r, row in enumerate(rows):
            y = top - r * row_gap
            for i, (label, fill) in enumerate(row):
                x = 12 + i * (w + 8)
                self._box(c, x, y, w, h, label, fill=fill, fs=7.7)
                coords[label] = (x, y)
                if i > 0 and r == 0:
                    px, py = coords[row[i - 1][0]]
                    self._arrow(c, px + w, py + h / 2, x, y + h / 2)
        c.setFillColor(MUTED)
        c.setFont(FONT, 6.8)
        c.drawCentredString(self.width / 2, top - row_gap - 12, "Clinical Data provides the evidence base for Memory, Risk, and Summary agents")
        self._arrow(c, coords["Memory"][0] + w, coords["Memory"][1] + h / 2, coords["Summary"][0], coords["Summary"][1] + h / 2)
        self._arrow(c, coords["Risk"][0] + w, coords["Risk"][1] + h / 2, coords["Summary"][0], coords["Summary"][1] + h / 2)
        self._arrow(c, coords["Summary"][0] + w, coords["Summary"][1] + h / 2, coords["Workbench"][0], coords["Workbench"][1] + h / 2)

        guard_y = 16
        c.setFillColor(RED_50)
        c.setStrokeColor(BORDER)
        c.roundRect(12, guard_y, self.width - 24, 13 * mm, 6, stroke=1, fill=1)
        c.setFillColor(DARK)
        c.setFont(FONT, 7.8)
        c.drawCentredString(self.width / 2, guard_y + 5.2 * mm, "Safety Agent checks outputs before patient messages, alerts, summaries, and pathway publication")

    def _draw_pages(self, c):
        nodes = [
            ("Dashboard", self.width / 2 - 34, self.height - 48),
            ("Risk Center", 24, self.height - 83),
            ("Patient List", self.width / 2 - 34, self.height - 83),
            ("Patient Detail", self.width / 2 - 34, self.height - 118),
            ("Summary", 20, 20),
            ("Timeline", 112, 20),
            ("Trends", 204, 20),
            ("Communication", 296, 20),
        ]
        for label, x, y in nodes:
            self._box(c, x, y, 72, 15 * mm, label, fill=BLUE_50, fs=7.6)
        lookup = {n[0]: n for n in nodes}
        for a, b in [
            ("Dashboard", "Risk Center"),
            ("Dashboard", "Patient List"),
            ("Patient List", "Patient Detail"),
            ("Patient Detail", "Summary"),
            ("Patient Detail", "Timeline"),
            ("Patient Detail", "Trends"),
            ("Patient Detail", "Communication"),
        ]:
            _, ax, ay = lookup[a]
            _, bx, by = lookup[b]
            self._arrow(c, ax + 36, ay, bx + 36, by + 15 * mm)

    def _draw_sequence(self, c):
        participants = parse_sequence_participants(self.mermaid)
        messages = parse_sequence_messages(self.mermaid)
        if not participants:
            self._draw_generic(c)
            return
        n = len(participants)
        left = 12
        usable = self.width - 24
        step = usable / max(1, n - 1) if n > 1 else usable
        xs = {p[0]: left + i * step for i, p in enumerate(participants)}
        label_y = self.height - 42
        for key, label in participants:
            self._box(c, xs[key] - 26, label_y, 52, 12 * mm, label, fill=BLUE_50, fs=6.8)
            c.setStrokeColor(colors.HexColor("#CBD5E1"))
            c.setDash(2, 3)
            c.line(xs[key], label_y, xs[key], 18)
            c.setDash()
        y = label_y - 16
        for sender, receiver, text in messages[:9]:
            if sender not in xs or receiver not in xs:
                continue
            self._arrow(c, xs[sender], y, xs[receiver], y)
            c.setFillColor(TEXT)
            c.setFont(FONT, 6.5)
            mid = (xs[sender] + xs[receiver]) / 2
            c.drawCentredString(mid, y + 3, text[:34])
            y -= 14

    def _draw_generic(self, c):
        preview = "\n".join(self.mermaid.strip().splitlines()[:8])
        block = CodeBlock(preview, "Mermaid source", 6.6)
        block.canv = c
        block.wrap(self.width - 18, self.height - 34)
        c.translate(9, 8)
        block.draw()


def wrap_chars(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def diagram_title(mermaid: str) -> str:
    if "sequenceDiagram" in mermaid:
        return "业务时序图"
    if "erDiagram" in mermaid:
        return "FHIR风格数据模型关系图"
    if "stateDiagram" in mermaid:
        return "Pathway生命周期图"
    if "flowchart" in mermaid:
        return "系统关系图"
    return "图表"


def parse_sequence_participants(src: str) -> list[tuple[str, str]]:
    out = []
    for line in src.splitlines():
        line = line.strip()
        m = re.match(r"participant\s+(\w+)\s+as\s+(.+)", line)
        if m:
            out.append((m.group(1), m.group(2).strip()))
    return out


def parse_sequence_messages(src: str) -> list[tuple[str, str, str]]:
    out = []
    for line in src.splitlines():
        line = line.strip()
        m = re.match(r"(\w+)-+>>(\w+):\s*(.+)", line)
        if m:
            out.append((m.group(1), m.group(2), m.group(3).strip()))
    return out


def make_table(rows: list[list[str]]) -> Table:
    col_count = max(len(r) for r in rows)
    normalized = [r + [""] * (col_count - len(r)) for r in rows]
    cell_style = STYLES["TableCell"]
    head_style = STYLES["TableHead"]
    data = []
    for r_idx, row in enumerate(normalized):
        style = head_style if r_idx == 0 else cell_style
        data.append([Paragraph(clean_inline(c.strip()), style) for c in row])
    col_widths = [CONTENT_W / col_count] * col_count
    table = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.35, BORDER),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def make_figure(image_name: str, caption: str, max_height: float = 112 * mm) -> KeepTogether:
    image_path = DOCS / "assets" / image_name
    if not image_path.exists():
        return KeepTogether(
            [
                Paragraph(
                    clean_inline(f"图像资产缺失：{image_name}"),
                    STYLES["Caption"],
                )
            ]
        )
    image = Image(str(image_path))
    scale = min(CONTENT_W / image.imageWidth, max_height / image.imageHeight)
    image.drawWidth = image.imageWidth * scale
    image.drawHeight = image.imageHeight * scale
    image.hAlign = "CENTER"
    return KeepTogether(
        [
            image,
            Paragraph(clean_inline(caption), STYLES["Caption"]),
            Spacer(1, 6),
        ]
    )


def is_table_separator(line: str) -> bool:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c or "") for c in cells)


def parse_table(lines: Sequence[str], start: int) -> tuple[list[list[str]], int] | None:
    if start + 1 >= len(lines):
        return None
    if not lines[start].strip().startswith("|") or not is_table_separator(lines[start + 1]):
        return None
    rows = []
    idx = start
    while idx < len(lines) and lines[idx].strip().startswith("|"):
        if idx != start + 1:
            rows.append([plain_text(c) for c in lines[idx].strip().strip("|").split("|")])
        idx += 1
    return rows, idx


def flush_paragraph(buffer: list[str], story: list):
    if not buffer:
        return
    text = " ".join(line.strip() for line in buffer if line.strip())
    if text:
        story.append(Paragraph(clean_inline(text), STYLES["BodyCN"]))
    buffer.clear()


def markdown_to_flowables(path: Path) -> list:
    lines = path.read_text(encoding="utf-8").splitlines()
    story = []
    para: list[str] = []
    in_code = False
    code_lang = ""
    code_lines: list[str] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_code:
                flush_paragraph(para, story)
                in_code = True
                code_lang = stripped[3:].strip()
                code_lines = []
            else:
                text = "\n".join(code_lines)
                if code_lang == "mermaid":
                    if path.name not in SUPPRESS_MERMAID_DOCS:
                        story.append(DiagramFlowable(text))
                        story.append(Spacer(1, 6))
                elif code_lang == "text" and path.name in SUPPRESS_TEXT_WIREFRAME_DOCS:
                    pass
                else:
                    title = "Low-fidelity wireframe" if code_lang == "text" else code_lang if code_lang else None
                    font_size = 6.2 if any(len(x) > 82 for x in code_lines) else 6.8
                    story.append(CodeBlock(text, title, font_size=font_size))
                    story.append(Spacer(1, 6))
                in_code = False
                code_lang = ""
                code_lines = []
            idx += 1
            continue
        if in_code:
            code_lines.append(line)
            idx += 1
            continue

        table = parse_table(lines, idx)
        if table:
            flush_paragraph(para, story)
            rows, idx = table
            if rows:
                story.append(make_table(rows))
                story.append(Spacer(1, 7))
            continue

        if not stripped:
            flush_paragraph(para, story)
            idx += 1
            continue

        # Markdown image links are kept for repo/Markdown readers. The PDF
        # places curated figures through FIGURES_BY_DOC to control pagination.
        if re.match(r"^!\[[^\]]*\]\([^)]+\)", stripped):
            flush_paragraph(para, story)
            idx += 1
            continue

        if stripped.startswith("# "):
            flush_paragraph(para, story)
            # The first H1 in each file is represented by a section divider.
            idx += 1
            continue
        if stripped.startswith("## "):
            flush_paragraph(para, story)
            story.append(Paragraph(clean_inline(stripped[3:]), STYLES["H2"]))
            idx += 1
            continue
        if stripped.startswith("### "):
            flush_paragraph(para, story)
            story.append(Paragraph(clean_inline(stripped[4:]), STYLES["H3"]))
            idx += 1
            continue
        if re.match(r"^[-*]\s+", stripped):
            flush_paragraph(para, story)
            item = re.sub(r"^[-*]\s+", "", stripped)
            story.append(Paragraph(clean_inline(item), STYLES["BulletCN"], bulletText="•"))
            idx += 1
            continue
        if re.match(r"^\d+\.\s+", stripped):
            flush_paragraph(para, story)
            item = re.sub(r"^\d+\.\s+", "", stripped)
            story.append(Paragraph(clean_inline(item), STYLES["BulletCN"], bulletText="•"))
            idx += 1
            continue

        para.append(line)
        idx += 1
    flush_paragraph(para, story)
    return story


def title_from_doc(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return plain_text(line[2:])
    return path.stem


def subtitle_for_doc(name: str) -> str:
    mapping = {
        "00_product_overview.md": "产品定位、使命愿景、边界和竞品差异",
        "01_prd.md": "用户、场景、功能范围、MVP边界和验收标准",
        "02_system_architecture.md": "模块职责、系统关系、数据流、部署与集成",
        "03_data_model_fhir.md": "FHIR风格实体、关系、证据引用和数据质量标记",
        "04_pathway_engine.md": "Care Pathway配置、规则、模板、审批和版本管理",
        "05_ai_agents.md": "多Agent职责、输入输出、工具、Prompt原则和降级策略",
        "06_safety_and_governance.md": "医疗安全、证据链、急症流程、人工审批和审计",
        "07_workbench_and_patient_app.md": "医生端、护士端、患者端页面逻辑",
        "08_demo_script.md": "比赛演示故事线、演示数据、讲解词和安全样例",
        "09_pitch_and_defense.md": "开题、创新点、商业价值、答辩问答",
        "10_roadmap.md": "比赛原型、MVP、医院试点、部署版和商业版路线",
        "11_wireframes_and_visuals.md": "图表索引和低保真页面线框图",
    }
    return mapping.get(name, "")


def build_story() -> list:
    story = [CoverPage(CONTENT_W, PAGE_H - TOP - BOTTOM), PageBreak()]
    story.append(Paragraph("目录", STYLES["DocTitle"]))
    toc_rows = [["章节", "内容"]]
    for doc in ORDERED_DOCS:
        p = DOCS / doc
        toc_rows.append([title_from_doc(p), subtitle_for_doc(doc)])
    story.append(make_table(toc_rows))
    story.append(PageBreak())

    for i, doc in enumerate(ORDERED_DOCS):
        p = DOCS / doc
        if i > 0:
            story.append(PageBreak())
        story.append(SectionDivider(title_from_doc(p), subtitle_for_doc(doc)))
        story.append(Spacer(1, 10))
        for image_name, caption in FIGURES_BY_DOC.get(doc, []):
            story.append(make_figure(image_name, caption))
        story.extend(markdown_to_flowables(p))
    return story


def on_page(canvas, doc):
    page = canvas.getPageNumber()
    canvas.saveState()
    if page > 1:
        canvas.setStrokeColor(BORDER)
        canvas.line(LEFT, PAGE_H - 12 * mm, PAGE_W - RIGHT, PAGE_H - 12 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont(FONT, 7.5)
        canvas.drawString(LEFT, PAGE_H - 9 * mm, "AI Native Doctor Copilot · Product & System Design")
        canvas.drawRightString(PAGE_W - RIGHT, 9 * mm, f"Page {page}")
    canvas.restoreState()


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=A4,
        leftMargin=LEFT,
        rightMargin=RIGHT,
        topMargin=TOP,
        bottomMargin=BOTTOM,
        title="AI Native Doctor Copilot Product and System Design",
        author="ContinuCare Copilot Team",
    )
    story = build_story()
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    print(OUT)


if __name__ == "__main__":
    main()
