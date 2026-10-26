import io
import json
import shutil

from PIL import Image
from ruamel.yaml import YAML

from micropub_server.backfill_cards import backfill_scraped
from micropub_server.cli import main
from micropub_server.config import MediaCard

CFG = MediaCard()
POST = """---
title: Explaining Variables
bookmark_of: 'https://blog.thepete.net/x/'
date: '2021-07-07T09:38:46.339Z'
aliases:
  - /bookmarks/2021/20210707t093846/
---
Yes to self-explaining variables pls.
"""
BUNDLE = "content/bookmarks/2021/07/07/a"


def jpeg(size=(1331, 998)) -> bytes:
    buf = io.BytesIO()
    Image.effect_noise(size, 40).convert("RGB").save(buf, "JPEG")
    return buf.getvalue()


def make(root, name="a", post=POST, og=None, image: bytes | None = None, filename="cover.jpg"):
    """A 2021 bookmark bundle with a scraped/ directory; returns the bundle path."""
    bundle = root / "content/bookmarks/2021/07/07" / name
    (bundle / "scraped").mkdir(parents=True)
    (bundle / "index.md").write_text(post)
    og = {"description": "Desc", "siteName": "Pete Hodgson"} if og is None else og
    (bundle / "scraped/opengraph.json").write_text(json.dumps(og))
    if image is not None:
        (bundle / "scraped/files").mkdir()
        (bundle / "scraped/files" / filename).write_bytes(image)
    return bundle


def og_with_image(**image):
    return {
        "title": "Ignored",
        "description": "An Explaining Variable.",
        "siteName": "Pete Hodgson",
        "image": {"filename": "cover.jpg", "type": "jpg", "height": "998", "width": 1331} | image,
    }


def front(path):
    text = path.read_text()
    return YAML(typ="safe").load(text.split("---\n")[1])


def test_converts_a_bundle(tmp_path):
    bundle = make(tmp_path, og=og_with_image(), image=jpeg())
    report = backfill_scraped(tmp_path, CFG)
    assert report.converted == 1
    meta = front(bundle / "index.md")
    assert meta["bookmark_site"] == "Pete Hodgson"
    assert meta["bookmark_description"] == "An Explaining Variable."
    assert meta["bookmark_image"] == {"file": "card.webp", "alt": "", "width": 800, "height": 600}
    assert meta["title"] == "Explaining Variables"  # the json's title is ignored
    webp = Image.open(bundle / "card.webp")
    assert (webp.format, webp.size) == ("WEBP", (800, 600))
    assert not (bundle / "scraped").exists()


def test_rest_of_the_front_matter_is_byte_for_byte(tmp_path):
    bundle = make(tmp_path, og=og_with_image(), image=jpeg())
    backfill_scraped(tmp_path, CFG)
    after = (bundle / "index.md").read_text()
    for line in POST.splitlines(keepends=True):
        assert line in after
    assert after.startswith(
        "---\ntitle: Explaining Variables\nbookmark_of: 'https://blog.thepete.net/x/'\n"
        "bookmark_site: Pete Hodgson\n"
    )
    assert after.endswith("---\nYes to self-explaining variables pls.\n")
    assert after.replace(after[after.index("bookmark_site") : after.index("date:")], "") == POST


def test_skips_a_bookmark_that_has_card_keys(tmp_path):
    done = POST.replace("date:", "bookmark_site: Mine\ndate:")
    bundle = make(tmp_path, post=done, og=og_with_image(), image=jpeg())
    report = backfill_scraped(tmp_path, CFG)
    assert (report.converted, report.skipped) == (0, 1)
    assert (bundle / "index.md").read_text() == done
    assert (bundle / "scraped").exists()
    assert not (bundle / "card.webp").exists()


def test_dry_run_changes_nothing(tmp_path):
    make(tmp_path, og=og_with_image(), image=jpeg())
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = backfill_scraped(tmp_path, CFG, dry_run=True)
    assert report.converted == 1
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_text_only_when_there_is_no_image_or_it_is_unusable(tmp_path):
    none = make(tmp_path, "none")  # no image key
    missing = make(tmp_path, "missing", og=og_with_image())  # file not there
    tiny = make(tmp_path, "tiny", og=og_with_image(), image=jpeg((50, 50)))
    junk = make(tmp_path, "junk", og=og_with_image(), image=b"not an image")
    report = backfill_scraped(tmp_path, CFG)
    assert report.converted == 4
    assert report.no_image == 4
    for bundle in (none, missing, tiny, junk):
        meta = front(bundle / "index.md")
        assert "bookmark_image" not in meta
        assert meta["bookmark_site"] == "Pete Hodgson"
        assert not (bundle / "card.webp").exists()
        assert not (bundle / "scraped").exists()


def test_empty_values_are_never_written(tmp_path):
    bundle = make(tmp_path, og={"siteName": "", "description": "Only this"})
    backfill_scraped(tmp_path, CFG)
    meta = front(bundle / "index.md")
    assert "bookmark_site" not in meta
    assert meta["bookmark_description"] == "Only this"


def test_nothing_usable_leaves_the_bundle_alone(tmp_path):
    bundle = make(tmp_path, og={"title": "T"})
    report = backfill_scraped(tmp_path, CFG)
    assert (report.converted, report.skipped) == (0, 1)
    assert (bundle / "index.md").read_text() == POST
    assert (bundle / "scraped").exists()


def test_other_files_in_the_bundle_survive_and_unscraped_bookmarks_are_ignored(tmp_path):
    bundle = make(tmp_path, og=og_with_image(), image=jpeg())
    (bundle / "syndication").mkdir()
    (bundle / "syndication/x.json").write_text("{}")
    plain = tmp_path / "content/bookmarks/2021/07/08/b"
    plain.mkdir(parents=True)
    (plain / "index.md").write_text(POST)
    backfill_scraped(tmp_path, CFG)
    assert (bundle / "syndication/x.json").exists()
    assert (plain / "index.md").read_text() == POST


def test_a_second_run_does_nothing(tmp_path):
    make(tmp_path, og=og_with_image(), image=jpeg())
    backfill_scraped(tmp_path, CFG)
    assert backfill_scraped(tmp_path, CFG).converted == 0


def test_cli_prints_counts_and_honours_dry_run(tmp_path, capsys):
    make(tmp_path, og=og_with_image(), image=jpeg())
    copy = tmp_path.parent / (tmp_path.name + "-copy")
    shutil.copytree(tmp_path, copy)
    assert (
        main(["backfill-cards", "--checkout", str(tmp_path), "--step", "scraped", "--dry-run"]) == 0
    )
    assert "would convert 1" in capsys.readouterr().out
    assert (tmp_path / BUNDLE / "scraped").exists()
    assert main(["backfill-cards", "--checkout", str(copy), "--step", "scraped"]) == 0
    assert "converted 1" in capsys.readouterr().out
    assert (copy / BUNDLE / "card.webp").exists()


def test_type_like_on_the_scraped_step_is_a_no_op(tmp_path, capsys):
    bundle = make(tmp_path, og=og_with_image(), image=jpeg())  # a bookmark: must stay as it is
    like = tmp_path / "content/likes/2021/07/07/x"
    like.mkdir(parents=True)
    (like / "index.md").write_text("---\nlike_of: https://a.example/\ndate: '2021-07-07'\n---\n")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    args = ["backfill-cards", "--checkout", str(tmp_path), "--step", "scraped", "--type", "like"]
    assert main(args) == 0
    assert "converted 0" in capsys.readouterr().out
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert (bundle / "scraped").exists()


def test_scraped_data_in_a_like_bundle_converts_with_like_keys(tmp_path):
    like = tmp_path / "content/likes/2021/07/07/x"
    (like / "scraped").mkdir(parents=True)
    (like / "index.md").write_text("---\nlike_of: https://a.example/\nlike_title: T\n---\n")
    (like / "scraped/opengraph.json").write_text(json.dumps({"siteName": "A", "description": "D"}))
    report = backfill_scraped(tmp_path, CFG, kind="like")
    assert report.converted == 1
    assert list(front(like / "index.md")) == [
        "like_of", "like_title", "like_site", "like_description",
    ]  # fmt: skip
