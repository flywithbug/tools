from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Literal, Set, Tuple


# =========================
# Constants / Conventions
# =========================

# 保留的 meta keys：不参与翻译、不参与冗余检查、永不删除
META_KEYS: Set[str] = {"@@locale"}


# =========================
# Config Models
# =========================

@dataclass(frozen=True)
class LocaleSpec:
    """Locale definition from config."""
    code: str
    name_en: str = ""


@dataclass(frozen=True)
class Options:
    """Behavior flags from config."""
    sort_keys: bool = True
    incremental_translate: bool = True
    cleanup_extra_keys: bool = True
    normalize_filenames: bool = True


@dataclass(frozen=True)
class SlangI18nConfig:
    """Normalized config loaded from slang_i18n.yaml."""
    i18n_dir: Path
    source_locale: LocaleSpec
    target_locales: Tuple[LocaleSpec, ...] = field(default_factory=tuple)

    openai_model: str = "gpt-4o"
    prompt_by_locale: Dict[str, str] = field(default_factory=dict)

    options: Options = field(default_factory=Options)

    @property
    def all_locales(self) -> Tuple[str, ...]:
        """(source + targets) locale codes."""
        codes = [self.source_locale.code]
        codes.extend([x.code for x in self.target_locales])
        return tuple(codes)


# =========================
# Layout Models
# =========================

I18nMode = Literal["single", "multi"]


@dataclass(frozen=True)
class LocaleFile:
    """A locale file on disk (or expected to exist)."""
    locale: str
    path: Path
    exists: bool = False


@dataclass(frozen=True)
class I18nGroup:
    """
    One business group.
    - single mode: use a synthetic group name, e.g. "default"
    - multi mode: group name is folder name, e.g. "home", "trade"
    """
    name: str
    prefix: str
    dir_path: Path
    files: Dict[str, LocaleFile] = field(default_factory=dict)  # locale -> LocaleFile

    def file_for(self, locale: str) -> Optional[LocaleFile]:
        return self.files.get(locale)


@dataclass(frozen=True)
class ProjectLayout:
    """Resolved project layout after scanning i18nDir."""
    mode: I18nMode
    i18n_dir: Path
    groups: Tuple[I18nGroup, ...] = field(default_factory=tuple)

    def all_files(self) -> Tuple[LocaleFile, ...]:
        out: List[LocaleFile] = []
        for g in self.groups:
            out.extend(list(g.files.values()))
        return tuple(out)


# =========================
# JSON File State Models
# =========================

@dataclass(frozen=True)
class JsonStructureInfo:
    """
    Result of validating JSON constraints:
    - must be a flat object
    - values must be strings
    - no nested object/array
    """
    is_object: bool
    is_flat: bool
    # nested paths: ("home",) means key "home" holds object/array
    nested_paths: Tuple[Tuple[str, ...], ...] = field(default_factory=tuple)
    # non-string value paths (excluding META_KEYS if needed)
    non_string_paths: Tuple[Tuple[str, ...], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class LocaleMetaInfo:
    """
    Validation for @@locale meta key.
    """
    present: bool
    value: Optional[str] = None
    matches_expected: bool = True
    is_first_key: bool = True


@dataclass(frozen=True)
class JsonFileState:
    """
    Parsed state of a JSON file.
    Note: `data` is expected to be a dict[str, str] after validation/normalization.
    """
    path: Path
    expected_locale: str

    # Raw loaded JSON (may contain non-string values before normalization)
    raw: Optional[object] = None

    # Validated flat dict (excluding invalid structures). Set by json_ops.
    data: Dict[str, str] = field(default_factory=dict)

    structure: Optional[JsonStructureInfo] = None
    locale_meta: Optional[LocaleMetaInfo] = None

    @property
    def is_valid_flat(self) -> bool:
        return bool(self.structure and self.structure.is_object and self.structure.is_flat)

    @property
    def has_nested(self) -> bool:
        return bool(self.structure and self.structure.nested_paths)

    @property
    def has_non_string(self) -> bool:
        return bool(self.structure and self.structure.non_string_paths)

    @property
    def is_locale_meta_ok(self) -> bool:
        return bool(self.locale_meta and self.locale_meta.present and self.locale_meta.matches_expected and self.locale_meta.is_first_key)


# =========================
# Reporting Models (doctor/check/translate output)
# =========================

class IssueLevel(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class IssueCode(str, Enum):
    # Config / layout
    CONFIG_MISSING = "config_missing"
    CONFIG_INVALID = "config_invalid"
    I18N_DIR_MISSING = "i18n_dir_missing"
    PREFIX_CONFLICT = "prefix_conflict"

    # JSON structure
    JSON_INVALID = "json_invalid"
    JSON_NOT_FLAT = "json_not_flat"
    JSON_NON_STRING = "json_non_string"

    # @@locale meta
    LOCALE_META_MISSING = "locale_meta_missing"
    LOCALE_META_MISMATCH = "locale_meta_mismatch"
    LOCALE_META_NOT_FIRST = "locale_meta_not_first"

    # Keys
    MISSING_KEYS = "missing_keys"
    EXTRA_KEYS = "extra_keys"


@dataclass(frozen=True)
class Issue:
    level: IssueLevel
    code: IssueCode
    message: str

    # Optional context
    path: Optional[Path] = None
    group: Optional[str] = None
    locale: Optional[str] = None

    # Optional details (keep small & structured)
    details: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Report:
    """
    A structured result for an action run. Suitable for printing or tests.
    """
    action: str
    issues: Tuple[Issue, ...] = field(default_factory=tuple)

    # Summaries / counters
    files_scanned: int = 0
    files_changed: int = 0
    keys_added: int = 0
    keys_removed: int = 0
    keys_translated: int = 0

    @property
    def ok(self) -> bool:
        return all(i.level != IssueLevel.ERROR for i in self.issues)

    def counts_by_level(self) -> Dict[str, int]:
        d: Dict[str, int] = {"info": 0, "warn": 0, "error": 0}
        for i in self.issues:
            d[i.level.value] = d.get(i.level.value, 0) + 1
        return d


# =========================
# Action Runtime Options
# =========================

@dataclass(frozen=True)
class RuntimeOptions:
    """
    CLI runtime options, not from YAML:
    - dry_run: don't write files
    - full: translate full overwrite instead of incremental
    """
    dry_run: bool = False
    full_translate: bool = False
    config_path: Optional[Path] = None
