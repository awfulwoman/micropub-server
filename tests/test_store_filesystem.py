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


async def test_written_files_are_world_readable(tmp_path):
    # mkstemp creates 0600 files; a site served from this directory must be able to read them
    s = FileSystemStore(tmp_path)
    await s.commit([Put("a/b.md", b"x")], "m")
    mode = (tmp_path / "a" / "b.md").stat().st_mode & 0o777
    assert mode & 0o044 == 0o044


async def test_deleting_the_last_file_prunes_empty_directories(tmp_path):
    # Git has no empty directories; the dev store should not leave them behind either
    s = FileSystemStore(tmp_path)
    await s.commit([Put("a/b/c/x.md", b"1"), Put("a/keep.md", b"2")], "m")
    await s.commit([Delete("a/b/c/x.md")], "m")
    assert not (tmp_path / "a" / "b").exists()
    assert (tmp_path / "a" / "keep.md").exists() and tmp_path.exists()


async def test_moving_prunes_the_old_directory(tmp_path):
    s = FileSystemStore(tmp_path)
    await s.commit([Put("old/index.md", b"1")], "m")
    await s.commit([Move("old/index.md", "new/index.md")], "m")
    assert not (tmp_path / "old").exists() and (tmp_path / "new" / "index.md").exists()


async def test_deleting_a_bundle_file_keeps_the_bundle(tmp_path):
    s = FileSystemStore(tmp_path)
    await s.commit([Put("b/index.md", b"1"), Put("b/card.webp", b"2")], "m")
    await s.commit([Put("b/index.md", b"3"), Delete("b/card.webp")], "m")
    assert (tmp_path / "b" / "index.md").read_bytes() == b"3"
    assert not (tmp_path / "b" / "card.webp").exists()
