"""Zip archives in the drop folder: unpacked under the archive's name, by the CLI into a staged copy."""

import io
import zipfile

from pmr import archive, cli


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_stage_unpacks_zips_and_leaves_the_drop_folder_alone(tmp_path):
    src, dest = tmp_path / "in", tmp_path / "staged"
    (src / "sub").mkdir(parents=True)
    (src / "a.pdf").write_bytes(b"a")
    (src / "sub" / "drop.zip").write_bytes(_zip({"drop/b.xlsx": b"b", "drop/deep/c.pdf": b"c", "../d.pdf": b"d",
                                                 "__MACOSX/drop/._b.xlsx": b"x", "drop/inner.zip": b"x"}))
    (src / "bad.zip").write_bytes(b"not a zip")
    unpacked, skipped = archive.stage(src, dest)
    files = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    # "../d.pdf" is dropped with the hidden files, so nothing lands outside the staged copy.
    assert files == ["a.pdf", "sub/drop/b.xlsx", "sub/drop/deep/c.pdf"]
    assert not (tmp_path / "d.pdf").exists() and not (dest / "sub" / "d.pdf").exists()
    assert unpacked == 1 and skipped == ["bad.zip", "sub/drop.zip/drop/inner.zip"]
    assert sorted(p.name for p in src.rglob("*") if p.is_file()) == ["a.pdf", "bad.zip", "drop.zip"]


def test_an_archive_over_the_limits_is_refused(monkeypatch):
    monkeypatch.setattr(archive, "MAX_MEMBERS", 1)
    try:
        list(archive.unpack(_zip({"a.pdf": b"a", "b.pdf": b"b"}), "x.zip"))
    except zipfile.BadZipFile:
        return
    raise AssertionError("an archive over the member limit was unpacked")


def test_cli_builds_from_a_zipped_drop_folder(inputs, tmp_path, capsys):
    src = tmp_path / "in"
    src.mkdir()
    with zipfile.ZipFile(src / "drop.zip", "w") as zf:
        for p in sorted(inputs.rglob("*")):
            if p.is_file():
                zf.write(p, f"drop/{p.relative_to(inputs).as_posix()}")
    code = cli.main(["--client", "CPERS", "--quarter", "4Q25", "--inputs", str(src), "--out", str(tmp_path / "out"),
                     "--no-llm", "--cache", str(tmp_path / "cache")])
    out = capsys.readouterr().out
    assert code == 0
    assert "Unpacked 1 zip archive(s)" in out
    assert "[use] drop/flash_4Q25.xlsx" in out
    assert [p.name for p in src.iterdir()] == ["drop.zip"]
