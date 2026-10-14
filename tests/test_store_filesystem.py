import pytest

from micropub_server.stores import Delete, FileSystemStore, Move, Put, StoreError


async def test_put_and_read(tmp_path):
    s = FileSystemStore(tmp_path)
    await s.commit([Put("content/notes/a.md", b"hi")], "m")
    assert await s.read("content/notes/a.md") == b"hi"
    assert await s.exists("content/notes/a.md")
    assert not await s.exists("content/notes/b.md")


async def test_delete_and_move(tmp_path):
    s = FileSystemStore(tmp_path)
    await s.commit([Put("a.md", b"x")], "m")
    await s.commit([Move("a.md", "sub/b.md")], "m")
    assert not await s.exists("a.md")
    assert await s.read("sub/b.md") == b"x"
    await s.commit([Delete("sub/b.md")], "m")
    assert not await s.exists("sub/b.md")


async def test_delete_missing_raises(tmp_path):
    with pytest.raises(StoreError) as e:
        await FileSystemStore(tmp_path).commit([Delete("nope.md")], "m")
    assert e.value.status == 404


async def test_path_escape_rejected(tmp_path):
    with pytest.raises(StoreError):
        await FileSystemStore(tmp_path).commit([Put("../evil.md", b"x")], "m")


async def test_check(tmp_path):
    assert await FileSystemStore(tmp_path).check() is None
    assert "does not exist" in await FileSystemStore(tmp_path / "nope").check()
