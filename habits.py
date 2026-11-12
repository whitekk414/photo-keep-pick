"""Local photo-choice learning and descriptive camera statistics."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import closing

from PIL import Image, ImageFilter, ImageOps, ImageStat

IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
DEFAULT_DB = Path.home() / ".photo-keep-pick" / "choices.sqlite3"


def features(photo):
    """Brightness, local contrast and edge strength on a common pixel scale."""
    gray = ImageOps.fit(photo.convert("L"), (96, 96))
    stats = ImageStat.Stat(gray)
    edges = gray.filter(ImageFilter.FIND_EDGES).crop((1, 1, 95, 95))
    return (stats.mean[0], stats.stddev[0], ImageStat.Stat(edges).mean[0])


def ranks(rows):
    columns = list(zip(*rows))
    return [tuple(sum((v > other) - (v < other) for other in col) / (len(rows) - 1)
                  for v, col in zip(row, columns)) for row in rows]


def weights(db):
    if not db.exists():
        return None
    with closing(sqlite3.connect(db)) as con, con:
        count, *means = con.execute("SELECT count(*),avg(light),avg(contrast),avg(edges) FROM choices").fetchone()
    return means if count >= 3 else None


def recommend(rows, model):
    if model is None or len(rows) < 2:
        return None
    scores = [sum(a*b for a, b in zip(row, model)) for row in ranks(rows)]
    best = max(scores)
    if best <= 0 or scores.count(best) > 1:
        return None
    return scores.index(best)


def learn(folder, manifest, db):
    groups = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if not isinstance(groups, list):
        raise ValueError("选择清单必须是分组列表")
    updates = []
    root = folder.resolve(strict=True)
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("无效分组")
        names, keeper = group.get("candidates"), group.get("keeper")
        if not isinstance(names, list) or len(names) < 2 or not all(isinstance(n, str) for n in names):
            raise ValueError("无效候选清单")
        if len(set(names)) != len(names) or (keeper is not None and keeper not in names):
            raise ValueError("候选重复或保留项不在组内")
        rows, hashes = [], []
        for name in names:
            p = (root/name).resolve(strict=True)
            if p.parent != root or Path(name).name != name or p.suffix.lower() not in IMAGE_TYPES:
                raise ValueError("清单只接受原目录下的照片文件名")
            with Image.open(p) as img:
                rows.append(features(ImageOps.exif_transpose(img)))
            digest = hashlib.sha256()
            with p.open("rb") as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b""):
                    digest.update(chunk)
            hashes.append(digest.hexdigest())
        key = hashlib.sha256("".join(sorted(hashes)).encode()).hexdigest()
        updates.append((key, ranks(rows)[names.index(keeper)] if keeper is not None else None))
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as con, con:
        con.execute("CREATE TABLE IF NOT EXISTS choices (id TEXT PRIMARY KEY, light REAL, contrast REAL, edges REAL)")
        for key, row in updates:
            if row is None:
                con.execute("DELETE FROM choices WHERE id=?", (key,))
            else:
                con.execute("INSERT OR REPLACE INTO choices VALUES (?,?,?,?)", (key, *row))
    return sum(row is not None for _, row in updates)


def shooting_summary(folder):
    orientations, hours, focal = Counter(), Counter(), Counter()
    skipped = 0
    for p in folder.iterdir():
        if not p.is_file() or p.suffix.lower() not in IMAGE_TYPES:
            continue
        try:
            with Image.open(p) as image:
                exif = image.getexif()
                detail = exif.get_ifd(0x8769) if 0x8769 in exif else {}
                w, h = image.size
                if exif.get(274) in (5, 6, 7, 8):
                    w, h = h, w
                date = str(detail.get(36867, exif.get(36867, "")))
                length = detail.get(41989, exif.get(41989))
                lens = float(length) if length else 0
                hour = date[11:13]
                hours[hour+"时" if hour.isdigit() and 0 <= int(hour) < 24 else "未知"] += 1
                focal[f"{lens:g} mm" if lens > 0 else "未知"] += 1
                orientations["横幅" if w > h else "竖幅" if h > w else "方形"] += 1
        except (OSError, ValueError, TypeError, Image.DecompressionBombError):
            skipped += 1
    return {"画幅": dict(orientations), "拍摄小时": dict(sorted(hours.items())),
            "等效焦距": dict(focal), "跳过": skipped}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="在本地查看拍摄统计、记住或清除选片偏好")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("summary").add_argument("folder", type=Path)
    teach = commands.add_parser("learn")
    teach.add_argument("folder", type=Path)
    teach.add_argument("manifest", type=Path)
    commands.add_parser("clear")
    args = parser.parse_args()
    try:
        if args.action == "summary":
            print(json.dumps(shooting_summary(args.folder), ensure_ascii=False, indent=2))
        elif args.action == "learn":
            print(f"已处理 {learn(args.folder, args.manifest, DEFAULT_DB)} 组已确认选择")
        else:
            DEFAULT_DB.unlink(missing_ok=True)
            print("已清除选片偏好")
    except (OSError, ValueError, sqlite3.Error, Image.DecompressionBombError) as error:
        parser.exit(1, f"{error}\n")
