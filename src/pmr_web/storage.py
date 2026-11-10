"""Document storage: a local folder, or an Azure Blob Storage container. Both have the same interface.

Keys are forward-slash paths:
  reports/<id>/inputs/<file>                  the uploaded drop folder for one report
  reports/<id>/output/<CODE>_<QUARTER>/...    everything the pipeline writes (facts, manifest, PDFs)
  reports/<id>/output/.../review.json         the reviewer's corrections and approval
  llm_cache/<hash>.json                       stored model answers, shared by every worker

The pipeline reads and writes ordinary files, so a worker copies a report's keys into a temporary
folder, runs, and copies the results back.
"""

import shutil
from pathlib import Path


class Storage:
    def read(self, key: str) -> bytes: ...
    def write(self, key: str, data: bytes) -> None: ...
    def exists(self, key: str) -> bool: ...
    def list(self, prefix: str) -> list[str]: ...
    def delete(self, key: str) -> None: ...

    def download_tree(self, prefix: str, dest: Path) -> int:
        """Copy every key under `prefix` into `dest`, keeping the relative layout."""
        keys = self.list(prefix)
        for key in keys:
            path = dest / key[len(prefix):].lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.read(key))
        return len(keys)

    def upload_tree(self, src: Path, prefix: str, skip: set[str] = frozenset()) -> int:
        """Copy every file under `src` to keys under `prefix`, except relative paths in `skip`."""
        n = 0
        for path in sorted(p for p in src.rglob("*") if p.is_file()):
            rel = path.relative_to(src).as_posix()
            if rel not in skip:
                self.write(f"{prefix.rstrip('/')}/{rel}", path.read_bytes())
                n += 1
        return n


class LocalStorage(Storage):
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        # Keys come from code, but a crafted upload name must still never escape the root.
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"key outside storage: {key!r}")
        return path

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def write(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list(self, prefix: str) -> list[str]:
        base = self._path(prefix)
        if not base.is_dir():
            return []
        return sorted(p.relative_to(self.root.resolve()).as_posix() for p in base.rglob("*") if p.is_file())

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_tree(self, prefix: str) -> None:
        shutil.rmtree(self._path(prefix), ignore_errors=True)


class BlobStorage(Storage):
    """Azure Blob Storage, reached with the app's managed identity: no account key exists in config."""

    def __init__(self, account_url: str, container: str):
        # Imported here so local runs need neither package installed.
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
        self.container = service.get_container_client(container)

    def read(self, key: str) -> bytes:
        return self.container.download_blob(key).readall()

    def write(self, key: str, data: bytes) -> None:
        self.container.upload_blob(key, data, overwrite=True)

    def exists(self, key: str) -> bool:
        return self.container.get_blob_client(key).exists()

    def list(self, prefix: str) -> list[str]:
        return sorted(b.name for b in self.container.list_blobs(name_starts_with=prefix.rstrip("/") + "/"))

    def delete(self, key: str) -> None:
        self.container.delete_blob(key, delete_snapshots="include")


def open_storage(settings) -> Storage:
    if settings.storage == "azure":
        return BlobStorage(settings.blob_account_url, settings.blob_container)
    return LocalStorage(settings.data_dir / "storage")
