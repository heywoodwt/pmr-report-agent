"""Zip archives in a drop folder: unpacked into their documents, under the archive's own name.

The web app unpacks an uploaded zip into storage; the CLI stages a copy of the drop folder with
each zip unpacked in place. Both use `unpack`, so a zip yields the same input paths either way.
"""

import io
import shutil
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

# An archive is unpacked only within these limits, so a small zip cannot fill the disk.
MAX_MEMBERS = 2000
MAX_BYTES = 1024 * 1024 * 1024


def safe_name(raw: str) -> str | None:
    """A relative path with no empty, '.' or '..' parts, or None if nothing is left."""
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return "/".join(parts) or None


def unpack(data: bytes, rel: str, clean: Callable[[str], str | None] = safe_name) -> Iterator[tuple[str, bytes | None]]:
    """Yields (path, contents) for each document in the zip `rel`, with paths under the archive's name.

    Contents are None for a member that `clean` rejects or that is itself a zip, which is not unpacked.
    Raises zipfile.BadZipFile for an archive that is corrupt or over the limits.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        # file_size is what each member inflates to; reads stop there even if the header lies.
        if len(members) > MAX_MEMBERS or sum(i.file_size for i in members) > MAX_BYTES:
            raise zipfile.BadZipFile(f"{rel} is over {MAX_MEMBERS} files or {MAX_BYTES >> 20} MiB unpacked")
        # A zipped folder "drop/" inside "drop.zip" is not nested twice.
        stem = str(Path(rel).with_suffix(""))
        top = Path(stem).name
        for i in members:
            parts = i.filename.replace("\\", "/").split("/")
            # macOS resource forks, hidden files and Excel lock files are not documents.
            if parts[0] == "__MACOSX" or any(p.startswith(".") for p in parts) or parts[-1].startswith("~$"):
                continue
            name = clean(i.filename)
            if name is None or name.lower().endswith(".zip"):
                yield f"{rel}/{i.filename}", None
                continue
            if name.startswith(f"{top}/"):
                name = name[len(top) + 1:]
            yield f"{stem}/{name}", zf.read(i)


def stage(inputs: Path, dest: Path) -> tuple[int, list[str]]:
    """Copies the drop folder into dest with each zip replaced by its documents.

    Returns the number of archives unpacked and the names skipped: members unpack rejects, and
    archives that are corrupt or over the limits.
    """
    shutil.copytree(inputs, dest, dirs_exist_ok=True, ignore=lambda _, names: [n for n in names if n.lower().endswith(".zip")])
    unpacked, skipped = 0, []
    for z in sorted(p for p in inputs.rglob("*") if p.is_file() and p.suffix.lower() == ".zip"):
        rel = z.relative_to(inputs).as_posix()
        try:
            files = list(unpack(z.read_bytes(), rel))
        except zipfile.BadZipFile:
            skipped.append(rel)
            continue
        for path, data in files:
            if data is None:
                skipped.append(path)
                continue
            (dest / path).parent.mkdir(parents=True, exist_ok=True)
            (dest / path).write_bytes(data)
        unpacked += 1
    return unpacked, skipped
