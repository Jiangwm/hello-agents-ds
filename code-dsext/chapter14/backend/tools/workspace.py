from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PureWindowsPath
from typing import Mapping, TypeAlias


_ALLOWED_SUFFIXES = frozenset({".csv", ".json", ".md"})
_SENSITIVE_SUFFIXES = frozenset({".env", ".pem", ".key", ".p12", ".pfx", ".cer", ".crt", ".der"})
JsonScalar: TypeAlias = bool | int | float | str | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class WorkspaceError(ValueError):
    code: str
    detail: str

    def __post_init__(self) -> None:
        ValueError.__init__(self, self.detail)

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


class ManifestError(WorkspaceError):
    """Manifest violates the trusted offline workspace contract."""


class WorkspacePathError(WorkspaceError):
    """A requested path leaves the manifest-governed read boundary."""


class WorkspaceIntegrityError(WorkspaceError):
    """A manifested resource is missing, malformed, or has changed."""


@dataclass(frozen=True, slots=True)
class CsvReadResult:
    resource: str
    version: str
    domain: str
    permission: str
    data_window: str
    content_hash: str
    query: dict[str, str]
    no_result: bool
    rows: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class DocumentReadResult:
    resource: str
    version: str
    domain: str
    permission: str
    data_window: str
    content_hash: str
    query: str
    no_result: bool
    content: str


@dataclass(frozen=True, slots=True)
class _Resource:
    path: str
    domain: str
    permission: str
    version: str
    sha256: str


class ReadOnlyWorkspace:
    """Manifest-whitelisted workspace that never mutates source artifacts."""

    def __init__(self, root: str | Path) -> None:
        candidate = Path(root).expanduser()
        if self._is_link(candidate):
            raise WorkspacePathError("symlink", "workspace root cannot be a symlink")
        try:
            self.root = candidate.resolve(strict=True)
        except OSError as error:
            raise WorkspacePathError("workspace_root", "workspace root must exist") from error
        if not self.root.is_dir():
            raise WorkspacePathError("workspace_root", "workspace root must be a directory")
        manifest_path = self.root / "manifest.json"
        if self._is_link(manifest_path):
            raise WorkspacePathError("symlink", "manifest cannot be a symlink")
        payload = self._load_manifest(manifest_path)
        self._validate_policy(payload)
        self.data_window = self._data_window(payload)
        self.version = self._string(payload, "version")
        raw_resources = payload.get("resources")
        if not isinstance(raw_resources, list) or not raw_resources:
            raise ManifestError("manifest_resources", "resources must be a non-empty list")
        resources = tuple(self._parse_resource(value) for value in raw_resources)
        if len({item.path for item in resources}) != len(resources):
            raise ManifestError("duplicate_resource", "resource paths must be unique")
        indexed: dict[str, _Resource] = {}
        for item in resources:
            path = self._resolve_manifest_path(item.path)
            if self._sha256(path) != item.sha256:
                raise WorkspaceIntegrityError("hash_mismatch", item.path)
            indexed[item.path] = item
        self._resources = indexed

    def read_csv(
        self,
        resource: str,
        *,
        query: Mapping[str, str] | None = None,
    ) -> CsvReadResult:
        item, path = self._read_target(resource, ".csv")
        selected = self._query_mapping(query)
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames is None:
                    raise WorkspaceIntegrityError("csv_header", resource)
                rows = tuple(
                    dict(row)
                    for row in reader
                    if all(row.get(key) == value for key, value in selected.items())
                )
        except (OSError, UnicodeDecodeError, csv.Error) as error:
            raise WorkspaceIntegrityError("csv_read", resource) from error
        return CsvReadResult(
            resource=item.path,
            version=item.version,
            domain=item.domain,
            permission=item.permission,
            data_window=self.data_window,
            content_hash=item.sha256,
            query=selected,
            no_result=not rows,
            rows=rows,
        )

    def read_document(self, resource: str, *, query: str = "") -> DocumentReadResult:
        item, path = self._read_target(resource, ".json,.md")
        if not isinstance(query, str):
            raise WorkspacePathError("invalid_query", "document query must be a string")
        try:
            content = path.read_text(encoding="utf-8")
            if path.suffix.casefold() == ".json":
                json.loads(content)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkspaceIntegrityError("document_read", resource) from error
        found = not query or query.casefold() in content.casefold()
        return DocumentReadResult(
            resource=item.path,
            version=item.version,
            domain=item.domain,
            permission=item.permission,
            data_window=self.data_window,
            content_hash=item.sha256,
            query=query,
            no_result=not found,
            content=content,
        )

    def current_hash(self, resource: str) -> str | None:
        item = self._resources.get(resource)
        if item is None:
            raise WorkspacePathError("not_manifested", resource)
        try:
            path = self._resolve_path(resource, must_exist=True)
        except WorkspacePathError as error:
            if error.code == "missing_resource":
                return None
            raise
        return self._sha256(path)

    def _read_target(self, resource: str, suffixes: str) -> tuple[_Resource, Path]:
        self._validate_relative(resource)
        item = self._resources.get(resource)
        if item is None:
            raise WorkspacePathError("not_manifested", resource)
        if Path(resource).suffix.casefold() not in suffixes.split(","):
            raise WorkspacePathError("resource_type", resource)
        path = self._resolve_path(resource, must_exist=True)
        if self._sha256(path) != item.sha256:
            raise WorkspaceIntegrityError("hash_mismatch", resource)
        return item, path

    @staticmethod
    def _load_manifest(path: Path) -> dict[str, JsonValue]:
        try:
            value: JsonValue = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ManifestError("manifest_invalid", "manifest.json is not valid JSON") from error
        if not isinstance(value, dict):
            raise ManifestError("manifest_shape", "manifest must be an object")
        return value

    @classmethod
    def _parse_resource(cls, value: JsonValue) -> _Resource:
        if not isinstance(value, dict):
            raise ManifestError("resource_shape", "resource must be an object")
        item = _Resource(
            path=cls._string(value, "path"),
            domain=cls._string(value, "domain"),
            permission=cls._string(value, "permission"),
            version=cls._string(value, "version"),
            sha256=cls._string(value, "sha256").casefold(),
        )
        if len(item.sha256) != 64 or any(char not in "0123456789abcdef" for char in item.sha256):
            raise ManifestError("resource_hash", item.path)
        return item

    @staticmethod
    def _string(payload: dict[str, JsonValue], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ManifestError("manifest_field", key)
        return value

    @classmethod
    def _data_window(cls, payload: dict[str, JsonValue]) -> str:
        value = payload.get("source_window")
        if not isinstance(value, dict):
            raise ManifestError("data_window", "source_window must be an object")
        return f"{cls._string(value, 'start')}/{cls._string(value, 'end')}"

    @staticmethod
    def _validate_policy(payload: dict[str, JsonValue]) -> None:
        if payload.get("read_only") is not True:
            raise ManifestError("read_only_required", "manifest must be read-only")
        if payload.get("production_control") != "prohibited":
            raise ManifestError("read_only_required", "production control must be prohibited")
        permission = payload.get("permission")
        if not isinstance(permission, dict) or permission.get("mode") != "read-only":
            raise ManifestError("permission_mode", "permission mode must be read-only")

    def _resolve_manifest_path(self, resource: str) -> Path:
        self._validate_relative(resource)
        return self._resolve_path(resource, must_exist=True)

    def _resolve_path(self, resource: str, *, must_exist: bool) -> Path:
        path = self.root.joinpath(*Path(resource).parts)
        for index in range(1, len(path.parts) + 1):
            part = Path(*path.parts[:index])
            if part.exists() and self._is_link(part):
                raise WorkspacePathError("symlink", resource)
        if must_exist and (not path.exists() or not path.is_file()):
            raise WorkspacePathError("missing_resource", resource)
        try:
            path.resolve(strict=must_exist).relative_to(self.root)
        except (OSError, ValueError) as error:
            raise WorkspacePathError("root_escape", resource) from error
        return path

    @staticmethod
    def _validate_relative(resource: str) -> None:
        if not isinstance(resource, str) or not resource.strip():
            raise WorkspacePathError("invalid_path", "resource must be a relative path")
        windows = PureWindowsPath(resource)
        path = Path(resource)
        if path.is_absolute() or windows.is_absolute() or ".." in path.parts or ".." in windows.parts:
            raise WorkspacePathError("path_traversal", resource)
        suffix = path.suffix.casefold()
        if suffix in _SENSITIVE_SUFFIXES:
            raise WorkspacePathError("sensitive_suffix", resource)
        if suffix not in _ALLOWED_SUFFIXES:
            raise WorkspacePathError("resource_type", resource)

    @staticmethod
    def _query_mapping(query: Mapping[str, str] | None) -> dict[str, str]:
        selected = dict(query or {})
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in selected.items()):
            raise WorkspacePathError("invalid_query", "CSV query values must be strings")
        return selected

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _is_link(path: Path) -> bool:
        return path.is_symlink() or path.is_junction()

