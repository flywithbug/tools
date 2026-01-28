from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml


DEFAULT_TEMPLATE_NAME = "gpt_json.yaml"


class ConfigError(Exception):
    pass


# -----------------------------
# Config dataclasses
# -----------------------------

@dataclass(frozen=True)
class Locale:
    code: str
    name_en: str = ""


@dataclass(frozen=True)
class LayoutRoot:
    pattern: str = "{code}{suffix}.json"


@dataclass(frozen=True)
class LayoutModule:
    pattern: str = "{folder}_{code}{suffix}.json"


@dataclass(frozen=True)
class LayoutRules:
    allow_unmatched_files: bool = False
    allow_nested_modules: bool = False


@dataclass(frozen=True)
class Layout:
    mode: str = "auto"  # auto/root/module
    root: LayoutRoot = LayoutRoot()
    module: LayoutModule = LayoutModule()
    rules: LayoutRules = LayoutRules()


@dataclass(frozen=True)
class Config:
    project_root: Path
    cfg_path: Path
    i18n_dir: Path

    openai_model: str
    max_workers: int

    source: Locale
    targets: List[Locale]

    file_suffix: str

    layout: Layout
    options: Dict[str, Any]
    prompts: Dict[str, Any]
    placeholder: Dict[str, Any]


# -----------------------------
# Menu
# -----------------------------

def run_menu(cfg_path: Path, project_root: Path) -> int:
    menu = [
        ("sort",      "排序 + 检查（冗余/重复/占位符）(自动 sync)"),
        ("translate", "翻译（缺目标文件自动创建）"),
        ("doctor",    "结构诊断"),
        ("sync",      "目录/文件同步检查"),
        ("init",      "生成/校验配置"),
    ]

    while True:
        print("\n=== box_json_i18n ===")
        for idx, (cmd, label) in enumerate(menu, start=1):
            print(f"{idx}. {cmd:<10} {label}")
        print("0. exit       退出")

        choice = input("> ").strip()
        if choice == "0":
            return 0
        if not choice.isdigit():
            print("无效选择")
            continue
        idx = int(choice)
        if not (1 <= idx <= len(menu)):
            print("无效选择")
            continue

        cmd = menu[idx - 1][0]
        print(f"请在命令行执行：box_json_i18n {cmd} --config {cfg_path} --project-root {project_root}")
        return 0


# -----------------------------
# Public API
# -----------------------------

def init_config(project_root: Path, cfg_path: Path, yes: bool = False) -> None:
    if not cfg_path.exists():
        _write_default_config(cfg_path)

    assert_config_ok(cfg_path, project_root=project_root, check_i18n_dir_exists=False)
    cfg = load_config(cfg_path, project_root=project_root)

    if not cfg.i18n_dir.exists():
        if yes:
            cfg.i18n_dir.mkdir(parents=True, exist_ok=True)
        else:
            # init 通常也可以直接创建 i18nDir，避免下一步卡住
            cfg.i18n_dir.mkdir(parents=True, exist_ok=True)


def assert_config_ok(cfg_path: Path, project_root: Path, check_i18n_dir_exists: bool) -> None:
    if not cfg_path.exists():
        raise ConfigError(
            f"配置文件不存在：{cfg_path}\n"
            f"解决方法：在项目根目录执行 `box_json_i18n init` 生成默认配置。"
        )

    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    errs = _validate_config_dict(raw)
    if errs:
        raise ConfigError("配置文件不合法：\n- " + "\n- ".join(errs))

    if check_i18n_dir_exists:
        i18n_dir = _resolve_i18n_dir(project_root, raw.get("i18nDir", "i18n"))
        if not i18n_dir.exists():
            raise ConfigError(
                f"i18nDir 不存在：{i18n_dir}\n"
                f"解决方法：执行 `box_json_i18n sync --yes` 创建缺失目录/文件。"
            )


def load_config(cfg_path: Path, project_root: Path) -> Config:
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    i18n_dir = _resolve_i18n_dir(project_root, raw.get("i18nDir", "i18n"))
    file_suffix = str(raw.get("fileSuffix", ""))

    source = raw.get("source_locale") or {}
    targets = raw.get("target_locales") or []

    layout_raw = raw.get("layout") or {}
    layout = Layout(
        mode=str(layout_raw.get("mode", "auto")),
        root=LayoutRoot(pattern=str((layout_raw.get("root") or {}).get("pattern", "{code}{suffix}.json"))),
        module=LayoutModule(pattern=str((layout_raw.get("module") or {}).get("pattern", "{folder}_{code}{suffix}.json"))),
        rules=LayoutRules(
            allow_unmatched_files=bool((layout_raw.get("rules") or {}).get("allow_unmatched_files", False)),
            allow_nested_modules=bool((layout_raw.get("rules") or {}).get("allow_nested_modules", False)),
        ),
    )

    return Config(
        project_root=project_root,
        cfg_path=cfg_path,
        i18n_dir=i18n_dir,
        openai_model=str(raw.get("openAIModel", "gpt-4o")),
        max_workers=int(raw.get("maxWorkers", 0)),
        source=Locale(code=str(source.get("code", "en")), name_en=str(source.get("name_en", ""))),
        targets=[Locale(code=str(t.get("code", "")), name_en=str(t.get("name_en", ""))) for t in targets],
        file_suffix=file_suffix,
        layout=layout,
        options=dict(raw.get("options") or {}),
        prompts=dict(raw.get("prompts") or {}),
        placeholder=dict(raw.get("placeholder") or {}),
    )


def override_i18n_dir(cfg: Config, i18n_dir: Path) -> Config:
    return replace(cfg, i18n_dir=i18n_dir)


# -----------------------------
# Layout & expected paths
# -----------------------------

def detect_mode(cfg: Config) -> str:
    mode = cfg.layout.mode
    if mode in ("root", "module"):
        return mode

    # auto:
    if not cfg.i18n_dir.exists():
        return "root"
    for p in cfg.i18n_dir.iterdir():
        if p.is_dir():
            return "module"
    return "root"


def list_modules(cfg: Config) -> List[str]:
    if not cfg.i18n_dir.exists():
        return []
    return sorted([p.name for p in cfg.i18n_dir.iterdir() if p.is_dir()])


def expected_files(cfg: Config) -> List[Path]:
    mode = detect_mode(cfg)
    suffix = cfg.file_suffix or ""
    locales = [cfg.source.code] + [t.code for t in cfg.targets]

    out: List[Path] = []
    if mode == "root":
        pat = cfg.layout.root.pattern
        for code in locales:
            name = pat.format(code=code, suffix=suffix)
            out.append(cfg.i18n_dir / name)
    else:
        pat = cfg.layout.module.pattern
        for folder in list_modules(cfg):
            for code in locales:
                name = pat.format(folder=folder, code=code, suffix=suffix)
                out.append(cfg.i18n_dir / folder / name)
    return out


def compute_missing(cfg: Config) -> Tuple[List[Path], List[Path]]:
    """
    返回 (missing_dirs, missing_files)
    注意：模块模式下模块目录是“已存在模块”决定的；sync 不会凭空创建新模块目录（除非你后续加 modules 配置）
    """
    missing_dirs: Set[Path] = set()
    missing_files: Set[Path] = set()

    if not cfg.i18n_dir.exists():
        missing_dirs.add(cfg.i18n_dir)
        return (sorted(missing_dirs), [])

    for f in expected_files(cfg):
        if not f.parent.exists():
            missing_dirs.add(f.parent)
        if not f.exists():
            missing_files.add(f)

    return (sorted(missing_dirs), sorted(missing_files))


def create_missing(cfg: Config, missing_dirs: List[Path], missing_files: List[Path]) -> None:
    for d in missing_dirs:
        d.mkdir(parents=True, exist_ok=True)
    for f in missing_files:
        f.parent.mkdir(parents=True, exist_ok=True)
        if not f.exists():
            f.write_text("{}\n", encoding="utf-8")


# -----------------------------
# Commands
# -----------------------------

def run_sync(cfg: Config, yes: bool) -> int:
    missing_dirs, missing_files = compute_missing(cfg)

    if not missing_dirs and not missing_files:
        print(f"[sync] OK：目录/文件齐全（mode={detect_mode(cfg)}）")
        return 0

    print(f"[sync] 检测到缺失（mode={detect_mode(cfg)}）：")
    if missing_dirs:
        print("  - 缺失目录：")
        for d in missing_dirs:
            print(f"    * {d}")
    if missing_files:
        print("  - 缺失文件：")
        for f in missing_files:
            print(f"    * {f}")

    if not yes:
        print("[sync] 未启用 --yes，本次仅输出缺失清单，不自动创建。")
        return 1

    create_missing(cfg, missing_dirs, missing_files)
    print("[sync] 已创建缺失目录/文件。")
    return 0


def run_sort(cfg: Config, yes: bool) -> int:
    # TODO: 这里后续实现具体检查/清理/排序。
    print(f"[sort] (skeleton) i18nDir={cfg.i18n_dir} yes={yes}")
    return 0


def run_doctor(cfg: Config) -> int:
    """
    你要求：启动默认 doctor，有问题提示，无问题放行。
    所以 doctor 的返回码非常关键：
    - 0：通过
    - 非 0：阻止继续
    """
    problems: List[str] = []

    # 1) 模式约束
    mode = detect_mode(cfg)
    if cfg.layout.mode == "root" and cfg.i18n_dir.exists():
        if any(p.is_dir() for p in cfg.i18n_dir.iterdir()):
            problems.append("layout.mode=root 但 i18nDir 下发现子目录（不允许模块目录）")
    if cfg.layout.mode == "module":
        if not cfg.i18n_dir.exists() or not any(p.is_dir() for p in cfg.i18n_dir.iterdir()):
            problems.append("layout.mode=module 但 i18nDir 下未发现任何模块子目录")

    # 2) allow_nested_modules
    if cfg.i18n_dir.exists() and mode == "module" and not cfg.layout.rules.allow_nested_modules:
        for folder in list_modules(cfg):
            sub = cfg.i18n_dir / folder
            if any(p.is_dir() for p in sub.iterdir()):
                problems.append(f"模块目录不允许嵌套子目录：{sub}")

    # 3) 缺失检查（这里只做报告；是否创建交给 sync/translate）
    missing_dirs, missing_files = compute_missing(cfg)
    if missing_dirs or missing_files:
        # doctor 提示问题，但不创建
        problems.append("存在缺失的目录/文件（可运行 sync --yes 创建）")

    if problems:
        print("[doctor] 发现问题：")
        for p in problems:
            print(f"  - {p}")
        if missing_dirs:
            print("  - 缺失目录：")
            for d in missing_dirs:
                print(f"    * {d}")
        if missing_files:
            print("  - 缺失文件：")
            for f in missing_files:
                print(f"    * {f}")
        return 2

    print("[doctor] OK：未发现问题。")
    return 0


# -----------------------------
# Internal helpers
# -----------------------------

def _resolve_i18n_dir(project_root: Path, raw: str) -> Path:
    p = Path(str(raw))
    return p if p.is_absolute() else (project_root / p).resolve()


def _validate_config_dict(d: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    if "i18nDir" not in d:
        errs.append("缺少字段：i18nDir")
    if "source_locale" not in d or not (d.get("source_locale") or {}).get("code"):
        errs.append("缺少字段：source_locale.code")
    if "target_locales" not in d or not isinstance(d.get("target_locales"), list):
        errs.append("缺少字段：target_locales（list）")

    layout = d.get("layout") or {}
    mode = str(layout.get("mode", "auto"))
    if mode not in ("auto", "root", "module"):
        errs.append("layout.mode 必须为 auto/root/module")

    src = (d.get("source_locale") or {}).get("code")
    targets = [t.get("code") for t in (d.get("target_locales") or []) if isinstance(t, dict)]
    codes = [src] + targets if src else targets
    dup = {c for c in codes if c and codes.count(c) > 1}
    if dup:
        errs.append(f"locale code 重复：{sorted(dup)}")

    return errs


def _write_default_config(cfg_path: Path) -> None:
    template = {
        "openAIModel": "gpt-4o",
        "maxWorkers": 0,
        "source_locale": {"code": "en", "name_en": "English"},
        "target_locales": [{"code": "zh_Hant", "name_en": "Traditional Chinese (Taiwan)"}],
        "i18nDir": "i18n",
        "fileSuffix": "",
        "layout": {
            "mode": "auto",
            "root": {"pattern": "{code}{suffix}.json"},
            "module": {"pattern": "{folder}_{code}{suffix}.json"},
            "rules": {"allow_unmatched_files": False, "allow_nested_modules": False},
        },
        "options": {
            "sort_keys": True,
            "cleanup_extra_keys": True,
            "incremental_translate": True,
            "normalize_filenames": False,
            "post_sort_after_translate": True,
        },
        "prompts": {
            "default_en": "Translate UI strings naturally.\nPreserve placeholders.\nOutput only translation.\n",
            "by_locale_en": {"zh_Hant": "Use Traditional Chinese (Taiwan) UI style.\n"},
        },
        "placeholder": {
            "patterns": [
                r"\{\{\s*[A-Za-z0-9_]+\s*\}\}",
                r"\{[A-Za-z0-9_]+\}",
                r"%(\d+\$)?[@a-zA-Z]",
            ]
        },
    }
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(yaml.safe_dump(template, sort_keys=False, allow_unicode=True), encoding="utf-8")
