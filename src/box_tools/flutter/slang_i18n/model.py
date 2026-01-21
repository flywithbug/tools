from __future__ import annotations

from pathlib import Path

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Locale:
    code: str
    name_en: str


@dataclass(frozen=True)
class Prompts:
    default_en: str
    by_locale_en: Dict[str, str]


@dataclass(frozen=True)
class Options:
    sort_keys: bool
    cleanup_extra_keys: bool
    incremental_translate: bool
    normalize_filenames: bool = True


@dataclass(frozen=True)
class ProjectConfig:
    openai_model: str
    source_locale: Locale
    target_locales: List[Locale]
    prompts: Prompts
    options: Options

    def target_codes(self) -> List[str]:
        return [x.code for x in self.target_locales]

    def target_name_en(self, code: str) -> str:
        for x in self.target_locales:
            if x.code == code:
                return x.name_en
        return code

@dataclass
class RedundantItem:
    group: str
    file: Path
    locale: str
    extra_keys: List[str]


@dataclass
class Progress:
    total_keys: int
    done_keys: int = 0
    started_at: float = 0.0

    def bump(self, n: int) -> None:
        self.done_keys += max(0, n)

    def percent(self) -> int:
        if self.total_keys <= 0:
            return 100
        return int(self.done_keys * 100 / self.total_keys)
