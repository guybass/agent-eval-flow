"""Materialize exact evidence bytes inside an explicitly owned local cache."""
import hashlib
import os
from pathlib import Path
import tempfile
from urllib.parse import urlparse
from urllib.request import url2pathname

from .. import objects as o


class ArtifactWriter:
    def __init__(self, cache, name, media_type):
        self.cache, self.name, self.media_type = cache, name, media_type
        self._file = tempfile.NamedTemporaryFile(mode="wb", prefix=".capture-", suffix=".tmp", dir=cache.root, delete=False)
        self._path = Path(self._file.name)
        self._hash = hashlib.sha256()
        self._finished = False

    def write(self, data):
        if self._finished:
            raise o.StorageError("Evidence writer has already finished")
        if not isinstance(data, bytes):
            raise TypeError("Evidence writer accepts bytes only")
        try:
            count = self._file.write(data)
            self._hash.update(data)
            return count
        except OSError as exc:
            self.abort()
            raise o.StorageError(f"Cannot spool evidence: {exc}") from exc

    def commit(self):
        if self._finished:
            raise o.StorageError("Evidence writer has already finished")
        digest = self._hash.hexdigest()
        target = self.cache._contained(self.cache.root / digest)
        try:
            self._file.flush()
            os.fsync(self._file.fileno())
            self._file.close()
            if target.exists():
                # Content-addressed evidence can already be open by an importer
                # on Windows. Reuse verified identical bytes instead of trying
                # to replace an open file or changing its existing references.
                with target.open("rb") as existing:
                    actual = hashlib.file_digest(existing, "sha256").hexdigest()
                if actual != digest:
                    raise o.StorageError("Existing cache object no longer matches its content hash")
                self._path.unlink()
            else:
                os.replace(self._path, target)
            self._finished = True
        except (OSError, o.StorageError) as exc:
            self.abort()
            raise o.StorageError(f"Cannot publish evidence: {exc}") from exc
        return o.ArtifactRef(uri=str(target), media_type=self.media_type, sha256=digest)

    def abort(self):
        self._file.close()
        self.cache._contained(self._path).unlink(missing_ok=True)
        self._finished = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if not self._finished:
            self.abort()


class ArtifactCache:
    def __init__(self, root):
        self.root = Path(root).resolve()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise o.StorageError(f"Cannot create artifact cache {self.root}: {exc}") from exc

    def _contained(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise o.StorageError(f"Evidence target is outside its configured cache: {path}")
        return path

    def open_writer(self, name, media_type):
        try:
            return ArtifactWriter(self, name, media_type)
        except OSError as exc:
            raise o.StorageError(f"Cannot open evidence writer: {exc}") from exc

    def write_bytes(self, name, data, media_type):
        with self.open_writer(name, media_type) as writer:
            writer.write(data)
            return writer.commit()

    def write_stream(self, name, stream, media_type):
        with self.open_writer(name, media_type) as writer:
            if hasattr(stream, "read"):
                while data := stream.read(1024 * 1024):
                    writer.write(data)
            else:
                for data in stream:
                    writer.write(data)
            return writer.commit()

    def verify(self, ref):
        issues = []
        def issue(message):
            issues.append(o.ValidationIssue(path=ref.uri, message=message, severity="error"))
        parsed = urlparse(ref.uri)
        if parsed.scheme == "file":
            if parsed.netloc and parsed.netloc != "localhost":
                issue("Remote file hosts are not fetched by local verification")
                return o.ValidationReport(issues=tuple(issues))
            path = Path(url2pathname(parsed.path))
        elif not parsed.scheme or (os.name == "nt" and len(parsed.scheme) == 1):
            path = Path(ref.uri)
        else:
            issue("This URI cannot be verified locally; no content was fetched")
            return o.ValidationReport(issues=tuple(issues))
        if ref.sha256 is None:
            issue("Evidence has no recorded SHA-256; integrity is unestablished")
        try:
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if ref.sha256 is not None and actual != ref.sha256:
                issue("Evidence bytes do not match their recorded SHA-256")
        except OSError as exc:
            issue(f"Evidence is unavailable: {exc}")
        return o.ValidationReport(issues=tuple(issues))
