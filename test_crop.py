import json
import sqlite3
import tempfile
import tkinter as tk
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
import crop


class CropTest(unittest.TestCase):
    def test_confirm_export_then_suggest_for_similar_new_photo(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            paths=[folder/"one.png",folder/"two.png"]
            for i,p in enumerate(paths):
                Image.new("RGB",(1200,800),(120+i,90,80)).save(p)
            manifest=folder/"keepers.json"
            manifest.write_text(json.dumps([dict(keeper=p.name,candidates=[p.name,"other.jpg"]) for p in paths]))
            self.assertEqual(crop.selection(folder,manifest),paths)
            db=folder/"crop.sqlite3"
            root=tk.Tk()
            self.addCleanup(root.destroy)
            with patch.object(crop,"DB",db):
                editor=crop.Editor(root,paths)
                root.update()
                editor.enabled.set(True);editor.reset()
                editor.start_drag(SimpleNamespace(x=0,y=0))
                editor.drag(SimpleNamespace(x=110,y=0))
                editor.zoom(1.25)
                corrected=editor.box
                with patch.object(crop.filedialog,"asksaveasfilename",return_value=""):
                    editor.export()
                self.assertFalse(db.exists())
                dest=folder/"approved.jpg"
                with patch.object(crop.filedialog,"asksaveasfilename",return_value=str(dest)):
                    editor.export()
                with Image.open(dest) as output:
                    self.assertEqual(output.size,(360,640))
                editor.move(1)
                self.assertEqual(editor.mode,"personal")
                self.assertAlmostEqual(editor.box.x,corrected.x,places=3)
                editor.reset(False)
                self.assertNotEqual(editor.box,corrected)
                with closing(sqlite3.connect(db)) as con:
                    self.assertEqual(con.execute("select accepted from crops").fetchone(),(0,))
                with patch.object(crop.messagebox,"askyesno",return_value=True):
                    editor.clear()
                self.assertFalse(db.exists())
                with self.assertRaises(FileExistsError):
                    crop.write_crop(editor.image,editor.box,paths[0])


if __name__=="__main__":
    unittest.main()
