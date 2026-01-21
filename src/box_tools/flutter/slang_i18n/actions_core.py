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
    load_languages_json,
    build_cfg_from_languages_json,
    generate_commented_yaml_template,
    load_config_yaml,
    parse_config_dict,
    validate_config,
    diff_config_with_languages,
    update_cfg_targets_from_languages_json,
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

def run_init(
        *,
        root_dir: Path,
        rt: Optional[RuntimeOptions] = None,
) -> Report:
    """
    init：
    - 若 slang_i18n.yaml 不存在：读取 languages.json → 生成带注释的 yaml
    - 若已存在：校验 yaml，并与 languages.json 对比，提示是否升级 target_locales
    - 确保 i18nDir 目录存在（不在这里创建具体 json 文件；后续由 layout/json_ops 负责）
    """
    rt = rt or RuntimeOptions()
    issues: List[Issue] = []
    root_dir = root_dir.resolve()

    cfg_path = rt.config_path or default_config_path(root_dir)

    # 1) languages.json 是 init 的输入源（生成 / 对比都要用）
    try:
        languages = load_languages_json(root_dir=root_dir)
    except Exception as e:
        issues.append(Issue(
            IssueLevel.ERROR,
            IssueCode.CONFIG_INVALID,
            f"读取 languages.json 失败：{e}",
            path=(root_dir / "languages.json"),
        ))
        return Report(action="init", issues=tuple(issues))

    # 2) 配置不存在：生成
    if not cfg_path.exists():
        try:
            cfg, languages_path = build_cfg_from_languages_json(root_dir=root_dir)
            yml_text = generate_commented_yaml_template(cfg=cfg, languages_json_path=languages_path)

            if not rt.dry_run:
                cfg_path.write_text(yml_text, encoding="utf-8")

            # 确保 i18nDir 目录存在
            _ensure_dir(cfg.i18n_dir, dry_run=rt.dry_run)

            issues.append(Issue(
                IssueLevel.INFO,
                IssueCode.CONFIG_INVALID,  # 这里没有更合适的 code，可后续加 IssueCode.CONFIG_CREATED
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

    # 3) 配置已存在：校验 + 对比 + 可选升级
    try:
        raw = load_config_yaml(cfg_path)
        cfg = parse_config_dict(root_dir=root_dir, raw=raw)
    except ConfigError as e:
        issues.append(Issue(IssueLevel.ERROR, IssueCode.CONFIG_INVALID, f"配置解析失败：{e}", path=cfg_path))
        return Report(action="init", issues=tuple(issues))
    except Exception as e:
        issues.append(Issue(IssueLevel.ERROR, IssueCode.CONFIG_INVALID, f"配置解析异常：{e}", path=cfg_path))
        return Report(action="init", issues=tuple(issues))

    # 基础校验（结构）
    vr = validate_config(cfg)
    issues.extend(list(vr.issues))

    # 与 languages.json 对比差异
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
                # 重新生成带注释 YAML：注释保留，语言列表更新
                yml_text = generate_commented_yaml_template(cfg=new_cfg, languages_json_path=(root_dir / "languages.json"))
                if not rt.dry_run:
                    cfg_path.write_text(yml_text, encoding="utf-8")

                issues.append(Issue(
                    IssueLevel.INFO,
                    IssueCode.CONFIG_INVALID,
                    "已升级配置文件（target_locales 已与 languages.json 同步）。",
                    path=cfg_path,
                    details={
                        "source_locale": new_cfg.source_locale.code,
                        "target_locales": [t.code for t in new_cfg.target_locales],
                        "dry_run": rt.dry_run,
                    },
                ))
                cfg = new_cfg
            except Exception as e:
                issues.append(Issue(
                    IssueLevel.ERROR,
                    IssueCode.CONFIG_INVALID,
                    f"升级配置失败：{e}",
                    path=cfg_path,
                ))
                return Report(action="init", issues=tuple(issues))

    else:
        issues.append(Issue(
            IssueLevel.INFO,
            IssueCode.CONFIG_INVALID,
            "配置文件与 languages.json 的语言列表一致。",
            path=cfg_path,
        ))

    # 确保 i18nDir 存在
    try:
        _ensure_dir(cfg.i18n_dir, dry_run=rt.dry_run)
    except Exception as e:
        issues.append(Issue(IssueLevel.ERROR, IssueCode.I18N_DIR_MISSING, f"创建 i18nDir 失败：{e}", path=cfg.i18n_dir))
        return Report(action="init", issues=tuple(issues))

    return Report(
        action="init",
        issues=tuple(issues),
        files_changed=0,  # 只有生成/升级才会 +1；这里先保持保守，未来可更精确统计
    )


def run_doctor(
        *,
        root_dir: Path,
        rt: Optional[RuntimeOptions] = None,
) -> Report:
    """
    doctor（第一版：先做配置与语言源诊断）：
    - 检查 slang_i18n.yaml 可读可解析
    - 检查 languages.json 可读
    - 检查配置结构合法
    - 检查语言列表与 languages.json 一致性
    - 检查 i18nDir 是否存在（不存在给出提示）
    后续会在 layout/json_ops 完成后加入：
    - prefix 冲突
    - JSON flat 检查、@@locale 检查
    """
    rt = rt or RuntimeOptions()
    issues: List[Issue] = []
    root_dir = root_dir.resolve()
    cfg_path = rt.config_path or default_config_path(root_dir)

    # 1) 配置文件存在性
    if not cfg_path.exists():
        issues.append(Issue(
            IssueLevel.ERROR,
            IssueCode.CONFIG_MISSING,
            f"未找到配置文件：{cfg_path.name}。请先运行 init 生成。",
            path=cfg_path,
        ))
        return Report(action="doctor", issues=tuple(issues))

    # 2) 读取 languages.json（诊断一致性）
    try:
        languages = load_languages_json(root_dir=root_dir)
    except Exception as e:
        issues.append(Issue(
            IssueLevel.ERROR,
            IssueCode.CONFIG_INVALID,
            f"读取 languages.json 失败：{e}",
            path=(root_dir / "languages.json"),
        ))
        return Report(action="doctor", issues=tuple(issues))

    # 3) 解析配置
    try:
        raw = load_config_yaml(cfg_path)
        cfg = parse_config_dict(root_dir=root_dir, raw=raw)
    except Exception as e:
        issues.append(Issue(
            IssueLevel.ERROR,
            IssueCode.CONFIG_INVALID,
            f"配置解析失败：{e}",
            path=cfg_path,
        ))
        return Report(action="doctor", issues=tuple(issues))

    # 4) 基础校验
    vr = validate_config(cfg)
    issues.extend(list(vr.issues))

    # 5) i18nDir 存在性（doctor 更严格：不存在就 warn）
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
