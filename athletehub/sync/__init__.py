from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class SyncResult:
    source: str
    status: str
    imported: int = 0
    discovered: int = 0
    skipped: int = 0
    errors: int = 0
    message: str = ""
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
