#!/usr/bin/env python3
"""Render a product clip with FFmpeg; no LLM, API key, or model required."""

from __future__ import annotations

import argparse
from datetime import datetime
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time
import unicodedata
import uuid

from typography import normalize_style
import output_storage
from premium_fonts import PRIVATE_FONT_FILES


ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE = ROOT / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
PRIVATE_FONTS = {
    "oswald": (ROOT / "assets/fonts/Oswald/Oswald-Regular.ttf", ROOT / "assets/fonts/Oswald/Oswald-SemiBold.ttf"),
    "source han sans cn": (
        ROOT / "assets/fonts/SourceHanSans/SourceHanSansCN-Regular.otf",
        ROOT / "assets/fonts/SourceHanSans/SourceHanSansCN-Bold.otf"),
    "source han serif cn": (ROOT / "assets/fonts/SourceHanSerif/SourceHanSerifCN-Regular.otf", ROOT / "assets/fonts/SourceHanSerif/SourceHanSerifCN-SemiBold.otf"),
    "lxgw wenkai": (ROOT / "assets/fonts/LXGWWenKai/LXGWWenKai-Regular.ttf", ROOT / "assets/fonts/LXGWWenKai/LXGWWenKai-Medium.ttf"),
    "montserrat": (ROOT / "assets/fonts/Montserrat/Montserrat-Regular.ttf", ROOT / "assets/fonts/Montserrat/Montserrat-Bold.ttf"),
}
PRIVATE_FONTS.update({family:tuple(ROOT/path for path in paths) for family,paths in PRIVATE_FONT_FILES.items()})


def say(message: str) -> None:
    print(message, flush=True)


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def resolve_input(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def probe(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"File not found: {path}")
    result = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        timeout=90,
    )
    if result.returncode:
        raise RuntimeError(f"Cannot inspect media: {result.stderr[-2000:]}")
    return json.loads(result.stdout)


def stream(info: dict, kind: str) -> dict | None:
    return next((item for item in info.get("streams", []) if item.get("codec_type") == kind), None)


def duration(info: dict, kind: str = "video") -> float:
    selected = stream(info, kind) or {}
    for value in (selected.get("duration"), info.get("format", {}).get("duration")):
        try:
            number = float(value)
            if math.isfinite(number) and number > 0:
                return number
        except (ValueError, TypeError):
            pass
    raise ValueError("Media has no valid duration.")


def choose_segments(config: dict, available: float) -> list[tuple[float, float]]:
    maximum = float(config.get("max_duration_seconds", 90))
    if not math.isfinite(maximum) or not 0.1 <= maximum <= 90:
        raise ValueError("max_duration_seconds must be between 0.1 and 90.")
    requested = config.get("segments") or [{"start": 0, "end": None}]
    if not isinstance(requested, list) or len(requested) > 100:
        raise ValueError("segments must be a list of at most 100 {start, end} objects.")
    selected = []
    budget = maximum
    for part in requested:
        start = float(part.get("start", 0))
        end = available if part.get("end") is None else float(part["end"])
        if not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end <= start:
            raise ValueError(f"Invalid segment: {part}")
        if start >= available:
            raise ValueError(f"Segment begins after source ends ({available:.3f} seconds): {part}")
        end = min(end, available, start + budget)
        if end - start >= 0.05:
            selected.append((start, end))
            budget -= end - start
        if budget < 0.05:
            break
    if not selected:
        raise ValueError("No usable segment remains.")
    return selected


def ass_text(value: str) -> str:
    # Do not allow text from a sheet/config to become ASS formatting instructions.
    return str(value).replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\r", "").replace("\n", r"\N")


def estimated_width(value: str) -> float:
    return sum(1 if unicodedata.east_asian_width(char) in "WF" else 0.56 for char in value)


def fitted_size(value: str, desired: int, minimum: int, max_width: int = 980) -> int:
    longest = max((estimated_width(line) for line in value.splitlines()), default=1)
    size = min(desired, int(max_width / max(longest, 1)))
    if size < minimum:
        raise ValueError(f"Overlay text is too long; shorten this line: {value}")
    return size


@lru_cache(maxsize=32)
def metric_font(font: str, size: int, bold: bool):
    """Use installed font metrics when Pillow is available; rendering needs no Pillow."""
    filenames = {
        "microsoft yahei": "msyhbd.ttc" if bold else "msyh.ttc",
        "microsoft yahei ui": "msyhbd.ttc" if bold else "msyh.ttc",
        "dengxian": "Dengb.ttf" if bold else "Deng.ttf",
        "bahnschrift": "bahnschrift.ttf",
    }
    filename = filenames.get(font.lower())
    path = PRIVATE_FONTS[font.lower()][int(bold)] if font.lower() in PRIVATE_FONTS else (
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / filename if filename else None)
    if path:
        try:
            from PIL import ImageFont
            return ImageFont.truetype(str(path), size)
        except (ImportError, OSError):
            pass
    return None


@lru_cache(maxsize=8)
def ass_size_scale(font: str, bold: bool = False) -> float:
    """ASS sizes use Windows line metrics; Pillow sizes use the font em square."""
    paths = PRIVATE_FONTS.get(font.lower())
    if paths:
        raw = paths[int(bold)].read_bytes()
        tables = {}
        for index in range(struct.unpack_from('>H', raw, 4)[0]):
            tag, _, offset, length = struct.unpack_from('>4sIII', raw, 12 + 16 * index)
            tables[tag] = raw[offset:offset + length]
        units = struct.unpack_from('>H', tables[b'head'], 18)[0]
        ascent, descent = struct.unpack_from('>HH', tables[b'OS/2'], 74)
        return units / (ascent + descent)
    return 1.0


@lru_cache(maxsize=16)
def render_family(font: str, bold: bool) -> str:
    paths=PRIVATE_FONTS.get(font.lower())
    if not paths:
        return font
    raw=paths[int(bold)].read_bytes()
    for index in range(struct.unpack_from('>H',raw,4)[0]):
        tag,_,offset,length=struct.unpack_from('>4sIII',raw,12+16*index)
        if tag!=b'name':
            continue
        table=raw[offset:offset+length]
        _,count,base=struct.unpack_from('>HHH',table)
        for item in range(count):
            platform,_,language,nameid,size,start=struct.unpack_from('>HHHHHH',table,6+12*item)
            if platform==3 and language==0x409 and nameid==1:
                return table[base+start:base+start+size].decode('utf-16-be')
    return font


def text_width(value: str, size: int, font: str, bold: bool = False) -> float:
    if font.lower() in {"oswald","montserrat"} and re.search(r'[\u2e80-\u9fff\uac00-\ud7af\uff00-\uffef]', value):
        return sum(text_width(part, size, "Source Han Sans CN" if re.search(
            r'[\u2e80-\u9fff\uac00-\ud7af\uff00-\uffef]', part) else font, bold)
            for part in re.split(r'([\u2e80-\u9fff\uac00-\ud7af\uff00-\uffef]+)', value) if part)
    measured = metric_font(font, size, bold)
    if measured is not None:
        return float(measured.getlength(value)) * ass_size_scale(font, bold)
    # Conservative fallback leaves room for wide Latin capitals and punctuation.
    return sum(1 if unicodedata.east_asian_width(char) in "WF" else
               0.82 if char.isupper() else 0.65 for char in value) * size


def wrap_overlay(value: str, size: int, font: str, max_lines: int,
                 bold: bool = False, balance: bool = False, max_width: int = 956) -> tuple[list[str], bool]:
    """Wrap whole Latin words and CJK text without shrinking it into unreadable type."""
    value = re.sub(r"\s+", " ", str(value)).strip()
    if not value:
        return [], False
    # Keep model identifiers together whenever they fit on a full line.
    raw_tokens = re.findall(r"[A-Za-z0-9]+(?:[.'’_/-][A-Za-z0-9]+)*|\s+|.", value)
    tokens = []
    for token in raw_tokens:
        tokens.extend(list(token) if text_width(token, size, font, bold) > max_width else [token])
    if balance and max_lines == 2 and text_width(value, size, font, bold) > max_width:
        candidates = []
        for split in range(1, len(tokens)):
            left, right = "".join(tokens[:split]).strip(), "".join(tokens[split:]).strip()
            if not left or not right or right[0] in "，。；：、？！,.!?;:｜·":
                continue
            widths = [text_width(line, size, font, bold) for line in (left, right)]
            if max(widths) <= max_width:
                # Favor an English model / Chinese descriptor split over midphrase.
                clean_boundary = bool(re.search(r"[A-Za-z0-9]$", left) and re.match(r"[\u3400-\u9fff]", right))
                score = abs(widths[0] - widths[1]) - (size * 3 if clean_boundary else 0)
                candidates.append((score, [left, right]))
        if candidates:
            return min(candidates, key=lambda item: item[0])[1], False
    lines = []
    current = ""
    for token in tokens:
        candidate = (current + token).lstrip()
        if current and text_width(candidate.rstrip(), size, font, bold) > max_width:
            lines.append(current.rstrip())
            current = token.lstrip()
        else:
            current = candidate
    if current.strip():
        lines.append(current.rstrip())
    clipped = len(lines) > max_lines
    lines = lines[:max_lines]
    if clipped and lines:
        last = lines[-1].rstrip("，。；：、,.!?;:｜· ")
        while last and text_width(last + "…", size, font, bold) > max_width:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return lines, clipped


def ass_timestamp(seconds: float) -> str:
    ticks = int(math.ceil(seconds * 100))
    hours, ticks = divmod(ticks, 360000)
    minutes, ticks = divmod(ticks, 6000)
    whole, fraction = divmod(ticks, 100)
    return f"{hours}:{minutes:02}:{whole:02}.{fraction:02}"


def make_overlay(config: dict, seconds: float, layout_info: dict | None = None) -> str:
    style=normalize_style(config['typography']) if 'typography' in config else {}
    font = str(config.get("font", "Microsoft YaHei"))
    brand_font = str(style.get('brand_font',config.get("brand_font", "Bahnschrift")))
    title_font = str(style.get('title_font',config.get("title_font", font)))
    description_font = str(style.get('description_font',config.get("description_font", font)))
    brand_bold=style.get('brand_bold',True)
    title_bold=style.get('title_bold',True)
    description_bold=style.get('description_bold',False)
    boxes={}
    if any(char in name for name in (font, brand_font, title_font, description_font) for char in ",\r\n{}\\"):
        raise ValueError("Invalid font name.")
    text_scale = float(config.get("text_scale", 1.0))
    if not math.isfinite(text_scale) or not 0.9 <= text_scale <= 1.25:
        raise ValueError("text_scale must be between 0.9 and 1.25.")
    brand = str(config.get("brand_title", ""))
    title = str(config.get("product_title", ""))
    descriptions = config.get("description_lines", [])
    if not isinstance(descriptions, list) or len(descriptions) > 3:
        raise ValueError("Use at most three description_lines.")
    brand = re.sub(r"\s+", " ", brand).strip()
    title = re.sub(r"\s+", " ", title).strip()
    if not brand or not title:
        raise ValueError("brand_title and product_title are required.")
    end = ass_timestamp(seconds + 1)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1440
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Text,{font},44,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,40,40,0,1
Style: Speech,Source Han Sans CN,66,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2.4,0,2,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    def line(text: str, y: int, size: int, bold: bool = False,
             family: str = font, color: str = "FFFFFF", spacing: int = 0) -> str:
        # libass/DirectWrite uses this static font's Windows family name (ID 1),
        # not its typographic family (ID 16). Requesting plain Oswald falls back.
        face = render_family(family,bold)
        weight = r"\b600}" if bold and family.lower() == 'oswald' else (r"\b1}" if bold else r"\b0}")
        tags = (r"{\an5\pos(540," + str(y) + r")\fs" + str(size) +
                r"\fn" + face + r"\c&H" + color + r"&\fsp" + str(spacing) +
                weight)
        content = ass_text(text)
        if family.lower() in {'oswald','montserrat'}:
            content = re.sub(r'[\u2e80-\u9fff\uac00-\ud7af\uff00-\uffef]+',
                lambda match: r'{\fnSource Han Sans CN}' + match[0] + r'{\fn' + face + '}', content)
        return f"Dialogue: 0,0:00:00.00,{end},Text,,0,0,0,,{tags}{content}\n"

    brand_size = round((240 if brand_font.lower() == 'oswald' else 112) * min(text_scale, 1.1)*style.get('brand_scale',1))
    brand_lines, brand_clipped = wrap_overlay(brand, brand_size, brand_font, 2, brand_bold, True)
    if len(brand_lines) > 1 and brand_size * ass_size_scale(brand_font, brand_bold) > 150:
        brand_size = round(150 / ass_size_scale(brand_font, brand_bold))
        brand_lines, brand_clipped = wrap_overlay(brand, brand_size, brand_font, 2, brand_bold, True)
    brand_height=brand_size*ass_size_scale(brand_font,brand_bold)
    brand_block=(1+max(0,len(brand_lines)-1)*1.2)*brand_height
    brand_y=max(40+brand_block/2,min(390-brand_block/2,228+style.get('brand_offset',0)))
    boxes['brand']={'top':brand_y-brand_block/2,'bottom':brand_y+brand_block/2}
    events = ""
    for index, text in enumerate(brand_lines if not config.get('brand_logo') else []):
        events += line(text, round(brand_y + (index - (len(brand_lines) - 1) / 2) * brand_height * 1.2),
                       brand_size, brand_bold, brand_font, color=ass_color(style.get('brand_color','#FFFFFF')), spacing=2)

    # Give the product name the strongest hierarchy. The short category below
    # identifies the item rather than filling the panel with selling points.
    title_size = round((200 if title_font.lower() == 'oswald' else 132) * text_scale*style.get('title_scale',1))
    description_size = round(58 * text_scale*style.get('description_scale',1))
    # The picture occupies y=410..1018. Keep all copy inside the black lower
    # panel, allowing the slider to enlarge sparse copy while dense copy fits.
    for _ in range(400):
        title_lines, title_clipped = wrap_overlay(title, title_size, title_font, 2, title_bold, True)
        description_lines = []
        for text in descriptions:
            wrapped, _ = wrap_overlay(str(text), description_size, description_font, 20,description_bold)
            description_lines.extend(wrapped)
        if len(description_lines) > 3:
            description_lines = description_lines[:3]
            description_lines[-1] = description_lines[-1].rstrip("，。；：、,.!?;:｜· ") + "…"
            while text_width(description_lines[-1], description_size, description_font,description_bold) > 956:
                description_lines[-1] = description_lines[-1][:-2].rstrip() + "…"
        title_height = round(title_size * ass_size_scale(title_font, title_bold))
        description_height = round(description_size * ass_size_scale(description_font,description_bold))
        title_step = round(title_height * 1.2)
        description_step = round(description_height * 1.28)
        gap = 24 if description_lines else 0
        block_height = (len(title_lines) - 1) * title_step + title_height + gap
        if description_lines:
            block_height += (len(description_lines) - 1) * description_step + description_height
        if block_height <= 354:
            break
        title_size = max(24,title_size-1)
        description_size = max(24,description_size-1)
    top = 1046 + max(0, (354 - block_height) / 2)
    title_block=(len(title_lines)-1)*title_step+title_height
    title_top=max(1046,min(1400-title_block,top+style.get('title_offset',0)))
    boxes['title']={'top':title_top,'bottom':title_top+title_block}
    for index, text in enumerate(title_lines):
        events += line(text, round(title_top + title_height / 2 + index * title_step), title_size, title_bold, title_font,color=ass_color(style.get('title_color','#FFFFFF')))
    top += (len(title_lines) - 1) * title_step + title_height + gap
    desc_block=max(0,len(description_lines)-1)*description_step+description_height
    desc_top=max(1046,min(1400-desc_block,top+style.get('description_offset',0)))
    if description_lines:
        boxes['description']={'top':desc_top,'bottom':desc_top+desc_block}
    for index, text in enumerate(description_lines):
        events += line(text, round(desc_top + description_height / 2 + index * description_step), description_size,
                       bold=description_bold,family=description_font, color=ass_color(style.get('description_color','#E6E6E6')))
    if layout_info is not None:
        layout_info.update(boxes=boxes,warnings=[])
        if 'description' in boxes and boxes['title']['bottom']+8>boxes['description']['top']:
            layout_info['warnings'].append('标题与商品类别靠得较近，可以调小文字或调整上下位置。')
        if title_clipped or brand_clipped:
            layout_info['warnings'].append('文案较长，部分文字已省略，建议缩短品牌或标题。')
    if config.get('subtitles_enabled'):
        from speech_subtitles import parse_srt,speech_ass
        speech_layout={}
        events+=speech_ass(parse_srt(config.get('subtitle_srt','')),seconds,style,speech_layout)
        if layout_info is not None and speech_layout.get('subtitle'):
            caption=speech_layout['subtitle']
            boxes['subtitle']=caption
            if any(caption['top']<box['bottom']+8 and caption['bottom']>box['top']-8 for box in
                   (box for name,box in boxes.items() if name!='subtitle')):
                layout_info['warnings'].append('语音字幕可能与品牌、标题或商品类别重叠，请调整字幕上下位置。')
            if speech_layout.get('clipped'):
                layout_info['warnings'].append('语音字幕较长，部分文字会省略，建议在 SRT 中拆成更短的时间段。')
    return header + events


def ass_color(value: str) -> str:
    color=value.lstrip('#')
    return color[4:6]+color[2:4]+color[0:2]


def make_graph(parts: list[tuple[float, float]], audio_index: int | None, fade_out_seconds: float = 1.0, cover_index: int | None = None, cover_hold: float = .6, brand_index: int | None = None) -> str:
    if isinstance(fade_out_seconds,bool) or not isinstance(fade_out_seconds,(int,float)) or not math.isfinite(fade_out_seconds) or not 0<=fade_out_seconds<=3:
        raise ValueError('fade_out_seconds must be between 0 and 3.')
    total=sum(end-start for start,end in parts)
    fade=min(fade_out_seconds,total)
    tail=f',fade=t=out:st={max(0,total-fade):.6f}:d={fade:.6f}:color=black' if fade else ''
    graph = []
    joined = []
    for index, (start, end) in enumerate(parts):
        graph.append(f"[0:v:0]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS[v{index}]")
        joined.append(f"[v{index}]")
        if audio_index is not None:
            graph.append(
                f"[{audio_index}:a:0]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS,"
                f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
            )
            joined.append(f"[a{index}]")
    if len(parts) == 1:
        video_name, audio_name = "v0", "a0"
    else:
        audio_count = 1 if audio_index is not None else 0
        graph.append("".join(joined) + f"concat=n={len(parts)}:v=1:a={audio_count}[vjoin]" + ("[ajoin]" if audio_count else ""))
        video_name, audio_name = "vjoin", "ajoin"
    if cover_index is not None and (not isinstance(cover_hold,(int,float)) or not math.isfinite(cover_hold) or not .034<=cover_hold<=2):raise ValueError('cover_hold must be 0.034–2 seconds.')
    graph.append(
        f"[{video_name}]scale=1080:608:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        "pad=1080:608:(ow-iw)/2:(oh-ih)/2:black,setsar=1,"
        "pad=1080:1440:0:410:black,fps=30,subtitles=overlay.ass:fontsdir=../../../assets/fonts/render"+("[vbrandbase]" if brand_index is not None else "[vbase]" if cover_index is not None else tail+"[vout]")
    )
    if brand_index is not None:
        graph.append(f'[{brand_index}:v:0]format=rgba[brandpic]')
        graph.append('[vbrandbase][brandpic]overlay=0:0:eof_action=repeat'+('[vbase]' if cover_index is not None else tail+'[vout]'))
    if cover_index is not None:
        graph.append(f'[{cover_index}:v:0]scale=1080:1440,setsar=1,format=rgba[coverpic]')
        graph.append(f"[vbase][coverpic]overlay=0:0:enable='lt(t,{min(total,cover_hold):.6f})':eof_action=repeat"+tail+'[vout]')
    if audio_index is not None:
        audio_tail=f'afade=t=out:st={max(0,total-fade):.6f}:d={fade:.6f}' if fade else 'anull'
        graph.append(f"[{audio_name}]{audio_tail}[aout]")
    return ";\n".join(graph) + "\n"


def stage_private_fonts(config: dict) -> None:
    """Give libass a flat, project-private directory; never install into Windows."""
    folder = ROOT / 'assets/fonts/render'
    folder.mkdir(parents=True, exist_ok=True)
    selected = {str(config.get(key, '')).lower() for key in
                ('font', 'title_font', 'brand_font', 'description_font')}
    selected.update(str(config.get('typography',{}).get(key,'')).lower() for key in ('brand_font','title_font','description_font'))
    if config.get('subtitles_enabled'):
        selected.add('source han sans cn')
    if {'oswald','montserrat','inter','manrope','space grotesk','bebas neue','outfit'} & selected:
        selected.add('source han sans cn')
    for family in selected:
        for source in set(PRIVATE_FONTS.get(family, ())):
            if not source.is_file():
                raise FileNotFoundError(f'Required project font is missing: {source}')
            target = folder / source.name
            if not target.is_file() or target.stat().st_size != source.stat().st_size:
                temporary=folder/(source.name+'.'+uuid.uuid4().hex+'.tmp')
                shutil.copy2(source, temporary)
                os.replace(temporary,target)


def run_encode(command: list[str], job_dir: Path, log, seconds: float) -> None:
    log.write("COMMAND: " + subprocess.list2cmdline(command) + "\n\n")
    log.flush()
    process = subprocess.Popen(
        command, cwd=job_dir, stdout=subprocess.PIPE, stderr=log,
        text=True, encoding="utf-8", errors="replace",
    )
    previous = -10
    try:
        assert process.stdout is not None
        for entry in process.stdout:
            if entry.startswith("out_time_us="):
                try:
                    progress = min(99, int(float(entry.partition("=")[2]) / 1_000_000 / seconds * 100))
                    if progress >= previous + 5:
                        say(f"Rendering: {progress}%")
                        previous = progress
                except ValueError:
                    pass
        code = process.wait()
        if code:
            raise RuntimeError(f"FFmpeg failed with exit code {code}; see the output log.")
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise


def validate_output(path: Path, expected_duration: float, audio_expected: bool) -> dict:
    info = probe(path)
    video = stream(info, "video")
    audio = stream(info, "audio")
    if not video or (video.get("width"), video.get("height")) != (1080, 1440):
        raise RuntimeError("Output validation failed: expected 1080 x 1440 video.")
    actual = duration(info)
    if actual > 90.08 or abs(actual - expected_duration) > 0.25:
        raise RuntimeError(f"Output duration mismatch: expected {expected_duration:.3f}, actual {actual:.3f}.")
    if audio_expected and not audio:
        raise RuntimeError("Output validation failed: audio track is missing.")
    if video.get("codec_name") != "h264" or video.get("pix_fmt") != "yuv420p":
        raise RuntimeError("Output validation failed: incompatible video encoding.")
    return {
        "width": video["width"], "height": video["height"], "duration_seconds": actual,
        "frame_rate": video.get("avg_frame_rate"), "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "file_size_bytes": path.stat().st_size,
    }


def render(config_path: Path, check_only: bool = False) -> int:
    config = read_json(config_path)
    for binary in (FFMPEG, FFPROBE):
        if not binary.is_file():
            raise ValueError(f"Required program is missing: {binary}")
    source = resolve_input(config.get("source"))
    if source is None:
        raise ValueError("source is required in the JSON configuration.")
    source_info = probe(source)
    if not stream(source_info, "video"):
        raise ValueError("Source has no video track.")
    external_audio = resolve_input(config.get("source_audio"))
    # YouTube often supplies independent video/audio streams. Use the adjacent
    # downloaded audio automatically only when the video has no embedded audio.
    if external_audio is None and not stream(source_info, "audio"):
        candidate = source.with_name("audio.m4a")
        if candidate.is_file():
            external_audio = candidate
    audio_info = probe(external_audio) if external_audio else source_info
    has_audio = stream(audio_info, "audio") is not None
    if external_audio and not has_audio:
        raise ValueError(f"Separate audio file contains no audio: {external_audio}")
    if not has_audio and not config.get("allow_silent_output", False):
        raise ValueError("Source has no audio. Download audio.m4a beside source.mp4, set source_audio, or explicitly allow_silent_output.")
    available = duration(source_info)
    if has_audio:
        available = min(available, duration(audio_info, "audio"))
    parts = choose_segments(config, available)
    total = sum(end - start for start, end in parts)
    overlay = make_overlay(config, total)
    from speech_subtitles import parse_srt,write_srt
    cues=parse_srt(config.get('subtitle_srt','')) if config.get('subtitles_enabled') else []
    cues=[dict(item,end=min(item['end'],total)) for item in cues if item['start']<total]
    if check_only:
        say(json.dumps({"source": str(source), "audio": str(external_audio) if external_audio else "embedded" if has_audio else None,
                        "segments": parts, "output_duration": total, "output_size": [1080, 1440]}, ensure_ascii=False, indent=2))
        return 0

    stage_private_fonts(config)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(config.get("output_prefix", config.get("product_id", "product")))).strip("_")[:80] or "product"
    if config.get('output_name'):
        from export_naming import safe_name
        slug=safe_name(config['output_name'])
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    name = f"{slug}_{stamp}"
    output_dir = output_storage.prepare_directory(config['output_dir'] if 'output_dir' in config else output_storage.load_settings()['output_dir'])
    job_dir = ROOT / "work" / "product_render" / name
    job_dir.mkdir(parents=True, exist_ok=False)
    output = output_dir / f"{name}.mp4"
    log_path = output_dir / f"{name}.log"
    metadata_path = output_dir / f"{name}.json"
    # Keep staging and final files on the same drive, including custom E:/F: folders.
    partial = output_dir / ('.'+name+'.partial.mp4')
    (job_dir / "overlay.ass").write_text(overlay, encoding="utf-8-sig")
    audio_index = (1 if external_audio else 0) if has_audio else None
    cover=resolve_input(config.get('cover_file'))
    if cover and (not cover.is_file() or cover.suffix.lower() not in {'.jpg','.png'}):raise ValueError('Cover image is missing or unsupported.')
    cover_index=(2 if external_audio else 1) if cover else None
    import brand_logos
    brand_overlay=brand_logos.overlay(config,job_dir/'brand_logo.png')
    brand_index=(1+int(bool(external_audio))+int(bool(cover))) if brand_overlay else None
    (job_dir / "render.ffscript").write_text(make_graph(parts, audio_index, config.get('fade_out_seconds',1.0),cover_index,config.get('cover_hold',.6),brand_index), encoding="utf-8")
    command = [str(FFMPEG), "-hide_banner", "-nostdin", "-n", "-loglevel", "info", "-threads", "4", "-i", str(source)]
    if external_audio:
        command += ["-i", str(external_audio)]
    if cover:
        command += ['-loop','1','-framerate','30','-i',str(cover)]
    if brand_overlay:
        command += ['-loop','1','-framerate','30','-i',str(brand_overlay)]
    command += ["-filter_complex_threads", "1", "-/filter_complex", "render.ffscript", "-map", "[vout]"]
    if has_audio:
        command += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    else:
        command += ["-an"]
    command += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-threads", "4", "-pix_fmt", "yuv420p",
                "-t", f"{total:.6f}", "-movflags", "+faststart", "-map_metadata", "-1", "-progress", "pipe:1", "-nostats", str(partial)]
    metadata = {
        "status": "rendering", "created_local": datetime.now().astimezone().isoformat(),
        "product_id": config.get("product_id"), "config": str(config_path), "source": str(source),
        "source_audio": str(external_audio) if external_audio else None,
        "source_references": config.get("source_references", {}),
        "brand_title": config.get("brand_title"), "product_title": config.get("product_title"),
        "description_lines": config.get("description_lines", []),
        "typography": {"font": config.get("font", "Microsoft YaHei"),
                       "brand_font": config.get("brand_font", "Bahnschrift"),
                       "title_font": config.get("title_font", config.get("font", "Microsoft YaHei")),
                       "description_font": config.get("description_font", config.get("font", "Microsoft YaHei")),
                       "text_scale": config.get("text_scale", 1.0),
                       "layout": "oswald_large_v4", "private_fonts": str(ROOT / 'assets/fonts/render'),
                       "editor_settings": config.get('typography'),
                       "overlay_file": str(job_dir / "overlay.ass")},
        "segments_seconds": [{"start": start, "end": end} for start, end in parts],
        "expected_duration_seconds": total, "output": str(output), "log": str(log_path),
        "ending_effect": {"type":"fade_to_black_and_silence","duration_seconds":min(config.get('fade_out_seconds',1.0),total)},
        "work_directory": str(job_dir), "transcription_used": bool(config.get('transcription')),
        "transcription": config.get('transcription'), "subtitle_count":len(cues),
        "translation":config.get('translation'), "subtitle_target":config.get('subtitle_target','original'),
        "translation_api_calls":0,
        "subtitles_enabled":bool(config.get('subtitles_enabled')), "llm_api_calls": 0,
        "text_review_note": config.get("text_review_note", ""),
        'director':config.get('director_report'),
        'brand_logo':config.get('brand_logo'),
        'cover':{'source_time':config.get('cover_time'),'hold_seconds':min(total,config.get('cover_hold',.6)),
                 'style':config.get('cover_style'),'wordmark_kind':config.get('wordmark_kind')} if cover else None,
    }
    begin = time.monotonic()
    try:
        with log_path.open("x", encoding="utf-8") as log:
            say(f"Product: {config.get('product_title')}")
            say(f"Duration: {total:.2f} seconds; output: 1080 x 1440 (3:4)")
            say(f"Log: {log_path}")
            run_encode(command, job_dir, log, total)
            metadata["validation"] = validate_output(partial, total, has_audio)
            # Windows rename fails if the destination already exists. Never
            # overwrite a previous export or alter the original source media.
            if output.exists():
                raise FileExistsError(f"Output already exists: {output}")
            partial.rename(output)
            if cover:
                cover_output=output.with_suffix('.cover.jpg')
                with cover.open('rb') as src,cover_output.open('xb') as dst:shutil.copyfileobj(src,dst)
                metadata['cover_file']=str(cover_output)
                title=resolve_input(config.get('title_asset_file'))
                if title:
                    with title.open('rb') as src,output.with_suffix('.title.png').open('xb') as dst:shutil.copyfileobj(src,dst)
            if cues:
                subtitle_file=output.with_suffix('.srt')
                with subtitle_file.open('x',encoding='utf-8-sig') as subtitle_handle:
                    subtitle_handle.write(write_srt(cues))
                metadata['subtitle_file']=str(subtitle_file)
                original_srt=config.get('original_subtitle_srt','')
                if original_srt:
                    original_file=output.with_suffix('.original.srt')
                    with original_file.open('x',encoding='utf-8-sig') as original_handle:
                        original_handle.write(original_srt)
                    metadata['original_subtitle_file']=str(original_file)
            metadata["status"] = "success"
            log.write("\nVALIDATION: " + json.dumps(metadata["validation"], ensure_ascii=False) + "\n")
    except BaseException as error:
        metadata["status"] = "cancelled" if isinstance(error, KeyboardInterrupt) else "failed"
        metadata["error"] = str(error)
        raise
    finally:
        metadata["elapsed_seconds"] = round(time.monotonic() - begin, 2)
        with metadata_path.open("x", encoding="utf-8") as handle:
            json.dump(metadata, handle, ensure_ascii=False, indent=2)
    say(f"Rendering: 100%\nSaved: {output}\nMetadata: {metadata_path}")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Make a <=90-second, 1080x1440 product video with local FFmpeg.")
    from runtime_paths import sample_config
    parser.add_argument("--config", default=str(sample_config(ROOT)))
    parser.add_argument("--check", action="store_true", help="Validate the configuration and source media without rendering.")
    arguments = parser.parse_args()
    try:
        return render(Path(arguments.config).resolve(), arguments.check)
    except KeyboardInterrupt:
        say("Cancelled. Original media is unchanged. See the job metadata/log for details.")
        return 130
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
