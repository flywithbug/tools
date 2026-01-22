from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List

from .models import (
    Issue,
    IssueCode,
    IssueLevel,
    Report,
    RuntimeOptions,
)
from .config import (
    ConfigError,
    default_config_path,
    ensure_languages_json,
    load_languages_json,
    generate_commented_yaml_template,
    load_config_yaml,
    parse_config_dict,
)



# =========================
# Internal helpers
# =========================

def _ask_yes_no(prompt: str, default_no: bool = True) -> bool:
    """
    交互确认：默认 No（更安全）。
    y/yes -> True
    n/no/空 -> False（当 default_no=True）
    """
    suffix = " [y/N] " if default_no else " [Y/n] "
    s = input(prompt + suffix).strip().lower()
    if not s:
        return not default_no
    return s in ("y", "yes")


def _ensure_dir(path: Path, *, dry_run: bool) -> None:
    if path.exists():
        return
    if dry_run:
        return
    path.mkdir(parents=True, exist_ok=True)


# =========================
# Public actions (core)
# =========================

def run_init(*, root_dir: Path, rt: Optional[RuntimeOptions] = None) -> Report:
    """
    init：
    - 若 languages.json 不存在：生成默认 languages.json，并提示用户可增删改
    - 若 slang_i18n.yaml 不存在：基于 languages.json 生成带注释的配置，并写入默认 prompts（严格示例格式）
    - 若 slang_i18n.yaml 存在：与 languages.json 对比差异，提示是否升级（仅更新 target_locales）
    - 确保 i18nDir 存在
    """
    rt = rt or RuntimeOptions()
    issues: List[Issue] = []
    root_dir = root_dir.resolve()
    cfg_path = rt.config_path or default_config_path(root_dir)

    # 1) 确保 languages.json 存在
    languages_path = ensure_languages_json(root_dir=root_dir, dry_run=rt.dry_run)

    if not languages_path.exists() and rt.dry_run:
        issues.append(Issue(
            IssueLevel.ERROR,
            IssueCode.CONFIG_INVALID,
            "未找到 languages.json（dry-run 模式不会生成默认文件）。请手动创建或取消 --dry-run 再运行 init。",
            path=languages_path,
        ))
        return Report(action="init", issues=tuple(issues))

    if languages_path.exists():
        # 如果是刚生成的（我们无法100%判断），也提示一下用户可编辑
        issues.append(Issue(
            IssueLevel.INFO,
            IssueCode.CONFIG_INVALID,
            "languages.json 已就绪。你可以按项目需要增删改语言列表。",
            path=languages_path,
        ))

    # 2) 读取 languages.json
    try:
        languages = load_languages_json(root_dir=root_dir)
    except Exception as e:
        issues.append(Issue(IssueLevel.ERROR, IssueCode.CONFIG_INVALID, f"读取 languages.json 失败：{e}", path=languages_path))
        return Report(action="init", issues=tuple(issues))

    # 3) 配置不存在：生成
    if not cfg_path.exists():
        try:
            cfg, _ = build_cfg_from_languages_json(root_dir=root_dir)
            yml_text = generate_commented_yaml_template(cfg=cfg, languages_json_path=languages_path)

            if not rt.dry_run:
                cfg_path.write_text(yml_text, encoding="utf-8")

            _ensure_dir(cfg.i18n_dir, dry_run=rt.dry_run)

            issues.append(Issue(
                IssueLevel.INFO,
                IssueCode.CONFIG_INVALID,
                f"已生成配置文件：{cfg_path.name}",
                path=cfg_path,
                details={
                    "source_locale": cfg.source_locale.code,
                    "target_locales": [t.code for t in cfg.target_locales],
                    "i18nDir": str(cfg.i18n_dir),
                    "dry_run": rt.dry_run,
                },
            ))
            return Report(action="init", issues=tuple(issues), files_changed=(0 if rt.dry_run else 1))

        except Exception as e:
            issues.append(Issue(IssueLevel.ERROR, IssueCode.CONFIG_INVALID, f"生成配置失败：{e}", path=cfg_path))
            return Report(action="init", issues=tuple(issues))

    # 4) 配置已存在：校验 + 对比 + 可选升级
    try:
        raw = load_config_yaml(cfg_path)
        cfg = parse_config_dict(root_dir=root_dir, raw=raw)
    except ConfigError as e:
        issues.append(Issue(IssueLevel.ERROR, IssueCode.CONFIG_INVALID, f"配置解析失败：{e}", path=cfg_path))
        return Report(action="init", issues=tuple(issues))
    except Exception as e:
        issues.append(Issue(IssueLevel.ERROR, IssueCode.CONFIG_INVALID, f"配置解析异常：{e}", path=cfg_path))
        return Report(action="init", issues=tuple(issues))

    diff = diff_config_with_languages(cfg=cfg, languages=languages)
    if diff.added or diff.removed:
        issues.append(Issue(
            IssueLevel.WARN,
            IssueCode.CONFIG_INVALID,
            "检测到 languages.json 与配置文件的语言列表不一致。",
            path=cfg_path,
            details={
                "added_in_languages_json": list(diff.added),
                "removed_in_languages_json": list(diff.removed),
                "source_locale": diff.source_code,
            },
        ))

        do_upgrade = _ask_yes_no("是否根据 languages.json 升级配置（仅更新 target_locales，保留其它配置）？", default_no=True)
        if do_upgrade:
            try:
                new_cfg = update_cfg_targets_from_languages_json(cfg=cfg, languages=languages)
                yml_text = generate_commented_yaml_template(cfg=new_cfg, languages_json_path=languages_path)
                if not rt.dry_run:
                    cfg_path.write_text(yml_text, encoding="utf-8")
                cfg = new_cfg

                issues.append(Issue(
                    IssueLevel.INFO,
                    IssueCode.CONFIG_INVALID,
                    "已升级配置文件（target_locales 已与 languages.json 同步）。",
                    path=cfg_path,
                    details={
                        "source_locale": cfg.source_locale.code,
                        "target_locales": [t.code for t in cfg.target_locales],
                        "dry_run": rt.dry_run,
                    },
                ))
            except Exception as e:
                issues.append(Issue(IssueLevel.ERROR, IssueCode.CONFIG_INVALID, f"升级配置失败：{e}", path=cfg_path))
                return Report(action="init", issues=tuple(issues))
    else:
        issues.append(Issue(
            IssueLevel.INFO,
            IssueCode.CONFIG_INVALID,
            "配置文件与 languages.json 的语言列表一致。",
            path=cfg_path,
        ))

    # 5) 确保 i18nDir 存在
    try:
        _ensure_dir(cfg.i18n_dir, dry_run=rt.dry_run)
    except Exception as e:
        issues.append(Issue(IssueLevel.ERROR, IssueCode.I18N_DIR_MISSING, f"创建 i18nDir 失败：{e}", path=cfg.i18n_dir))
        return Report(action="init", issues=tuple(issues))

    return Report(action="init", issues=tuple(issues))



def run_doctor(*, root_dir: Path, rt: Optional[RuntimeOptions] = None) -> Report:
    """
    doctor（当前阶段只做配置 & languages.json 诊断）：
    - slang_i18n.yaml 是否存在、能否解析
    - languages.json 是否存在、能否解析
    - 语言列表差异提示（建议运行 init 并选择升级）
    - i18nDir 是否存在（不存在提示）
    """
    rt = rt or RuntimeOptions()
    issues: List[Issue] = []
    root_dir = root_dir.resolve()
    cfg_path = rt.config_path or default_config_path(root_dir)

    if not cfg_path.exists():
        issues.append(Issue(
            IssueLevel.ERROR,
            IssueCode.CONFIG_MISSING,
            f"未找到配置文件：{cfg_path.name}。请先运行 init 生成。",
            path=cfg_path,
        ))
        return Report(action="doctor", issues=tuple(issues))

    # languages.json：doctor 不自动生成，只提示（避免静默写文件）
    languages_path = root_dir / "languages.json"
    if not languages_path.exists():
        issues.append(Issue(
            IssueLevel.ERROR,
            IssueCode.CONFIG_INVALID,
            "未找到 languages.json。请运行 init 自动生成默认文件，或手动创建。",
            path=languages_path,
        ))
        return Report(action="doctor", issues=tuple(issues))

    try:
        languages = load_languages_json(root_dir=root_dir)
    except Exception as e:
        issues.append(Issue(IssueLevel.ERROR, IssueCode.CONFIG_INVALID, f"读取 languages.json 失败：{e}", path=languages_path))
        return Report(action="doctor", issues=tuple(issues))

    try:
        raw = load_config_yaml(cfg_path)
        cfg = parse_config_dict(root_dir=root_dir, raw=raw)
    except Exception as e:
        issues.append(Issue(IssueLevel.ERROR, IssueCode.CONFIG_INVALID, f"配置解析失败：{e}", path=cfg_path))
        return Report(action="doctor", issues=tuple(issues))

    if not cfg.i18n_dir.exists():
        issues.append(Issue(
            IssueLevel.WARN,
            IssueCode.I18N_DIR_MISSING,
            f"i18nDir 不存在：{cfg.i18n_dir}（可运行 init 自动创建目录）",
            path=cfg.i18n_dir,
        ))
    else:
        issues.append(Issue(
            IssueLevel.INFO,
            IssueCode.I18N_DIR_MISSING,
            f"i18nDir 存在：{cfg.i18n_dir}",
            path=cfg.i18n_dir,
        ))

    # 6) 与 languages.json 对比差异
    diff = diff_config_with_languages(cfg=cfg, languages=languages)
    if diff.added or diff.removed:
        issues.append(Issue(
            IssueLevel.WARN,
            IssueCode.CONFIG_INVALID,
            "languages.json 与配置的语言列表不一致：建议运行 init 并选择升级。",
            path=cfg_path,
            details={
                "added_in_languages_json": list(diff.added),
                "removed_in_languages_json": list(diff.removed),
                "source_locale": diff.source_code,
            },
        ))
    else:
        issues.append(Issue(
            IssueLevel.INFO,
            IssueCode.CONFIG_INVALID,
            "languages.json 与配置的语言列表一致。",
            path=cfg_path,
        ))

    return Report(action="doctor", issues=tuple(issues), files_scanned=0)


# =========================
# Placeholders for next steps
# (need layout.py + json_ops.py)
# =========================

def run_sort(*, root_dir: Path, rt: Optional[RuntimeOptions] = None) -> Report:
    raise NotImplementedError("run_sort 依赖 json_ops/layout，下一步实现")


def run_check(*, root_dir: Path, rt: Optional[RuntimeOptions] = None) -> Report:
    raise NotImplementedError("run_check 依赖 json_ops/layout，下一步实现")


def run_clean(*, root_dir: Path, rt: Optional[RuntimeOptions] = None) -> Report:
    raise NotImplementedError("run_clean 依赖 json_ops/layout，下一步实现")
