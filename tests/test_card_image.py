import io

import pytest
from PIL import Image

from micropub_server.card_image import CardImage, fetch_card, process_card
from micropub_server.config import MediaCard

from .conftest import public_resolver
from .test_media_processing import jpeg_with_gps

CFG = MediaCard()


def encode(fmt: str, size=(1600, 900), mode="RGB", **options) -> bytes:
    im = Image.effect_noise(size, 40).convert(mode)
    buf = io.BytesIO()
    im.save(buf, fmt, **options)
    return buf.getvalue()


def decoded(card: CardImage) -> Image.Image:
    return Image.open(io.BytesIO(card.content))


@pytest.mark.parametrize(
    "fmt, mode", [("JPEG", "RGB"), ("PNG", "RGB"), ("PNG", "RGBA"), ("WEBP", "RGB"), ("GIF", "P")]
)
def test_each_accepted_format_becomes_a_resized_webp(fmt, mode):
    card = process_card(encode(fmt, mode=mode), CFG)
    assert card is not None
    im = decoded(card)
    assert im.format == "WEBP"
    assert (card.width, card.height) == im.size == (800, 450)  # 16:9 kept


def test_small_images_are_not_upscaled():
    card = process_card(encode("PNG", (300, 200)), CFG)
    assert (card.width, card.height) == (300, 200)


def test_only_the_first_frame_of_a_gif_is_kept():
    frames = [Image.new("RGB", (400, 300), c) for c in ("red", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:])
    card = process_card(buf.getvalue(), CFG)
    assert getattr(decoded(card), "n_frames", 1) == 1


def test_fixture_really_has_gps():
    assert Image.open(io.BytesIO(jpeg_with_gps())).getexif().get_ifd(0x8825)


def test_gps_and_all_metadata_are_dropped():
    card = process_card(jpeg_with_gps((400, 300)), CFG)
    im = decoded(card)
    assert not im.getexif().get_ifd(0x8825)
    assert len(im.getexif()) == 0
    assert b"SecretCameraMaker" not in card.content
    assert b"EXIF" not in card.content and b"XMP" not in card.content


def test_exif_orientation_is_applied():
    card = process_card(jpeg_with_gps((400, 300), orientation=6), CFG)
    assert (card.width, card.height) == (300, 400)


def test_file_name_is_card_webp():
    assert process_card(encode("PNG", (300, 200)), CFG).filename == "card.webp"


@pytest.mark.parametrize(
    "data",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"></svg>',
        b"not an image at all",
        b"",
        encode("PNG", (150, 300)),  # too narrow
        encode("PNG", (400, 90)),  # too short
        encode("PNG", (32, 32)),  # a tracking pixel or logo
    ],
)
def test_unusable_images_give_none(data):
    assert process_card(data, CFG) is None


def test_too_many_pixels_is_refused_before_decoding(monkeypatch):
    monkeypatch.setattr("micropub_server.media.MAX_PIXELS", 1000)
    assert process_card(encode("PNG", (400, 300)), CFG) is None


def test_corrupt_image_gives_none():
    assert process_card(encode("PNG", (400, 300))[:200], CFG) is None


def test_output_is_small():
    card = process_card(encode("JPEG", (1600, 900), quality=85), CFG)
    assert 0 < len(card.content) < 200_000


# --- fetching ------------------------------------------------------------------------------------
URL = "https://img.example/pic.jpg"


async def test_fetches_and_processes(no_network):
    no_network.get(URL).respond(200, headers={"content-type": "image/jpeg"}, content=encode("JPEG"))
    card = await fetch_card(URL, public_resolver, CFG)
    assert card is not None and card.filename == "card.webp" and card.width == 800


async def test_follows_a_redirect(no_network):
    no_network.get("https://img.example/old").respond(302, headers={"location": "/pic.jpg"})
    no_network.get(URL).respond(200, content=encode("PNG"))
    assert await fetch_card("https://img.example/old", public_resolver, CFG) is not None


async def test_a_redirect_into_a_private_address_is_refused(no_network):
    no_network.get(URL).respond(302, headers={"location": "http://127.0.0.1/secret.png"})
    route = no_network.get("http://127.0.0.1/secret.png").respond(200, content=encode("PNG"))
    assert await fetch_card(URL, public_resolver, CFG) is None
    assert not route.called


async def test_private_addresses_are_never_fetched():
    assert await fetch_card("http://127.0.0.1/a.png", public_resolver, CFG) is None
    assert await fetch_card("http://192.168.1.1/a.png", public_resolver, CFG) is None


async def test_other_schemes_are_refused():
    assert await fetch_card("file:///etc/passwd", public_resolver, CFG) is None
    assert await fetch_card("ftp://img.example/a.png", public_resolver, CFG) is None


async def test_a_body_over_the_limit_is_refused_whatever_content_length_says(no_network):
    data = encode("PNG", (800, 600))
    cfg = MediaCard(max_bytes=len(data) - 1)
    no_network.get(URL).respond(200, headers={"content-length": "10"}, content=data)
    assert await fetch_card(URL, public_resolver, cfg) is None
    no_network.get(URL).respond(200, content=data)
    assert await fetch_card(URL, public_resolver, MediaCard(max_bytes=len(data))) is not None


@pytest.mark.parametrize("status", [404, 500])
async def test_error_statuses_give_none(no_network, status):
    no_network.get(URL).respond(status)
    assert await fetch_card(URL, public_resolver, CFG) is None


async def test_an_unreachable_host_gives_none(no_network):
    import httpx

    no_network.get(URL).mock(side_effect=httpx.ConnectError("down"))
    assert await fetch_card(URL, public_resolver, CFG) is None


async def test_svg_is_refused(no_network):
    no_network.get(URL).respond(
        200, headers={"content-type": "image/svg+xml"}, content=b"<svg xmlns='x'></svg>"
    )
    assert await fetch_card(URL, public_resolver, CFG) is None


async def test_time_running_out_gives_none(no_network):
    import asyncio

    async def slow(request):
        await asyncio.sleep(1)

    no_network.get(URL).mock(side_effect=slow)
    assert await fetch_card(URL, public_resolver, CFG, timeout=0.05) is None
