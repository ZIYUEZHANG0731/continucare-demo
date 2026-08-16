#!/usr/bin/env python3
"""Build a polished, fully self-contained HTML submission from Markdown.

Only Python's standard library is used. Local Markdown images are converted to
data URIs so the generated HTML can be shared as a single offline file.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
LIST_RE = re.compile(r"^(\s*)([-+*]|\d+\.)\s+(.+)$")
TABLE_SEPARATOR_RE = re.compile(r"^:?-{3,}:?$")


@dataclass
class Heading:
    level: int
    text: str
    anchor: str


def plain_text(value: str) -> str:
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    return value.strip()


def inline_markup(value: str) -> str:
    escaped = html.escape(value, quote=False)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(
        r"(?<!\*)\*([^*]+)\*(?!\*)",
        r"<em>\1</em>",
        escaped,
    )
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        r'<a href="\2" target="_blank" rel="noreferrer">\1</a>',
        escaped,
    )
    return escaped


def slugify(value: str, used: set[str]) -> str:
    cleaned = plain_text(value).lower()
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "-", cleaned, flags=re.UNICODE).strip("-")
    if not cleaned:
        cleaned = "section"
    candidate = cleaned
    index = 2
    while candidate in used:
        candidate = f"{cleaned}-{index}"
        index += 1
    used.add(candidate)
    return candidate


def table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_table_separator(line: str) -> bool:
    cells = table_cells(line)
    return bool(cells) and all(TABLE_SEPARATOR_RE.fullmatch(cell or "") for cell in cells)


def image_data_uri(markdown_dir: Path, target: str) -> tuple[str, Path | None]:
    raw = target.strip()
    if raw.startswith(("http://", "https://", "data:")):
        return raw, None
    path = (markdown_dir / unquote(raw)).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Markdown image does not exist: {path}")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}", path


def is_special(lines: list[str], index: int) -> bool:
    stripped = lines[index].strip()
    if not stripped:
        return True
    if stripped.startswith("```") or stripped == "---":
        return True
    if HEADING_RE.match(stripped) or IMAGE_RE.match(stripped):
        return True
    if stripped.startswith(">") or LIST_RE.match(lines[index]):
        return True
    return index + 1 < len(lines) and stripped.startswith("|") and is_table_separator(lines[index + 1])


def render_blocks(
    lines: list[str],
    markdown_dir: Path,
    used_anchors: set[str],
    *,
    skip_h1: bool = False,
) -> tuple[str, list[Heading], list[Path]]:
    chunks: list[str] = []
    headings: list[Heading] = []
    embedded_paths: list[Path] = []
    index = 0

    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            language = stripped[3:].strip()
            code: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            index += 1
            label = f'<span class="code-language">{html.escape(language)}</span>' if language else ""
            chunks.append(
                f'<div class="code-block">{label}<pre><code>{html.escape(chr(10).join(code))}</code></pre></div>'
            )
            continue

        heading_match = HEADING_RE.match(stripped)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2)
            if level == 1 and skip_h1:
                index += 1
                continue
            anchor = slugify(title, used_anchors)
            headings.append(Heading(level, plain_text(title), anchor))
            chunks.append(
                f'<h{level} id="{html.escape(anchor)}">{inline_markup(title)}'
                f'<a class="heading-anchor" href="#{html.escape(anchor)}" aria-label="链接到本节">#</a>'
                f'</h{level}>'
            )
            index += 1
            continue

        if stripped == "---":
            chunks.append('<hr class="section-divider">')
            index += 1
            continue

        image_match = IMAGE_RE.match(stripped)
        if image_match:
            alt, target = image_match.groups()
            uri, source_path = image_data_uri(markdown_dir, target)
            if source_path:
                embedded_paths.append(source_path)
            caption = ""
            consumed = 1
            probe = index + 1
            if probe < len(lines) and not lines[probe].strip():
                probe += 1
            if probe < len(lines):
                caption_match = re.fullmatch(r"\*([^*]+)\*", lines[probe].strip())
                if caption_match:
                    caption = caption_match.group(1)
                    consumed = probe - index + 1
            diagram_class = " diagram" if "report-diagrams" in target else " screenshot"
            digest = hashlib.sha1(target.encode("utf-8")).hexdigest()[:8]
            figure = [f'<figure class="document-figure{diagram_class}" id="figure-{digest}">']
            figure.append(
                f'<img src="{uri}" alt="{html.escape(alt, quote=True)}" '
                f'decoding="async">'
            )
            if caption:
                figure.append(f'<figcaption>{inline_markup(caption)}</figcaption>')
            figure.append("</figure>")
            chunks.append("".join(figure))
            index += consumed
            continue

        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while index < len(lines):
                current = lines[index].strip()
                if not current.startswith(">"):
                    break
                content = current[1:]
                if content.startswith(" "):
                    content = content[1:]
                quote_lines.append(content)
                index += 1
            inner, nested_headings, nested_images = render_blocks(
                quote_lines, markdown_dir, used_anchors, skip_h1=False
            )
            headings.extend(nested_headings)
            embedded_paths.extend(nested_images)
            chunks.append(f'<aside class="callout" role="note">{inner}</aside>')
            continue

        if index + 1 < len(lines) and stripped.startswith("|") and is_table_separator(lines[index + 1]):
            header = table_cells(lines[index])
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(table_cells(lines[index]))
                index += 1
            table = ['<div class="table-wrap"><table><thead><tr>']
            table.extend(f"<th>{inline_markup(cell)}</th>" for cell in header)
            table.append("</tr></thead><tbody>")
            for row in rows:
                table.append("<tr>")
                padded = row + [""] * max(0, len(header) - len(row))
                table.extend(f"<td>{inline_markup(cell)}</td>" for cell in padded[: len(header)])
                table.append("</tr>")
            table.append("</tbody></table></div>")
            chunks.append("".join(table))
            continue

        list_match = LIST_RE.match(raw)
        if list_match:
            ordered = list_match.group(2).endswith(".") and list_match.group(2)[0].isdigit()
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            checklist = False
            while index < len(lines):
                match = LIST_RE.match(lines[index])
                if not match:
                    break
                current_ordered = match.group(2).endswith(".") and match.group(2)[0].isdigit()
                if current_ordered != ordered:
                    break
                content = match.group(3).strip()
                checkbox = re.match(r"\[([ xX])\]\s*(.*)", content)
                if checkbox:
                    checklist = True
                    checked = checkbox.group(1).lower() == "x"
                    marker = '<span class="checkbox checked">✓</span>' if checked else '<span class="checkbox"></span>'
                    items.append(f"<li>{marker}{inline_markup(checkbox.group(2))}</li>")
                else:
                    items.append(f"<li>{inline_markup(content)}</li>")
                index += 1
            class_name = ' class="checklist"' if checklist else ""
            chunks.append(f"<{tag}{class_name}>{''.join(items)}</{tag}>")
            continue

        paragraph: list[str] = []
        while index < len(lines) and not is_special(lines, index):
            paragraph.append(lines[index].strip())
            index += 1
        if paragraph:
            chunks.append(f"<p>{inline_markup(' '.join(paragraph))}</p>")
            continue

        index += 1

    return "\n".join(chunks), headings, embedded_paths


def toc_html(headings: list[Heading]) -> str:
    links: list[str] = []
    for heading in headings:
        if heading.level not in (2, 3):
            continue
        child = " toc-child" if heading.level == 3 else ""
        links.append(
            f'<a class="toc-link{child}" href="#{html.escape(heading.anchor)}">'
            f'{html.escape(heading.text)}</a>'
        )
    return "\n".join(links)


def build_html(markdown_path: Path, output_path: Path) -> tuple[int, list[Path]]:
    markdown = markdown_path.read_text(encoding="utf-8")
    lines = markdown.splitlines()
    title = next(
        (plain_text(match.group(2)) for line in lines if (match := HEADING_RE.match(line.strip())) and len(match.group(1)) == 1),
        markdown_path.stem,
    )
    body, headings, images = render_blocks(lines, markdown_path.parent, set(), skip_h1=True)
    timestamp = datetime.now().strftime("%Y-%m-%d")

    page = f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --brand: #006d70;
      --brand-strong: #004f52;
      --brand-soft: #e8f4f4;
      --ink: #172126;
      --muted: #5e6b70;
      --line: #d6dee0;
      --surface: #ffffff;
      --canvas: #f4f7f7;
      --navy: #11182b;
      --amber: #d97706;
      --danger: #b42318;
      --shadow: 0 18px 45px rgba(23, 33, 38, .09);
      --content: 1060px;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--canvas);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
      font-size: 17px;
      line-height: 1.78;
      text-rendering: optimizeLegibility;
      -webkit-font-smoothing: antialiased;
    }}
    a {{ color: var(--brand-strong); }}
    .progress {{ position: fixed; inset: 0 auto auto 0; height: 3px; width: 0; background: var(--brand); z-index: 20; }}
    .topbar {{
      position: sticky; top: 0; z-index: 10;
      display: flex; align-items: center; justify-content: space-between;
      min-height: 58px; padding: 8px 28px;
      color: #fff; background: rgba(17, 24, 43, .96);
      backdrop-filter: blur(12px);
    }}
    .topbar-brand {{ font-weight: 700; letter-spacing: .02em; }}
    .topbar-meta {{ color: rgba(255,255,255,.68); font-size: 13px; }}
    .print-button {{
      border: 1px solid rgba(255,255,255,.3); border-radius: 999px;
      color: #fff; background: transparent; padding: 7px 14px;
      font: inherit; font-size: 13px; cursor: pointer;
    }}
    .hero {{
      position: relative; overflow: hidden;
      color: #fff; background: linear-gradient(135deg, #004f52 0%, #006d70 55%, #0a7d78 100%);
      padding: 86px max(32px, calc((100vw - 1200px) / 2)) 78px;
    }}
    .hero::after {{
      content: ""; position: absolute; width: 480px; height: 480px; right: -120px; top: -220px;
      border: 1px solid rgba(255,255,255,.16); border-radius: 50%; box-shadow: 0 0 0 72px rgba(255,255,255,.035), 0 0 0 144px rgba(255,255,255,.025);
    }}
    .hero-kicker {{ position: relative; z-index: 1; font-size: 14px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; opacity: .78; }}
    .hero h1 {{ position: relative; z-index: 1; max-width: 1120px; margin: 20px 0 22px; font-size: clamp(36px, 5vw, 64px); line-height: 1.18; letter-spacing: -.035em; }}
    .hero-summary {{ position: relative; z-index: 1; max-width: 820px; margin: 0; font-size: 20px; color: rgba(255,255,255,.8); }}
    .hero-tags {{ position: relative; z-index: 1; display: flex; gap: 10px; flex-wrap: wrap; margin-top: 30px; }}
    .hero-tag {{ border: 1px solid rgba(255,255,255,.22); border-radius: 999px; padding: 5px 12px; font-size: 13px; color: rgba(255,255,255,.86); }}
    .layout {{ display: grid; grid-template-columns: 250px minmax(0, var(--content)); gap: 38px; justify-content: center; align-items: start; padding: 46px 28px 100px; }}
    .toc {{ position: sticky; top: 84px; max-height: calc(100vh - 110px); overflow: auto; padding: 18px; border: 1px solid var(--line); border-radius: 18px; background: rgba(255,255,255,.82); box-shadow: 0 10px 28px rgba(23,33,38,.05); }}
    .toc-title {{ margin: 0 0 12px; color: var(--muted); font-size: 12px; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }}
    .toc-link {{ display: block; margin: 2px 0; padding: 7px 10px; border-radius: 9px; color: var(--ink); font-size: 14px; line-height: 1.4; text-decoration: none; }}
    .toc-link:hover, .toc-link.active {{ color: var(--brand-strong); background: var(--brand-soft); }}
    .toc-child {{ padding-left: 22px; color: var(--muted); font-size: 13px; }}
    main {{ min-width: 0; padding: 42px 54px 68px; border: 1px solid rgba(214,222,224,.9); border-radius: 24px; background: var(--surface); box-shadow: var(--shadow); }}
    h2, h3, h4 {{ position: relative; scroll-margin-top: 82px; color: var(--ink); }}
    h2 {{ margin: 70px 0 24px; padding-top: 10px; font-size: 34px; line-height: 1.35; letter-spacing: -.02em; }}
    h2:first-child {{ margin-top: 0; }}
    h2::before {{ content: ""; display: block; width: 48px; height: 5px; margin-bottom: 16px; border-radius: 9px; background: var(--brand); }}
    h3 {{ margin: 48px 0 18px; font-size: 26px; line-height: 1.4; }}
    h4 {{ margin: 34px 0 12px; font-size: 20px; line-height: 1.5; color: var(--brand-strong); }}
    .heading-anchor {{ margin-left: 8px; color: var(--line); font-size: .7em; text-decoration: none; opacity: 0; }}
    h2:hover .heading-anchor, h3:hover .heading-anchor, h4:hover .heading-anchor {{ opacity: 1; }}
    p {{ margin: 14px 0; }}
    strong {{ color: var(--ink); font-weight: 750; }}
    em {{ color: var(--muted); }}
    code {{ padding: .12em .38em; border-radius: 6px; color: #8d2b20; background: #fff1ee; font-family: "SFMono-Regular", Consolas, monospace; font-size: .88em; overflow-wrap: anywhere; }}
    ul, ol {{ margin: 14px 0 22px; padding-left: 1.55em; }}
    li {{ margin: 7px 0; padding-left: .2em; }}
    li::marker {{ color: var(--brand); font-weight: 700; }}
    .checklist {{ padding-left: 0; list-style: none; }}
    .checklist li {{ display: flex; gap: 10px; align-items: flex-start; }}
    .checkbox {{ flex: 0 0 18px; width: 18px; height: 18px; margin-top: .37em; border: 1.5px solid #aab5b8; border-radius: 5px; line-height: 16px; text-align: center; font-size: 12px; }}
    .checkbox.checked {{ color: #fff; border-color: var(--brand); background: var(--brand); }}
    .callout {{ margin: 24px 0 30px; padding: 20px 24px; border: 1px solid #f0c36d; border-left: 5px solid var(--amber); border-radius: 14px; background: #fff9ed; }}
    .callout p:first-child {{ margin-top: 0; }} .callout p:last-child, .callout ul:last-child {{ margin-bottom: 0; }}
    .section-divider {{ height: 1px; margin: 52px 0; border: 0; background: linear-gradient(90deg, transparent, var(--line) 10%, var(--line) 90%, transparent); }}
    .table-wrap {{ overflow-x: auto; margin: 24px 0 34px; border: 1px solid var(--line); border-radius: 14px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; line-height: 1.58; }}
    th {{ padding: 13px 15px; color: #fff; background: var(--brand-strong); text-align: left; font-weight: 700; }}
    td {{ min-width: 130px; padding: 12px 15px; border-top: 1px solid var(--line); vertical-align: top; }}
    tbody tr:nth-child(even) {{ background: #f8fafa; }}
    .document-figure {{ margin: 30px 0 34px; }}
    .document-figure img {{ display: block; width: 100%; height: auto; border: 1px solid var(--line); border-radius: 15px; background: #fff; box-shadow: 0 10px 28px rgba(23,33,38,.08); }}
    .document-figure.diagram img {{ border-radius: 18px; }}
    figcaption {{ max-width: 850px; margin: 11px auto 0; color: var(--muted); font-size: 13px; line-height: 1.6; text-align: center; }}
    .code-block {{ position: relative; margin: 22px 0 30px; }}
    .code-language {{ position: absolute; top: 10px; right: 12px; z-index: 1; color: #8e9aa0; font: 11px/1.2 monospace; text-transform: uppercase; }}
    pre {{ overflow: auto; margin: 0; padding: 22px; border-radius: 14px; color: #e8eeee; background: var(--navy); font: 13px/1.65 "SFMono-Regular", Consolas, monospace; }}
    pre code {{ padding: 0; color: inherit; background: transparent; }}
    .footer {{ padding: 28px; color: rgba(255,255,255,.62); background: var(--navy); text-align: center; font-size: 13px; }}
    @media (max-width: 1050px) {{
      .layout {{ grid-template-columns: minmax(0, var(--content)); }}
      .toc {{ position: static; max-height: none; columns: 2; }}
      .toc-title {{ column-span: all; }}
    }}
    @media (max-width: 720px) {{
      body {{ font-size: 16px; }} .topbar {{ padding: 8px 16px; }} .topbar-meta {{ display: none; }}
      .hero {{ padding: 60px 22px 52px; }} .hero h1 {{ font-size: 34px; }} .hero-summary {{ font-size: 17px; }}
      .layout {{ padding: 22px 12px 64px; gap: 18px; }} .toc {{ columns: 1; }}
      main {{ padding: 30px 20px 48px; border-radius: 18px; }} h2 {{ font-size: 28px; }} h3 {{ font-size: 23px; }}
      .document-figure {{ margin-left: -10px; margin-right: -10px; }}
    }}
    @media print {{
      @page {{ size: A4; margin: 15mm 14mm 17mm; }}
      * {{ -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }}
      body {{ background: #fff; font-size: 10.5pt; }} .progress, .topbar, .toc, .print-button {{ display: none !important; }}
      .hero {{ padding: 24mm 15mm 20mm; page-break-after: always; }} .hero h1 {{ font-size: 28pt; }}
      .layout {{ display: block; padding: 0; }} main {{ padding: 0; border: 0; border-radius: 0; box-shadow: none; }}
      h2 {{ break-before: page; margin-top: 0; padding-top: 0; font-size: 20pt; }} h2:first-child {{ break-before: auto; }}
      h3, h4 {{ break-after: avoid; }} p, li {{ orphans: 3; widows: 3; }}
      .document-figure, .table-wrap, .callout, .code-block {{ break-inside: avoid; }}
      .document-figure img {{ box-shadow: none; }}
      .footer {{ display: none; }}
    }}
  </style>
</head>
<body>
  <div class="progress" id="progress"></div>
  <header class="topbar">
    <div class="topbar-brand">ContinuCare Copilot</div>
    <div class="topbar-meta">40强赛参赛方案 · 单文件离线版</div>
    <button class="print-button" type="button" onclick="window.print()">打印 / 导出 PDF</button>
  </header>
  <section class="hero">
    <div class="hero-kicker">United Family Healthcare Challenge · Submission</div>
    <h1>{html.escape(title)}</h1>
    <p class="hero-summary">让院外变化成为医生复诊前可信、可追溯、可复用的连续上下文。</p>
    <div class="hero-tags">
      <span class="hero-tag">确定性事实主链</span><span class="hero-tag">受控 AI 辅助</span>
      <span class="hero-tag">患者与医护人工确认</span><span class="hero-tag">FHIR 证据链</span>
    </div>
  </section>
  <div class="layout">
    <nav class="toc" aria-label="文档目录">
      <div class="toc-title">Contents · 目录</div>
      {toc_html(headings)}
    </nav>
    <main>{body}</main>
  </div>
  <footer class="footer">ContinuCare Copilot · 生成日期 {timestamp} · 图片已全部内嵌，可离线打开</footer>
  <script>
    const progress = document.getElementById('progress');
    const updateProgress = () => {{
      const max = document.documentElement.scrollHeight - innerHeight;
      progress.style.width = (max > 0 ? (scrollY / max) * 100 : 0) + '%';
    }};
    addEventListener('scroll', updateProgress, {{passive: true}}); updateProgress();
    const links = [...document.querySelectorAll('.toc-link')];
    const byId = new Map(links.map(link => [link.hash.slice(1), link]));
    const observer = new IntersectionObserver(entries => {{
      entries.filter(entry => entry.isIntersecting).forEach(entry => {{
        links.forEach(link => link.classList.remove('active'));
        byId.get(entry.target.id)?.classList.add('active');
      }});
    }}, {{rootMargin: '-18% 0px -72% 0px'}});
    document.querySelectorAll('h2[id], h3[id]').forEach(node => observer.observe(node));
  </script>
</body>
</html>'''
    output_path.write_text(page, encoding="utf-8")
    return len(headings), images


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("markdown", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    markdown_path = args.markdown.resolve()
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    heading_count, images = build_html(markdown_path, output_path)
    print(f"Wrote {output_path}")
    print(f"Embedded images: {len(images)}")
    print(f"Navigation headings: {heading_count}")


if __name__ == "__main__":
    main()
