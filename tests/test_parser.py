import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from srd_gui import parser, naming  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")


def read(name):
    with open(os.path.join(FIX, name), encoding="utf-8", errors="replace") as f:
        return f.read()


class NormalizeUrlTests(unittest.TestCase):
    def test_variants(self):
        for raw in (
            "https://www.spriters-resource.com/game_boy_gbc/pokemongoldsilver/",
            "www.spriters-resource.com/game_boy_gbc/pokemongoldsilver",
            "https://www.spriters-resource.com/game_boy_gbc/pokemongoldsilver/asset/9100/",
            "https://spriters-resource.com/game_boy_gbc/pokemongoldsilver/?x=1",
        ):
            c, console, game = parser.normalize_game_url(raw)
            self.assertEqual(c, "https://www.spriters-resource.com/game_boy_gbc/pokemongoldsilver/")
            self.assertEqual((console, game), ("game_boy_gbc", "pokemongoldsilver"))

    def test_rejects(self):
        for raw in ("", "https://example.com/a/b/", "https://www.spriters-resource.com/", "https://www.spriters-resource.com/nes/"):
            with self.assertRaises(parser.InvalidGameUrl):
                parser.normalize_game_url(raw)

    def test_base_override(self):
        c, _, _ = parser.normalize_game_url("http://127.0.0.1:8123/nes/1942/", base_url="http://127.0.0.1:8123")
        self.assertEqual(c, "http://127.0.0.1:8123/nes/1942/")


class GamePageTests(unittest.TestCase):
    def test_pokemon_2026(self):
        cat = parser.parse_game_page(read("game_pokemongoldsilver_2026-10.html"),
                                     "https://www.spriters-resource.com/game_boy_gbc/pokemongoldsilver/")
        self.assertEqual(cat.game_title, "Pokémon Gold / Silver")
        self.assertEqual(cat.console_title, "Game Boy / GBC")
        names = [s.name for s in cat.sections]
        self.assertEqual(names, ["Characters", "Pokémon", "Backgrounds (Routes)", "Backgrounds (Locations)", "Miscellaneous"])
        for s in cat.sections:
            self.assertEqual(s.declared_count, len(s.items), s.name)
        self.assertEqual(len(cat.items), 128)
        first = cat.items[0]
        self.assertEqual(first.id, "9100")
        self.assertEqual(first.title, "Characters (Battle)")
        self.assertEqual(first.url, "https://www.spriters-resource.com/game_boy_gbc/pokemongoldsilver/asset/9100/")
        self.assertTrue(first.thumb.startswith("https://www.spriters-resource.com/media/asset_icons/9/9100.png"))
        self.assertEqual([i.order for i in cat.items], list(range(128)))
        self.assertEqual(len({i.id for i in cat.items}), 128)
        json.dumps(cat.to_dict())  # serialisable

    def test_1942(self):
        cat = parser.parse_game_page(read("game_1942_2026-03.html"), "https://www.spriters-resource.com/nes/1942/")
        self.assertEqual(cat.game_title, "1942")
        self.assertEqual(cat.console_title, "NES")
        self.assertEqual([s.name for s in cat.sections], ["Playable Characters", "Enemies & Bosses", "Maps", "Miscellaneous"])
        self.assertEqual(len(cat.items), 20)
        self.assertTrue(cat.items[0].thumb.endswith("94430.gif?updated=1755475979"))

    def test_unsectioned_and_legacy_sheet_links(self):
        html = """<html><h1>Foo</h1><div class="icondisplay">
        <a href="/nes/foo/sheet/12/"><div class="iconheader" title="A">A</div><img src="/x.png"></a>
        <a href="/nes/foo/"><div class="iconheader">not an asset</div></a>
        <a href="/nes/foo/asset/12/"><div class="iconheader">dup</div></a>
        </div></html>"""
        cat = parser.parse_game_page(html, "https://www.spriters-resource.com/nes/foo/")
        self.assertEqual(len(cat.sections), 1)
        self.assertEqual(cat.sections[0].name, "Uncategorized")
        self.assertEqual([i.id for i in cat.items], ["12"])


class AssetPageTests(unittest.TestCase):
    def test_download_button(self):
        a = parser.parse_asset_page(read("asset_106321_2026-08.html"),
                                    "https://www.spriters-resource.com/game_boy_gbc/pokemongoldsilver/asset/106321/")
        self.assertEqual(a.kind, "download-link")
        self.assertEqual(a.url, "https://www.spriters-resource.com/media/assets/103/106321.png?updated=1755477189")
        self.assertEqual(a.filename, "Game Boy / GBC - Pokemon Gold / Silver - Miscellaneous - Maps (World Map & Flight).png")
        self.assertEqual(a.title, "Maps (World Map & Flight)")

    def test_second_fixture(self):
        a = parser.parse_asset_page(read("asset_146844_2026-03.html"), "https://www.spriters-resource.com/x/y/asset/146844/")
        self.assertTrue(a.url.endswith("146844.png?updated=1755482675"))
        self.assertTrue(a.filename.endswith("Mystery Gift Screen.png"))

    def test_image_fallback(self):
        html = '<h1>Sheet</h1><div class="zoomdisplay"><img src="/media/assets/1/2.gif"></div>'
        a = parser.parse_asset_page(html, "https://www.spriters-resource.com/x/y/asset/2/")
        self.assertEqual(a.kind, "image")
        self.assertEqual(a.filename, "Sheet.gif")

    def test_nothing(self):
        self.assertIsNone(parser.parse_asset_page("<html><body>nope</body></html>", "https://www.spriters-resource.com/x/y/asset/2/"))

    def test_challenge_detection(self):
        self.assertTrue(parser.looks_like_challenge('<title>Just a moment...</title><div id="challenge-running">'))
        self.assertFalse(parser.looks_like_challenge(read("asset_106321_2026-08.html")))


class NamingTests(unittest.TestCase):
    def test_sanitize(self):
        self.assertEqual(naming.sanitize("Pokémon Gold / Silver"), "Pokémon Gold - Silver")
        self.assertEqual(naming.sanitize('a<b>:"c|d?e*f'), "a_b___c_d_e_f")
        self.assertEqual(naming.sanitize("  trailing dots... "), "trailing dots")
        self.assertEqual(naming.sanitize("CON"), "_CON")
        self.assertEqual(naming.sanitize("con.png"), "_con.png")
        self.assertEqual(naming.sanitize(""), "untitled")
        self.assertEqual(len(naming.sanitize("x" * 500)), naming.MAX_COMPONENT)

    def test_split_ext(self):
        self.assertEqual(naming.split_ext("Foo.PNG"), ("Foo", ".png"))
        self.assertEqual(naming.split_ext("Mr. Game & Watch"), ("Mr. Game & Watch", ""))
        self.assertEqual(naming.split_ext("archive.zip"), ("archive", ".zip"))

    def test_unique(self):
        out = naming.unique_names([("1", "Title", ".png"), ("2", "title", ".png"), ("3", "Other", ".gif")])
        self.assertEqual(out, {"1": "Title (1).png", "2": "title (2).png", "3": "Other.gif"})


if __name__ == "__main__":
    unittest.main()
