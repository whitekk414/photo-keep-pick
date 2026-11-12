"""Interactive original-resolution cropping with optional local example-based learning."""
import argparse
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageFilter, ImageOps, ImageStat, ImageTk

from habits import DEFAULT_DB, learn

DB = DEFAULT_DB.with_name("crops.sqlite3")
RATIOS = {"9:16": 9/16, "1:1": 1., "4:5": .8, "16:9": 16/9}


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float

    def data(self):
        return [self.x, self.y, self.w, self.h]


def clamp(x, low, high):
    return max(low, min(x, high))


def box_at(size, ratio, x, y, zoom):
    aspect = size[0]/size[1]
    w = min(1., ratio/aspect)/clamp(zoom, 1., 4.)
    h = w*aspect/ratio
    return Box(clamp(x-w/2, 0, 1-w), clamp(y-h/2, 0, 1-h), w, h)


def subject(image):
    # A coarse edge-density centroid. Remove the filter's artificial image border.
    edges = image.convert("L").resize((66, 50)).filter(ImageFilter.FIND_EDGES).crop((1, 1, 65, 49))
    grid = edges.resize((8, 6), Image.Resampling.BOX)
    values = list(grid.getdata())
    floor = sorted(values)[len(values)//2]
    mass = [max(0, v-floor) for v in values]
    total = sum(mass)
    x = sum((i%8+.5)*v for i,v in enumerate(mass))/(8*total) if total else .5
    y = sum((i//8+.5)*v for i,v in enumerate(mass))/(6*total) if total else .5
    gray = image.convert("L")
    layout = gray.resize((8, 6), Image.Resampling.BOX)
    return (x, y, ImageStat.Stat(gray.resize((16, 16))).mean[0]/255,
            *(pixel/255 for pixel in layout.getdata()))


def scene_match(current, stored):
    """Compare layouts in both directions. Old records have only three features."""
    direction = 1
    layout_error = 0.
    if len(stored) == len(current) == 51:
        grid = stored[3:]
        reversed_grid = [grid[row+7-col] for row in range(0, 48, 8) for col in range(8)]
        direct = math.dist(current[3:], grid)/math.sqrt(48)
        reflected = math.dist(current[3:], reversed_grid)/math.sqrt(48)
        # Only reverse a learned pan for a close, unambiguous mirrored match.
        if reflected < .09 and reflected + .03 < direct:
            direction = -1
        layout_error = reflected if direction < 0 else direct
        if layout_error > .16:
            return math.inf, direction
    x, y, light = stored[:3]
    if direction < 0:
        x = 1-x
    return math.dist(current[:3], (x, y, light)) + layout_error, direction


def suggested(size, ratio, scene, samples):
    default = box_at(size, ratio, scene[0], scene[1], 1)
    neighbors = []
    for stored_scene, original, final in samples:
        distance, direction = scene_match(scene, stored_scene)
        if distance <= .32:
            neighbors.append((distance, direction, original, final))
    neighbors.sort(key=lambda item:item[0])
    if not neighbors:
        return default, None
    dx = dy = zoom = total = 0.
    for distance, direction, base, end in neighbors[:8]:
        weight = 1/(.08+distance)
        dx += direction*weight*((end.x+end.w/2)-(base.x+base.w/2))
        dy += weight*((end.y+end.h/2)-(base.y+base.h/2))
        zoom += weight*base.w/end.w
        total += weight
    personal = box_at(size, ratio, default.x+default.w/2+dx/total,
                      default.y+default.h/2+dy/total, zoom/total)
    return default, personal


def samples(db, ratio_name, key):
    if not db.exists():
        return []
    with closing(sqlite3.connect(db)) as con:
        rows = con.execute("SELECT scene, baseline, final FROM crops WHERE ratio=? AND image<>?", (ratio_name,key))
        return [(json.loads(s), Box(*json.loads(b)), Box(*json.loads(f))) for s,b,f in rows]


def remember(db, key, ratio, scene, baseline, initial, final, mode, elapsed):
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as con, con:
        con.execute("""CREATE TABLE IF NOT EXISTS crops (
            image TEXT, ratio TEXT, scene TEXT, baseline TEXT, initial TEXT, final TEXT,
            mode TEXT, elapsed REAL, accepted INTEGER, PRIMARY KEY(image,ratio))""")
        accepted = max(abs(a-b) for a,b in zip(initial.data(),final.data())) < .001
        con.execute("INSERT OR REPLACE INTO crops VALUES (?,?,?,?,?,?,?,?,?)",
                    (key, ratio, json.dumps(scene), *(json.dumps(b.data()) for b in [baseline,initial,final]),
                     mode, elapsed, int(accepted)))


def write_crop(image, box, path):
    w,h = image.size
    x,y = min(w-1,round(box.x*w)), min(h-1,round(box.y*h))
    extent = (x,y,max(x+1,round((box.x+box.w)*w)),max(y+1,round((box.y+box.h)*h)))
    cropped = image.crop(extent)
    with path.open("xb") as stream:
        try:
            cropped.convert("RGB").save(stream, "JPEG", quality=95)
        except Exception:
            stream.close()
            path.unlink(missing_ok=True)
            raise


def selection(folder, manifest):
    root = folder.resolve(strict=True)
    paths = []
    for group in json.loads(manifest.read_text(encoding="utf-8-sig")):
        keeper = group["keeper"]
        if keeper is None:
            continue
        if not isinstance(keeper,str) or Path(keeper).name != keeper or keeper not in group["candidates"]:
            raise ValueError("无效的保留项")
        p = (root/keeper).resolve(strict=True)
        if p.parent != root:
            raise ValueError("照片必须位于原目录内")
        if p not in paths:
            paths.append(p)
    if not paths:
        raise ValueError("清单里没有已选照片")
    return paths


class Editor:
    def __init__(self, root, paths, folder=None, manifest=None):
        self.root,self.paths,self.index = root,paths,0
        root.title("Photo Keep Pick · 裁剪")
        bar = ttk.Frame(root,padding=8); bar.pack(fill="x")
        self.ratio = tk.StringVar(value="9:16")
        ratios = ttk.Combobox(bar, textvariable=self.ratio, values=list(RATIOS), state="readonly",width=6)
        ratios.pack(side="left"); ratios.bind("<<ComboboxSelected>>", lambda _:self.reset())
        ttk.Button(bar,text="默认构图",command=lambda:self.reset(False)).pack(side="left")
        self.personal_button = ttk.Button(bar,text="按我的习惯",command=lambda:self.reset(True))
        self.personal_button.pack(side="left")
        self.enabled=tk.BooleanVar(value=False)
        ttk.Checkbutton(bar,text="记住我的裁剪习惯",variable=self.enabled,command=self.reset).pack(side="left")
        ttk.Button(bar,text="清除裁剪记录",command=self.clear).pack(side="left")
        self.canvas=tk.Canvas(root,width=720,height=460,bg="#26342f",highlightthickness=0)
        self.canvas.pack(fill="both",expand=True)
        self.canvas.bind("<Configure>", lambda _:self.draw())
        self.canvas.bind("<ButtonPress-1>", self.start_drag)
        self.canvas.bind("<B1-Motion>",self.drag)
        controls=ttk.Frame(root,padding=8);controls.pack(fill="x")
        ttk.Label(controls,text="放大").pack(side="left")
        self.scale=ttk.Scale(controls,from_=1,to=4,command=self.zoom);self.scale.pack(side="left",fill="x",expand=True)
        for text, action in [("上一张",lambda:self.move(-1)),("下一张",lambda:self.move(1)),
                             ("导出 JPEG",self.export),("查看效果",self.stats)]:
            ttk.Button(controls,text=text,command=action).pack(side="left")
        if manifest:
            def remember_selection():
                try:
                    count=learn(folder,manifest,DEFAULT_DB)
                    messagebox.showinfo("选片习惯",f"已处理 {count} 组已确认选择。")
                except (OSError, ValueError,sqlite3.Error) as exc:
                    messagebox.showerror("记录失败",str(exc))
            ttk.Button(controls,text="记住清单选片",command=remember_selection).pack(side="left")
        self.info=ttk.Label(root,padding=8);self.info.pack(fill="x")
        self.load()

    def load(self):
        source=self.paths[self.index]
        with Image.open(source) as src:
            self.image=ImageOps.exif_transpose(src).convert("RGB")
        self.preview=self.image.copy();self.preview.thumbnail((1280,900))
        self.scene=subject(self.preview)
        digest=hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b""):digest.update(chunk)
        self.key=digest.hexdigest()
        self.started=time.monotonic()
        self.reset()

    def reset(self, use_personal=True):
        ratio=self.ratio.get()
        history=samples(DB,ratio,self.key) if self.enabled.get() else []
        self.base,personal=suggested(self.image.size,RATIOS[ratio],self.scene,history)
        self.personal_button.configure(state="normal" if personal else "disabled")
        self.mode="personal" if use_personal and personal else "default"
        self.box=self.initial=personal if self.mode=="personal" else self.base
        self.anchor=None
        self.scale.set(self.base.w/self.box.w)
        self.info.configure(text=f"{self.index+1}/{len(self.paths)}  {self.paths[self.index].name}   ·   "
                            +("按我的习惯" if self.mode=="personal" else "默认构图")+"   ·   拖动取景框调整位置")
        self.draw()

    def draw(self):
        cw,ch=self.canvas.winfo_width(),self.canvas.winfo_height()
        if min(cw,ch)<2:return
        factor=min(cw/self.preview.width,ch/self.preview.height)
        w,h=max(1,round(self.preview.width*factor)),max(1,round(self.preview.height*factor))
        left,top=(cw-w)/2,(ch-h)/2
        self.display=(left,top,w,h)
        key=(self.index,w,h)
        if getattr(self,"rendered",None)!=key:
            self.tkimage=ImageTk.PhotoImage(self.preview.resize((w,h),Image.Resampling.LANCZOS))
            self.canvas.delete("all")
            self.canvas.create_image(left,top,image=self.tkimage,anchor="nw",tags="image")
            self.rendered=key
        else:self.canvas.coords("image",left,top)
        self.canvas.delete("frame")
        b=self.box
        coords=(left+b.x*w,top+b.y*h,left+(b.x+b.w)*w,top+(b.y+b.h)*h)
        self.canvas.create_rectangle(*coords,width=5,outline="#16382a",tags="frame")
        self.canvas.create_rectangle(*coords,width=2,outline="#f6ec87",tags="frame")

    def start_drag(self,event):
        self.anchor=(event.x,event.y,self.box)

    def drag(self,event):
        if self.anchor is None:return
        x,y,b=self.anchor
        _,_,w,h=self.display
        self.box=Box(clamp(b.x+(event.x-x)/w,0,1-b.w),clamp(b.y+(event.y-y)/h,0,1-b.h),b.w,b.h)
        self.draw()

    def zoom(self,value):
        b=self.box
        self.box=box_at(self.image.size,RATIOS[self.ratio.get()],b.x+b.w/2,b.y+b.h/2,float(value))
        self.draw()

    def move(self,step):
        idx=self.index+step
        if 0<=idx<len(self.paths):
            self.index=idx;self.load()

    def export(self):
        source=self.paths[self.index]
        dest=filedialog.asksaveasfilename(defaultextension=".jpg",filetypes=[("JPEG","*.jpg")],
                                         initialfile=source.stem+"-crop.jpg")
        if not dest:return
        try:write_crop(self.image,self.box,Path(dest))
        except OSError as exc:
            messagebox.showerror("导出失败，请使用新文件名",str(exc));return
        if self.enabled.get():
            try:
                remember(DB,self.key,self.ratio.get(),self.scene,self.base,self.initial,self.box,
                         self.mode,time.monotonic()-self.started)
            except (OSError,sqlite3.Error) as exc:
                messagebox.showwarning("图片已导出，偏好保存失败",str(exc));return
        self.info.configure(text="已导出 "+dest)

    def stats(self):
        if not DB.exists():
            messagebox.showinfo("使用效果","还没有已记录的导出。");return
        with closing(sqlite3.connect(DB)) as con:
            rows=con.execute("SELECT mode,count(*),sum(accepted),avg(elapsed) FROM crops GROUP BY mode")
            lines=[f"{'默认构图' if m=='default' else '按我的习惯'}：{n} 次导出，{a} 次直接采用，平均 {t:.1f} 秒" for m,n,a,t in rows]
        messagebox.showinfo("使用效果","\n".join(lines) or "还没有记录")

    def clear(self):
        if messagebox.askyesno("清除记录","删除本机全部裁剪偏好和使用效果记录？"):
            DB.unlink(missing_ok=True);self.enabled.set(False);self.reset()


if __name__=="__main__":
    parser=argparse.ArgumentParser(description="选片后裁剪并可选学习构图")
    parser.add_argument("images",type=Path,nargs="*")
    parser.add_argument("--keepers",type=Path)
    parser.add_argument("--folder",type=Path)
    args=parser.parse_args()
    if bool(args.keepers)!=bool(args.folder) or (args.keepers and args.images):
        parser.error("使用照片路径，或同时提供 --keepers 和 --folder")
    root=tk.Tk();root.withdraw()
    try:
        paths=selection(args.folder,args.keepers) if args.keepers else args.images
        if not paths:paths=[Path(p) for p in filedialog.askopenfilenames(title="选择要裁剪的照片")]
        if paths:
            Editor(root,paths,args.folder,args.keepers);root.deiconify();root.mainloop()
    except (OSError,ValueError,KeyError,sqlite3.Error) as exc:
        parser.exit(1,f"{exc}\n")
