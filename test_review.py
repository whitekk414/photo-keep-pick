import tempfile
from pathlib import Path
from PIL import Image, ImageDraw
from app import generate

with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp)
    image = Image.new("RGB", (100, 80), "red")
    ImageDraw.Draw(image).rectangle((5, 10, 35, 48), fill="white")
    image.save(folder/"one.png")
    image.save(folder/"two.png")
    Image.new("RGB", (100, 80), "blue").save(folder/"other.png")
    (folder/"bad.jpg").write_bytes(b"oops")
    originals = {p: p.read_bytes() for p in folder.iterdir()}
    out = folder/"review.html"
    assert generate(folder, out) == (1, 1)
    page = out.read_text(encoding="utf-8")
    assert 'value="one.png"' in page and 'value="two.png"' in page and "other.png" not in page
    assert all(p.read_bytes() == data for p,data in originals.items())
    try:
        generate(folder, out)
    except FileExistsError:
        pass
    else:
        raise AssertionError("overwrote existing output")
    print("OK")
