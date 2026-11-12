"""Generate a standalone, offline photo review page."""
import argparse
import base64
import html
import io
import json
from pathlib import Path

from PIL import Image, ImageOps


def describe(path):
    with Image.open(path) as src:
        photo = ImageOps.exif_transpose(src).convert("RGB")
    ratio = photo.width / photo.height
    photo.thumbnail((720, 560))
    colors = list(photo.resize((4, 4), Image.Resampling.BOX).getdata())
    gray = list(photo.convert("L").resize((9, 8), Image.Resampling.LANCZOS).getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            i = row * 9 + col
            bits = (bits << 1) | (gray[i] > gray[i+1])
    buf = io.BytesIO()
    photo.save(buf, "JPEG", quality=80)
    return (ratio, colors, bits), base64.b64encode(buf.getvalue()).decode("ascii")


def similar(left, right):
    ra, ca, ha = left
    rb, cb, hb = right
    distance = sum(abs(i-j) for a,b in zip(ca,cb) for i,j in zip(a,b))
    return abs(ra/rb-1) <= .12 and distance <= 48*24 and (ha ^ hb).bit_count() <= 8


def generate(folder, output):
    groups = []
    skipped = 0
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"} or not path.is_file():
            continue
        try:
            key, data = describe(path)
        except (ValueError, OSError, Image.DecompressionBombError):
            skipped += 1
            continue
        for anchor, members in groups:
            if similar(key, anchor):
                break
        else:
            members = []
            groups.append((key, members))
        members.append((path.name, data))
    parts = []
    for _, members in groups:
        if len(members) < 2:
            continue
        number = len(parts)+1
        cards = []
        for name, data in members:
            safe = html.escape(name, quote=True)
            cards.append(f'<label><img alt="" src="data:image/jpeg;base64,{data}"><span><input type="radio" name="g{number}" value="{safe}">{safe}</span></label>')
        parts.append(f'<fieldset><legend>第 {number} 组</legend>{"".join(cards)}<button type="button" class="clear">清除本组选择</button></fieldset>')
    template = Path(__file__).with_name("review.html").read_text(encoding="utf-8-sig")
    page = template.replace("<!-- GROUPS -->", "".join(parts) or "<p>未找到相似照片。</p>")
    with output.open("x", encoding="utf-8") as stream:
        stream.write(page)
    return len(parts), skipped


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生成本地相似照片对照页")
    parser.add_argument("folder", type=Path)
    parser.add_argument("output", type=Path, help="新的 HTML 文件名")
    args = parser.parse_args()
    try:
        count, skipped = generate(args.folder, args.output)
    except OSError as exc:
        parser.exit(1, f"{exc}\n")
    print(f"生成 {count} 个候选组，跳过 {skipped} 个无法读取的文件。")
