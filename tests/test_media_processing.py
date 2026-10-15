import io

import pytest
from PIL import Image

from micropub_server.config import Media
from micropub_server.media import MediaError, process_upload


def jpeg_with_gps(size=(400, 300), orientation=1) -> bytes:
    im = Image.new("RGB", size, (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = orientation  # orientation
    exif[0x010F] = "SecretCameraMaker"  # make
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (52.0, 30.0, 12.34)
    gps[3] = "E"
    gps[4] = (13.0, 24.0, 5.67)
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif, quality=95)
    return buf.getvalue()


def test_fixture_really_has_gps():
    # guard the guard: the test image must carry GPS, or the next tests prove nothing
    exif = Image.open(io.BytesIO(jpeg_with_gps())).getexif()
    assert exif.get_ifd(0x8825)


def test_gps_and_all_exif_are_stripped_from_jpeg():
    out = process_upload(jpeg_with_gps(), "IMG_0001.JPG", Media())
    result = Image.open(io.BytesIO(out.content))
    assert not result.getexif().get_ifd(0x8825)
    assert len(result.getexif()) == 0
    assert b"SecretCameraMaker" not in out.content
    assert b"Exif" not in out.content[:64]


def test_exif_orientation_is_applied_before_stripping():
    # orientation 6 = rotate 90 CW: a 400x300 image becomes 300x400
    out = process_upload(jpeg_with_gps((400, 300), orientation=6), "a.jpg", Media())
    assert Image.open(io.BytesIO(out.content)).size == (300, 400)


def test_large_images_are_resized_down_not_up():
    big = io.BytesIO()
    Image.new("RGB", (4000, 2000)).save(big, "JPEG")
    out = process_upload(big.getvalue(), "big.jpg", Media())
    assert Image.open(io.BytesIO(out.content)).size == (2560, 1280)
    small = process_upload(jpeg_with_gps((100, 80)), "small.jpg", Media())
    assert Image.open(io.BytesIO(small.content)).size == (100, 80)


def test_png_is_kept_as_png_and_stripped():
    im = Image.new("RGBA", (50, 50), (0, 255, 0, 128))
    im.info["Software"] = "SecretEditor"
    buf = io.BytesIO()
    from PIL.PngImagePlugin import PngInfo

    meta = PngInfo()
    meta.add_text("Comment", "SecretComment")
    im.save(buf, "PNG", pnginfo=meta)
    assert b"SecretComment" in buf.getvalue()
    out = process_upload(buf.getvalue(), "pic.png", Media())
    assert out.ext == "png" and out.media_type == "photo" and out.content_type == "image/png"
    assert b"SecretComment" not in out.content


def test_properties():
    out = process_upload(jpeg_with_gps(), "My Holiday Snap!.JPEG", Media())
    assert out.media_type == "photo" and out.content_type == "image/jpeg" and out.ext == "jpg"
    assert out.filename == "my-holiday-snap.jpg"
    assert len(out.md5) == 32


def test_same_input_gives_same_md5():
    a = process_upload(jpeg_with_gps(), "a.jpg", Media())
    b = process_upload(jpeg_with_gps(), "b.jpg", Media())
    assert a.md5 == b.md5


def animated_gif(comment: bytes | None = None) -> bytes:
    colours = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]  # different frames, or Pillow merges them
    frames = [Image.new("RGB", (10, 10), c).convert("P") for c in colours]
    buf = io.BytesIO()
    extra = {"comment": comment} if comment else {}
    frames[0].save(
        buf, "GIF", save_all=True, append_images=frames[1:], loop=0, duration=80, **extra
    )
    return buf.getvalue()


def test_gif_keeps_its_animation():
    out = process_upload(animated_gif(), "a.gif", Media())
    assert out.media_type == "photo" and out.ext == "gif"
    im = Image.open(io.BytesIO(out.content))
    durations = []
    for i in range(3):
        im.seek(i)
        durations.append(im.info["duration"])
    assert durations == [80, 80, 80]
    assert im.n_frames == 3 and im.info.get("loop") == 0


def test_gif_comments_are_stripped():
    data = animated_gif(comment=b"SecretComment")
    assert b"SecretComment" in data
    assert b"SecretComment" not in process_upload(data, "a.gif", Media()).content


def test_image_with_too_many_pixels_is_refused_before_it_is_decoded():
    # A tiny file that claims a huge canvas: the classic decompression bomb.
    buf = io.BytesIO()
    Image.new("1", (20_000, 20_000)).save(buf, "PNG")  # ~400 MP, compresses to a few KB
    assert len(buf.getvalue()) < 1_000_000
    with pytest.raises(MediaError) as exc:
        process_upload(buf.getvalue(), "bomb.png", Media())
    assert exc.value.status == 413 and "pixels" in str(exc.value)


def test_a_large_but_sane_photo_is_still_accepted():
    buf = io.BytesIO()
    Image.new("RGB", (6000, 4000), (10, 20, 30)).save(buf, "JPEG")  # 24 MP
    out = process_upload(buf.getvalue(), "big.jpg", Media())
    assert Image.open(io.BytesIO(out.content)).size == (2560, 1707)


@pytest.mark.parametrize(
    ("data", "name"),
    [
        (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "x.svg"),
        (b"%PDF-1.4 fake", "x.pdf"),
        (b"just text", "x.txt"),
        (b"MZ\x90\x00 fake exe", "x.exe"),
    ],
)
def test_unsupported_types_are_415(data, name):
    with pytest.raises(MediaError) as e:
        process_upload(data, name, Media())
    assert e.value.status == 415


def test_type_comes_from_the_bytes_not_the_filename():
    with pytest.raises(MediaError):
        process_upload(b"just text", "cute.jpg", Media())


def test_corrupt_image_is_rejected():
    with pytest.raises(MediaError) as e:
        process_upload(jpeg_with_gps()[:200], "broken.jpg", Media())
    assert e.value.status in (400, 415)


def test_too_large_is_413():
    with pytest.raises(MediaError) as e:
        process_upload(jpeg_with_gps(), "a.jpg", Media(max_bytes=100))
    assert e.value.status == 413


def test_mp3_audio_passes_through():
    mp3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + b"\x00" * 200
    out = process_upload(mp3, "song.mp3", Media())
    assert out.media_type == "audio" and out.content == mp3
