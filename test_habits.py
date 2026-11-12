import json
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app import generate
from habits import learn, recommend, shooting_summary, weights


class HabitTests(unittest.TestCase):
    def test_confirmed_choices_generalize_without_recounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            db, manifest = folder/"model.sqlite3", folder/"keepers.json"
            groups = []
            for index in range(3):
                names = [f"{index}-{option}.png" for option in range(2)]
                for level, name in enumerate(names):
                    Image.new("RGB", (40, 30), (100+index+level*10,)*3).save(folder/name)
                groups.append(dict(candidates=names, keeper=names[1]))
            manifest.write_text(json.dumps(groups))
            self.assertEqual(learn(folder, manifest, db), 3)
            learn(folder, manifest, db)
            with closing(sqlite3.connect(db)) as con, con:
                self.assertEqual(con.execute("select count(*) from choices").fetchone()[0], 3)
            self.assertEqual(recommend([(123, 0, 0), (128, 0, 0)], weights(db)), 1)
            self.assertIsNone(recommend([(1, 0, 0)]*2, weights(db)))
            target=folder/"review.html"
            generate(folder,target,weights(db))
            self.assertIn("按你的选片习惯推荐",target.read_text(encoding="utf8"))
            self.assertEqual(shooting_summary(folder)["画幅"], {"横幅":6})
            groups[0]["keeper"] = None
            manifest.write_text(json.dumps(groups))
            learn(folder, manifest, db)
            self.assertIsNone(weights(db))

if __name__ == "__main__":
    unittest.main()
