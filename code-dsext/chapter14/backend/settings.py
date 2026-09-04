from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class SettingsError(ValueError):
    code: str
    detail: str

    def __post_init__(self) -> None:
        ValueError.__init__(self, self.detail)

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@dataclass(frozen=True, slots=True)
class Settings:
    workspace_root: Path | str
    state_dir: Path | str
    offline: bool = True
    read_only: bool = True
    production_control: Literal["prohibited"] = "prohibited"
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_root", Path(self.workspace_root).expanduser())
        object.__setattr__(self, "state_dir", Path(self.state_dir).expanduser())
        if type(self.offline) is not bool or self.offline is not True:
            raise SettingsError("offline_required", "offline 必须严格为 True")
        if type(self.read_only) is not bool or self.read_only is not True:
            raise SettingsError("read_only_required", "read_only 必须严格为 True")
        if self.production_control != "prohibited":
            raise SettingsError(
                "production_control_prohibited",
                "production_control 必须为 prohibited",
            )
        origins = tuple(self.cors_origins)
        if not origins or any(not self._is_localhost_origin(origin) for origin in origins):
            raise SettingsError(
                "cors_origins_local_only",
                "cors_origins 只能包含 localhost origins",
            )
        object.__setattr__(self, "cors_origins", origins)

    @staticmethod
    def _is_localhost_origin(origin: str) -> bool:
        if not isinstance(origin, str):
            return False
        parsed = urlparse(origin)
        return (
            parsed.scheme in {"http", "https"}
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            and not parsed.path
            and not parsed.params
            and not parsed.query
            and not parsed.fragment
        )

    def resolve(self) -> Settings:
        workspace_root = Path(self.workspace_root).resolve(strict=False)
        state_dir = Path(self.state_dir).resolve(strict=False)
        try:
            state_dir.relative_to(workspace_root)
        except ValueError:
            return replace(
                self,
                workspace_root=workspace_root,
                state_dir=state_dir,
            )
        raise SettingsError(
            "state_dir_outside_workspace",
            "state_dir 必须位于 workspace_root 外部",
        )
