# data.py
from __future__ import annotations

import json
import re
import datetime
import textwrap
import pprint
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

import yaml

# ----------------------------
# Swift L10n.swift 生成
# ----------------------------


def _swift_escape(s: str) -> str:
    """将文本转义为 Swift 字符串字面量可用的形式。"""
    if s is None:
        return ""
    # 顺序很重要：先转义反斜杠
    s = s.replace("\\", "\\\\")
    s = s.replace('"', "\\\"")
    s = s.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return s


_COMMENT_STRIP_RE = re.compile(r"^\s*(?://+|/\*+|\*+|\*/+)\s*|\s*(?:\*/)?\s*$")


def _comment_to_doc_line(comments: List[str]) -> Optional[str]:
    """把 .strings 上方的注释块提炼成一行 /// 文档注释（尽量贴近原文件）。"""
    if not comments:
        return None
    # 找“最后一行有内容”的注释（通常最贴近 key 的语义）
    for raw in reversed(comments):
        s = raw.strip()
        if not s:
            continue
        if not _is_comment_line(s):
            # 非标准行也允许（parse_strings_file 已经把它归到 comments 里）
            pass
        # 去掉 // /* * */ 等标记
        s = _COMMENT_STRIP_RE.sub("", s).strip()
        if s:
            return s
    return None


def _to_pascal_case(s: str) -> str:
    # 仅做最小规则：按 '_' 分词，首字母大写，其余原样保留（兼容 historyLocations 这种 camel）
    parts = [p for p in re.split(r"[_\s]+", s) if p]
    if not parts:
        return "X"
    return "".join(p[:1].upper() + p[1:] for p in parts)


def _to_camel_case_from_key_remainder(rem: str) -> str:
    """把 group 之后的 key remainder（可能含 '.'/'_'）转成 lowerCamelCase 属性名。"""
    # 以 '.' 分段；每段再以 '_' 分词
    segs: List[str] = []
    for seg in rem.split("."):
        seg = seg.strip()
        if not seg:
            continue
        segs.extend([w for w in seg.split("_") if w])

    if not segs:
        return "value"

    out = segs[0]
    for w in segs[1:]:
        out += w[:1].upper() + w[1:]
    # Swift 标识符不能以数字开头；极端情况兜底
    if out and out[0].isdigit():
        out = "_" + out
    return out


def _swift_prop_name_for_key(key: str) -> Tuple[str, str]:
    """
    ✅ 与 generate_l10n_swift 完全一致的属性名推导逻辑，但返回 (group_prefix, prop)
    - grp = _group_prefix(key)  -> Swift enum 名来源
    - rem = 去掉 grp + '.' 或 grp + '_'（如果存在）
    - prop = _to_camel_case_from_key_remainder(rem) -> enum 内 static var 名
    """
    grp = _group_prefix(key)
    rem = key
    if "." in key and key.startswith(grp + "."):
        rem = key[len(grp) + 1 :]
    elif "_" in key and key.startswith(grp + "_"):
        rem = key[len(grp) + 1 :]
    prop = _to_camel_case_from_key_remainder(rem)
    return grp, prop



def scan_camelcase_conflicts(entries: List["StringsEntry"]) -> Dict[str, List[str]]:
    """
    扫描“驼峰化后同名，但原 key 不同”的冲突（Swift L10n 生成会撞属性名）。
    返回：{prop_name: [key1, key2, ...]}（仅保留冲突项，且 key 列表排序去重）
    """
    bucket: Dict[str, List[str]] = {}
    for e in entries:
        prop = _swift_prop_name_for_key(e.key)
        bucket.setdefault(prop, []).append(e.key)

    out: Dict[str, List[str]] = {}
    for prop, keys in bucket.items():
        uniq = sorted(set(keys))
        if len(uniq) >= 2:
            out[prop] = uniq
    return out


def _format_camel_conflicts(conflicts: Dict[str, List[str]], *, header: str) -> str:
    lines: List[str] = []
    lines.append(header)
    for prop in sorted(conflicts.keys()):
        keys = conflicts[prop]
        lines.append(f"- {prop}: {keys}")
    return "\n".join(lines)


def generate_l10n_swift(
    cfg: "StringsI18nConfig",
    *,
    strings_filename: str = "Localizable.strings",
    out_path: Optional[Path] = None,
) -> Path:
    """从 Base.lproj/<strings_filename> 生成 L10n.swift（按 key 前缀分组）。"""
    base_dir = (cfg.lang_root / cfg.base_folder).resolve()
    src_fp = (base_dir / strings_filename).resolve()
    if not src_fp.exists():
        raise FileNotFoundError(f"未找到 Base strings 文件：{src_fp}")

    # ✅ 产物约定：L10n.swift 放在 lang_root 下面
    # - out_path 为空：默认 <lang_root>/L10n.swift
    # - out_path 为相对路径：相对 <lang_root>
    # - out_path 为绝对路径：按绝对路径写
    if out_path is None:
        out_path = (cfg.lang_root / "L10n.swift")
    elif not out_path.is_absolute():
        out_path = (cfg.lang_root / out_path)
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    _preamble, entries = parse_strings_file(src_fp)
    # 稳定排序：先分组再按 key
    entries = sorted(entries, key=lambda e: (_group_prefix(e.key), e.key))

    # ✅ 生成前的防爆：Base 内 camelCase 冲突直接报错（否则 Swift 编译炸）
    camel_conflicts = scan_camelcase_conflicts(entries)
    if camel_conflicts:
        msg = _format_camel_conflicts(
            camel_conflicts,
            header=f"Base/{strings_filename} 存在 Swift camelCase 属性名冲突（请先手动改 key）：",
        )
        raise ValueError(msg)

    # 按 group_prefix 聚合
    groups: Dict[str, List[StringsEntry]] = {}
    for e in entries:
        groups.setdefault(_group_prefix(e.key), []).append(e)

    lines: List[str] = []
    lines.append(f"// Auto-generated from {cfg.base_folder}/{strings_filename}")
    lines.append("import Foundation")
    lines.append("")
    lines.append("extension String {")
    lines.append("    func callAsFunction(_ arguments: CVarArg...) -> String {")
    lines.append("        String(format: self, locale: Locale.current, arguments: arguments)")
    lines.append("    }")
    lines.append("}")
    lines.append("")
    lines.append("enum L10n {")

    for grp in sorted(groups.keys(), key=lambda x: x.lower()):
        enum_name = _to_pascal_case(grp)
        lines.append(f"    enum {enum_name} {{")

        for e in groups[grp]:
            # remainder：去掉 group_prefix + 分隔符
            rem = e.key
            if "." in e.key and e.key.startswith(grp + "."):
                rem = e.key[len(grp) + 1 :]
            elif "_" in e.key and e.key.startswith(grp + "_"):
                rem = e.key[len(grp) + 1 :]

            prop = _to_camel_case_from_key_remainder(rem)
            doc = _comment_to_doc_line(e.comments)
            if doc:
                lines.append(f"        /// {doc}")

            key_esc = _swift_escape(e.key)
            val_esc = _swift_escape(e.value)
            cmt_esc = _swift_escape(e.value)  # 与现有样例一致：comment 使用同文案

            lines.append(
                f"        static var {prop}: String {{ return NSLocalizedString(\"{key_esc}\", value: \"{val_esc}\", comment: \"{cmt_esc}\") }}"
            )
            lines.append("")

        # 去掉 enum 内末尾多余空行
        while lines and lines[-1] == "":
            lines.pop()
        lines.append("    }")
        lines.append("")

    # 去掉文件末尾多余空行
    while lines and lines[-1] == "":
        lines.pop()
    lines.append("}")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ----------------------------
# 常量 / 默认文件名
# ----------------------------
DEFAULT_TEMPLATE_NAME = "strings_i18n.yaml"     # 内置模板文件（带注释）
DEFAULT_LANGUAGES_NAME = "languages.json"      # 本地语言列表文件（code + name_en）


# ----------------------------
# 异常类型
# ----------------------------
class ConfigError(RuntimeError):
    """用于启动阶段的配置错误（更友好的报错与解决建议）"""
    pass


# ----------------------------
# 数据模型（按 strings_i18n.yaml schema）
# ----------------------------
@dataclass(frozen=True)
class Locale:
    code: str
    name_en: str


@dataclass(frozen=True)
class StringsI18nConfig:
    # 路径
    project_root: Path
    languages_path: Path          # 绝对路径
    lang_root: Path               # 绝对路径：*.lproj 所在目录
    base_folder: str              # e.g. Base.lproj

    # 语言
    base_locale: Locale
    source_locale: Locale
    core_locales: List[Locale]
    target_locales: List[Locale]

    # 行为开关
    options: Dict[str, Any]
    prompts: Dict[str, Any]


# ----------------------------
# 内置文件读取（模板 / 默认 languages.json）
# ----------------------------
def _pkg_file(name: str) -> Path:
    # 默认把模板与默认 languages.json 放在 data.py 同目录
    return Path(__file__).with_name(name)


def ensure_languages_json(project_root: Path, languages_rel: str = DEFAULT_LANGUAGES_NAME) -> Path:
    """如果本地没有 languages.json，则用内置默认 languages.json 生成一份。"""
    project_root = project_root.resolve()
    dst = (project_root / languages_rel).resolve()

    if dst.exists():
        return dst

    src = _pkg_file(DEFAULT_LANGUAGES_NAME)
    if not src.exists():
        raise FileNotFoundError(f"内置默认 {DEFAULT_LANGUAGES_NAME} 不存在：{src}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def _load_languages(languages_path: Path) -> List[Dict[str, str]]:
    arr = json.loads(languages_path.read_text(encoding="utf-8"))
    if not isinstance(arr, list):
        raise ValueError(f"{languages_path.name} 顶层必须是数组")
    out: List[Dict[str, str]] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip()
        name_en = str(item.get("name_en", "")).strip()
        if not code or not name_en:
            continue
        out.append({"code": code, "name_en": name_en})
    return out


def _all_locale_codes(cfg: StringsI18nConfig) -> List[str]:
    codes: List[str] = []
    for loc in [cfg.base_locale, cfg.source_locale] + cfg.core_locales + cfg.target_locales:
        if loc and loc.code not in codes:
            codes.append(loc.code)
    return codes


def _dedup_locales_preserve_order(locales: List[Locale]) -> List[Locale]:
    seen: set[str] = set()
    out: List[Locale] = []
    for l in locales:
        if l.code in seen:
            continue
        seen.add(l.code)
        out.append(l)
    return out


_PRINTF_RE = re.compile(r'%(?:\d+\$)?(?:@|d|i|u|f|s|ld|lld|lu|llu|lf)', re.IGNORECASE)

def _extract_printf_placeholders(value: str) -> List[str]:
    # 忽略转义的 %%（它不是占位符）
    if not value:
        return []
    # 临时替换 %% 防止被正则误伤
    tmp = value.replace("%%", "")
    return _PRINTF_RE.findall(tmp)


def _doctor_print_and_write(
    cfg: StringsI18nConfig,
    errors: List[str],
    warns: List[str],
    extra_sections: Optional[Dict[str, Any]] = None,
) -> int:
    # 控制台摘要
    print("\n=== doctor summary ===")
    print(f"- project_root: {cfg.project_root}")
    print(f"- lang_root:    {cfg.lang_root}")
    print(f"- base_folder:  {cfg.base_folder}")
    print(f"- base_locale:  {cfg.base_locale.code}")
    print(f"- source_locale:{cfg.source_locale.code}")
    print(f"- core_locales: {[l.code for l in cfg.core_locales]}")
    print(f"- target_locales: {len(cfg.target_locales)}")

    if errors:
        print("\n[ERROR]")
        for e in errors:
            print(f"- {e}")
    if warns:
        print("\n[WARN]")
        for w in warns:
            print(f"- {w}")

    # 写报告文件（含详细 section）
    try:
        lines: List[str] = []
        lines.append("box_strings_i18n doctor report")
        lines.append("")
        lines.append("=== summary ===")
        lines.append(f"project_root: {cfg.project_root}")
        lines.append(f"lang_root:    {cfg.lang_root}")
        lines.append(f"base_folder:  {cfg.base_folder}")
        lines.append(f"base_locale:  {cfg.base_locale.code}")
        lines.append(f"source_locale:{cfg.source_locale.code}")
        lines.append(f"core_locales: {[l.code for l in cfg.core_locales]}")
        lines.append(f"target_locales: {len(cfg.target_locales)}")

        if errors:
            lines.append("")
            lines.append("[ERROR]")
            for e in errors:
                lines.append(f"- {e}")
        if warns:
            lines.append("")
            lines.append("[WARN]")
            for w in warns:
                lines.append(f"- {w}")

        if extra_sections:
            lines.append("")
            lines.append("=== details ===")
            for k, v in (extra_sections or {}).items():
                lines.append("")
                lines.append(f"## {k}")
                if isinstance(v, str):
                    lines.append(v.rstrip())
                else:
                    lines.append(pprint.pformat(v, width=120))

        content = "\n".join(lines).rstrip() + "\n"
        report_path = _write_report_file(cfg, content, name="doctor")
        if report_path is not None:
            print(f"\nReport: {report_path}")
    except Exception as e:
        print(f"\nReport 写入失败：{e}")

    return 1 if errors else 0


def build_target_locales_from_languages_json(
    languages_path: Path,
    *,
    source_code: str,
    core_codes: List[str],
) -> Tuple[List[Dict[str, str]], int]:
    """
    从 languages.json 生成 target_locales（code + name_en），并：
    - 按 code 去重（保序）
    - 剔除 source_code
    - 剔除 core_codes
    返回：(targets, removed_count)
    """
    items = _load_languages(languages_path)
    seen = set()
    out: List[Dict[str, str]] = []
    removed = 0

    core_set = set(core_codes)

    for it in items:
        code = it["code"]
        if code == source_code or code in core_set:
            removed += 1
            continue
        if code in seen:
            continue
        seen.add(code)
        out.append(it)

    return out, removed


# ----------------------------
# YAML 模板“保注释”局部替换：target_locales block
# ----------------------------
def _yaml_block_for_target_locales(locales: List[Dict[str, str]]) -> str:
    lines = ["target_locales:"]
    for it in locales:
        lines.append(f"  - code: {it['code']}")
        lines.append(f"    name_en: {it['name_en']}")
    return "\n".join(lines) + "\n"


def replace_target_locales_block(template_text: str, new_locales: List[Dict[str, str]]) -> str:
    """
    仅替换模板中 `target_locales:` 段落的内容，其他注释/排版保留。
    匹配规则：从 `target_locales:` 开始，替换到下一个顶层 key 之前。
    """
    new_block = _yaml_block_for_target_locales(new_locales)

    start_match = re.search(r"(?m)^target_locales:\s*$", template_text)
    if not start_match:
        raise ValueError("模板中未找到 target_locales: 段落")

    start = start_match.start()
    after = template_text[start_match.end():]

    # 下一段顶层 key（形如 prompts:, options:, languages: 等）
    next_key = re.search(r"(?m)^(?!target_locales:)[A-Za-z_][A-Za-z0-9_]*:\s*$", after)

    if next_key:
        end = start_match.end() + next_key.start()
    else:
        end = len(template_text)

    return template_text[:start] + new_block + template_text[end:]


# ----------------------------
# init：生成/校验配置，确保 languages.json + lang_root 存在
# ----------------------------
def init_config(project_root: Path, cfg_path: Path) -> None:
    project_root = project_root.resolve()
    cfg_path = cfg_path.resolve()

    # 1) cfg 不存在：用内置模板生成（保留注释）+ 动态替换 target_locales
    if not cfg_path.exists():
        tpl = _pkg_file(DEFAULT_TEMPLATE_NAME)
        if not tpl.exists():
            raise FileNotFoundError(f"内置默认配置模板不存在：{tpl}")

        tpl_text = tpl.read_text(encoding="utf-8")
        raw_tpl = yaml.safe_load(tpl_text) or {}
        validate_config(raw_tpl)  # 模板自身也要合法

        # 2) 先确保 languages.json 存在（按模板里的 languages 字段）
        languages_rel = str(raw_tpl.get("languages") or DEFAULT_LANGUAGES_NAME)
        languages_path = ensure_languages_json(project_root, languages_rel=languages_rel)

        # 3) 生成 targets：languages - core - source
        src = _first_locale(raw_tpl["source_locale"])
        core = [_locale_obj(x) for x in (raw_tpl.get("core_locales") or [])]
        targets, _removed = build_target_locales_from_languages_json(
            languages_path,
            source_code=src.code,
            core_codes=[c.code for c in core],
        )

        out_text = replace_target_locales_block(tpl_text, targets)
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(out_text, encoding="utf-8")

    # 4) 校验配置（init 阶段不强制检查目录存在）
    assert_config_ok(cfg_path, project_root=project_root, check_paths_exist=False)

    # 5) 创建 lang_root 目录（按 project_root 解析）
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    lang_root = (project_root / str(raw["lang_root"])).resolve()
    lang_root.mkdir(parents=True, exist_ok=True)

    # 6) 确保 languages 文件存在（按配置）
    languages_rel = str(raw.get("languages") or DEFAULT_LANGUAGES_NAME)
    ensure_languages_json(project_root, languages_rel=languages_rel)


# ----------------------------
# 启动优先校验入口
# ----------------------------
def assert_config_ok(
    cfg_path: Path,
    *,
    project_root: Optional[Path] = None,
    check_paths_exist: bool = True,
) -> Dict[str, Any]:
    cfg_path = cfg_path.resolve()
    project_root = (project_root or cfg_path.parent).resolve()

    if not cfg_path.exists():
        raise ConfigError(
            f"配置文件不存在：{cfg_path}\n"
            f"解决方法：运行 `box_strings_i18n init` 生成默认配置。"
        )

    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise ConfigError(
            f"配置文件无法解析为 YAML：{cfg_path}\n"
            f"原因：{e}\n"
            f"解决方法：修复 YAML 格式或运行 `box_strings_i18n init` 重新生成。"
        )

    try:
        validate_config(raw)
    except Exception as e:
        raise ConfigError(
            f"配置文件校验失败：{cfg_path}\n"
            f"原因：{e}\n"
            f"解决方法：修复配置字段/类型，或运行 `box_strings_i18n init` 重新生成。"
        )

    if check_paths_exist:
        # languages
        languages_path = (project_root / str(raw["languages"])).resolve()
        if not languages_path.exists():
            raise ConfigError(
                f"languages 文件不存在：{languages_path}\n"
                f"解决方法：运行 `box_strings_i18n init` 自动生成，或修复配置中的 languages 路径。"
            )

        # lang_root + base_folder
        lang_root = (project_root / str(raw["lang_root"])).resolve()
        if not lang_root.exists():
            raise ConfigError(
                f"lang_root 目录不存在：{lang_root}\n"
                f"解决方法：创建目录或运行 `box_strings_i18n init` 让工具初始化。"
            )

        base_folder = str(raw["base_folder"])
        base_dir = (lang_root / base_folder).resolve()
        if not base_dir.exists():
            raise ConfigError(
                f"Base 语言目录不存在：{base_dir}\n"
                f"解决方法：确认 Xcode 工程内 Base.lproj 路径，或修复配置中的 lang_root/base_folder。"
            )

    return raw


# ----------------------------
# load_config：把 raw dict 转成 StringsI18nConfig（路径解析为绝对路径）
# ----------------------------
def load_config(cfg_path: Path, *, project_root: Optional[Path] = None) -> StringsI18nConfig:
    cfg_path = cfg_path.resolve()
    project_root = (project_root or cfg_path.parent).resolve()

    raw = assert_config_ok(cfg_path, project_root=project_root, check_paths_exist=True)

    languages_path = (project_root / str(raw["languages"])).resolve()
    lang_root = (project_root / str(raw["lang_root"])).resolve()

    base_locale = _first_locale(raw["base_locale"])
    source_locale = _first_locale(raw["source_locale"])
    core_locales = [_locale_obj(x) for x in (raw.get("core_locales") or [])]
    target_locales = [_locale_obj(x) for x in (raw.get("target_locales") or [])]

    return StringsI18nConfig(
        project_root=project_root,
        languages_path=languages_path,
        lang_root=lang_root,
        base_folder=str(raw["base_folder"]),
        base_locale=base_locale,
        source_locale=source_locale,
        core_locales=core_locales,
        target_locales=target_locales,
        options=dict(raw.get("options") or {}),
        prompts=dict(raw.get("prompts") or {}),
    )


# ----------------------------
# validate_config：字段 + 类型 + 关键语义校验
# ----------------------------
def validate_config(raw: Dict[str, Any]) -> None:
    required_top = [
        "options", "languages", "lang_root", "base_folder",
        "base_locale", "source_locale", "core_locales",
        "target_locales", "prompts",
    ]
    for k in required_top:
        if k not in raw:
            raise ValueError(f"配置缺少字段：{k}")

    # options
    options = raw["options"]
    if not isinstance(options, dict):
        raise ValueError("options 必须是 object")

    for k in ["cleanup_extra_keys", "incremental_translate", "normalize_filenames", "sort_keys"]:
        if k not in options:
            raise ValueError(f"options 缺少字段：{k}")

    # paths
    for k in ["languages", "lang_root", "base_folder"]:
        if not isinstance(raw[k], str) or not str(raw[k]).strip():
            raise ValueError(f"{k} 必须是非空字符串")

    # locales (这些在模板里是 list[object]，每个只放一个)
    _ = _first_locale(raw["base_locale"])
    src = _first_locale(raw["source_locale"])

    core = raw["core_locales"]
    if not isinstance(core, list) or len(core) == 0:
        raise ValueError("core_locales 必须是非空数组")
    core_locales = [_locale_obj(x) for x in core]

    targets = raw["target_locales"]
    if not isinstance(targets, list):
        raise ValueError("target_locales 必须是数组（允许为空，但建议由 init 生成）")
    target_locales = [_locale_obj(x) for x in targets]

    # 语义：去重与冲突
    def codes(locales: List[Locale]) -> List[str]:
        return [x.code for x in locales]

    core_codes = codes(core_locales)
    if len(set(core_codes)) != len(core_codes):
        raise ValueError("core_locales.code 存在重复，请去重")

    tgt_codes = codes(target_locales)
    if len(set(tgt_codes)) != len(tgt_codes):
        raise ValueError("target_locales.code 存在重复，请去重")

    if src.code in set(tgt_codes):
        raise ValueError("target_locales 里包含 source_locale.code，请移除（source 不能作为 target）")

    # prompts
    prompts = raw["prompts"]
    if not isinstance(prompts, dict):
        raise ValueError("prompts 必须是 object")
    if "default_en" not in prompts or not isinstance(prompts["default_en"], str):
        raise ValueError("prompts.default_en 必须存在且为字符串")


def _locale_obj(obj: Any) -> Locale:
    if not isinstance(obj, dict):
        raise ValueError("locale item 必须是 object")
    code = str(obj.get("code", "")).strip()
    name_en = str(obj.get("name_en", "")).strip()
    if not code or not name_en:
        raise ValueError("locale.code/name_en 不能为空")
    return Locale(code=code, name_en=name_en)


def _first_locale(obj: Any) -> Locale:
    if not isinstance(obj, list) or len(obj) == 0:
        raise ValueError("locale 必须是非空数组（list），且第一项为 object")
    return _locale_obj(obj[0])


# ----------------------------
# commands：doctor/sort（骨架）
# ----------------------------
def run_doctor(cfg: StringsI18nConfig) -> int:
    """
    最佳实践的 doctor：
    - 配置 & 目录结构校验
    - Base.lproj/其它语言 *.strings 可解析性检查
    - key 一致性（缺失/冗余）统计
    - 重复 key 检测（Base 视为错误；其它语言视为警告）
    - Swift camelCase 冲突检测（Base 视为错误：会导致 L10n.swift 属性名冲突）
    - printf 占位符一致性（%@/%d/%1$@ ...）检查（警告）
    """
    errors: List[str] = []
    warns: List[str] = []

    # ---- 路径/结构 ----
    if not cfg.lang_root.exists():
        errors.append(f"lang_root 不存在：{cfg.lang_root}")
        return _doctor_print_and_write(cfg, errors, warns)

    base_dir = (cfg.lang_root / cfg.base_folder).resolve()
    if not base_dir.exists():
        errors.append(f"Base 目录不存在：{base_dir}")
        return _doctor_print_and_write(cfg, errors, warns)

    if not cfg.languages_path.exists():
        errors.append(f"languages.json 不存在：{cfg.languages_path}（可先执行 init，会自动生成模板/拷贝默认 languages.json）")
        return _doctor_print_and_write(cfg, errors, warns)

    # ---- languages.json 内容 ----
    try:
        languages_list = _load_languages(cfg.languages_path)
        languages = {d['code'] for d in languages_list if 'code' in d}
    except Exception as e:
        errors.append(f"languages.json 读取失败：{cfg.languages_path}（{e}）")
        return _doctor_print_and_write(cfg, errors, warns)

    cfg_codes = _all_locale_codes(cfg)
    missing_in_languages = [c for c in cfg_codes if c not in languages]
    if missing_in_languages:
        warns.append(
            "languages.json 缺少以下 code（建议补全，以便 init/校验一致）："
            + ", ".join(missing_in_languages)
        )

    # ---- Base.lproj 文件集 ----
    base_files = sorted([p for p in base_dir.glob("*.strings") if p.is_file()])
    if not base_files:
        errors.append(f"Base 目录下未发现任何 *.strings：{base_dir}")
        return _doctor_print_and_write(cfg, errors, warns)

    # 解析 Base 并建立“金标准 key 集合”
    base_map: Dict[str, List[StringsEntry]] = {}
    base_keys_by_file: Dict[str, set] = {}

    # ✅ 新增：Swift camelCase 冲突（按文件）
    base_camel_conflicts_by_file: Dict[str, Dict[str, List[str]]] = {}

    for fp in base_files:
        try:
            preamble, entries = parse_strings_file(fp)
        except Exception as e:
            errors.append(f"Base 解析失败：{fp.name}（{e}）")
            continue

        dups = _collect_duplicates(entries)
        if dups:
            errors.append(f"Base 存在重复 key：{fp.name} -> {dups}")

        # ✅ camelCase 冲突：Base 视为 ERROR（gen L10n.swift 会撞属性名）
        camel_conflicts = scan_camelcase_conflicts(entries)
        if camel_conflicts:
            base_camel_conflicts_by_file[fp.name] = camel_conflicts
            errors.append(
                "Base 存在 Swift camelCase 属性名冲突（会导致 L10n.swift 编译/生成失败）："
                f"{fp.name} -> { {k: v for k, v in list(camel_conflicts.items())[:10]} }"
                + (" …" if len(camel_conflicts) > 10 else "")
            )

        base_map[fp.name] = entries
        base_keys_by_file[fp.name] = {e.key for e in entries}

    # ---- 其它语言检查 ----
    other_locales = [cfg.source_locale] + cfg.core_locales + cfg.target_locales
    other_locales = _dedup_locales_preserve_order(other_locales)

    missing_dirs: List[str] = []
    missing_files: List[str] = []
    parse_fail: List[str] = []

    missing_keys: Dict[str, Dict[str, List[str]]] = {}
    redundant_keys: Dict[str, Dict[str, List[str]]] = {}

    placeholder_mismatch: Dict[str, Dict[str, List[Tuple[str, List[str], List[str]]]]] = {}

    for loc in other_locales:
        loc_dir = (cfg.lang_root / f"{loc.code}.lproj").resolve()
        if not loc_dir.exists():
            missing_dirs.append(loc.code)
            continue

        for bf in base_files:
            target_fp = (loc_dir / bf.name)
            if not target_fp.exists():
                missing_files.append(f"{loc.code}/{bf.name}")
                continue

            try:
                _, loc_entries = parse_strings_file(target_fp)
            except Exception as e:
                parse_fail.append(f"{loc.code}/{bf.name}（{e}）")
                continue

            dups = _collect_duplicates(loc_entries)
            if dups:
                warns.append(f"重复 key（{loc.code}/{bf.name}）：{dups}")

            base_keys = base_keys_by_file.get(bf.name, set())
            loc_keys = {e.key for e in loc_entries}

            mk = sorted(list(base_keys - loc_keys))
            rk = sorted(list(loc_keys - base_keys))

            if mk:
                missing_keys.setdefault(loc.code, {}).setdefault(bf.name, []).extend(mk)
            if rk:
                redundant_keys.setdefault(loc.code, {}).setdefault(bf.name, []).extend(rk)

            base_entries_by_key = {e.key: e for e in base_map.get(bf.name, [])}
            loc_entries_by_key = {e.key: e for e in loc_entries}
            for k in (base_keys & loc_keys):
                b = base_entries_by_key.get(k)
                t = loc_entries_by_key.get(k)
                if not b or not t:
                    continue
                bph = _extract_printf_placeholders(b.value)
                tph = _extract_printf_placeholders(t.value)
                if bph != tph:
                    placeholder_mismatch.setdefault(loc.code, {}).setdefault(bf.name, []).append((k, bph, tph))

    if missing_dirs:
        warns.append("缺少语言目录（可通过 sort 自动补齐空文件夹/文件）："
                     + ", ".join(sorted(set(missing_dirs))))
    if missing_files:
        warns.append("缺少 *.strings 文件（可通过 sort 自动创建空文件）："
                     + ", ".join(missing_files[:30]) + (" …" if len(missing_files) > 30 else ""))

    if parse_fail:
        errors.append("以下文件解析失败（请先修复语法/引号/分号等）："
                      + "; ".join(parse_fail[:20]) + (" …" if len(parse_fail) > 20 else ""))

    miss_count = sum(len(keys) for m in missing_keys.values() for keys in m.values())
    red_count = sum(len(keys) for m in redundant_keys.values() for keys in m.values())
    ph_count = sum(len(v) for m in placeholder_mismatch.values() for v in m.values())

    if miss_count:
        warns.append(f"发现缺失 key（相对 Base）：共 {miss_count} 个（建议走 translate 增量或补齐）")
    if red_count:
        warns.append(f"发现冗余 key（Base 不存在）：共 {red_count} 个（建议在 sort 中选择删除）")
    if ph_count:
        warns.append(f"发现占位符不一致：共 {ph_count} 项（建议人工确认，避免运行时崩溃/格式错乱）")

    if ph_count:
        policy = _resolve_placeholder_mismatch_policy(cfg, placeholder_mismatch, max_items=3)
        if policy == "delete":
            n = _apply_placeholder_mismatch_delete(cfg, placeholder_mismatch)
            warns.append(f"已删除占位符不一致条目：{n} 条（建议再运行 translate 增量补齐）")

    if red_count:
        preview_report: Dict[str, List[str]] = {}
        for lang, by_file in sorted(redundant_keys.items(), key=lambda kv: kv[0]):
            for fn, keys in sorted(by_file.items(), key=lambda kv: kv[0]):
                for k in sorted(set(keys)):
                    preview_report.setdefault(lang, []).append(f"{fn}:{k}")
        content = _format_key_report(preview_report, title="⚠️ 冗余 key（示例预览）：", max_keys_per_file=4)
        print(content)
        p = _write_report_file(cfg, content, name="redundant_keys_preview")
        if p is not None:
            print(f"📄 已输出报告文件：{p}")

        opt = (cfg.options or {}).get("redundant_key_policy")
        if opt in {"keep", "delete"}:
            pass
        elif sys.stdin.isatty():
            ans = input("是否现在就删除这些冗余 key？(y=删除 / n=保留继续) [n]: ").strip().lower()
            if ans == "y":
                deleted = 0
                for lang, by_file in redundant_keys.items():
                    loc_dir = (cfg.lang_root / f"{lang}.lproj").resolve()
                    if not loc_dir.exists():
                        continue
                    for fn, keys in by_file.items():
                        fp = (loc_dir / fn).resolve()
                        if not fp.exists():
                            continue
                        try:
                            preamble, entries = parse_strings_file(fp)
                        except Exception:
                            continue
                        bad = set(keys)
                        new_entries = [e for e in entries if e.key not in bad]
                        if len(new_entries) != len(entries):
                            deleted += (len(entries) - len(new_entries))
                            write_strings_file(fp, preamble, new_entries, group_by_prefix=False)
                warns.append(f"已删除冗余 key：{deleted} 条")

    strict = bool(cfg.options.get("doctor_strict", False))
    if strict and warns:
        errors.extend([f"[STRICT] {w}" for w in warns])
        warns = []

    return _doctor_print_and_write(
        cfg,
        errors,
        warns,
        extra_sections={
            "缺失 key（按语言/文件）": missing_keys,
            "冗余 key（按语言/文件）": redundant_keys,
            "占位符不一致（按语言/文件）": placeholder_mismatch,
            "Base Swift camelCase 冲突（按文件）": base_camel_conflicts_by_file,
        },
    )


# ----------------------------
# sort 前的完整性检查：确保各语言 *.lproj 目录与 Base.lproj 的 *.strings 文件集一致
# - 若缺失：创建目录与空文件
# - 若多余：暂不删除（避免误删项目自定义文件）
# ----------------------------
def ensure_strings_files_integrity(cfg: StringsI18nConfig) -> Tuple[int, int]:
    base_dir = (cfg.lang_root / cfg.base_folder).resolve()
    if not base_dir.exists():
        raise ConfigError(f"Base 目录不存在：{base_dir}")

    base_strings = sorted([p for p in base_dir.glob('*.strings') if p.is_file()])
    if not base_strings:
        raise ConfigError(
            f"Base 目录下未发现任何 .strings 文件：{base_dir}\n"
            f"解决方法：确认 Xcode 是否已生成 Localizable.strings 等文件，或检查 lang_root/base_folder 配置。"
        )

    locales: List[Locale] = []
    if cfg.source_locale:
        locales.append(cfg.source_locale)
    locales.extend(cfg.core_locales or [])
    locales.extend(cfg.target_locales or [])

    created_dirs = 0
    created_files = 0

    for loc in locales:
        loc_dir = (cfg.lang_root / f"{loc.code}.lproj").resolve()
        if not loc_dir.exists():
            loc_dir.mkdir(parents=True, exist_ok=True)
            created_dirs += 1

        existing = {p.name for p in loc_dir.glob('*.strings') if p.is_file()}
        for base_file in base_strings:
            if base_file.name not in existing:
                target = loc_dir / base_file.name
                target.write_text('', encoding='utf-8')
                created_files += 1

    return created_dirs, created_files


# ----------------------------
# .strings 解析/写回 + 排序
# ----------------------------
_STRINGS_ENTRY_RE = re.compile(r'^\s*"((?:\\.|[^"\\])*)"\s*=\s*"((?:\\.|[^"\\])*)"\s*;\s*$')

@dataclass
class StringsEntry:
    key: str
    value: str
    comments: List[str]  # 原样保存（行级），写回时放在 entry 上方


def _is_comment_line(line: str) -> bool:
    s = line.lstrip()
    return s.startswith("//") or s.startswith("/*") or s.startswith("*") or s.startswith("*/")


def _group_prefix(key: str) -> str:
    # 规则：优先按 '.' 的第一个段；否则按 '_' 的第一个段；否则全 key
    if "." in key:
        return key.split(".", 1)[0]
    if "_" in key:
        return key.split("_", 1)[0]
    return key


def parse_strings_file(path: Path) -> Tuple[List[str], List[StringsEntry]]:
    """解析 iOS .strings 文件，保留注释（注释归属到其下方的 key）。"""
    if not path.exists():
        return [], []

    lines = path.read_text(encoding="utf-8").splitlines()
    preamble: List[str] = []
    entries: List[StringsEntry] = []

    pending_comments: List[str] = []
    seen_first_entry = False

    for line in lines:
        m = _STRINGS_ENTRY_RE.match(line)
        if m:
            key, value = m.group(1), m.group(2)

            while pending_comments and pending_comments[-1].strip() == "":
                pending_comments.pop()

            entries.append(StringsEntry(key=key, value=value, comments=pending_comments))
            pending_comments = []
            seen_first_entry = True
            continue

        if not seen_first_entry:
            preamble.append(line)
            continue

        if line.strip() == "" or _is_comment_line(line):
            pending_comments.append(line)
        else:
            pending_comments.append(line)

    return preamble, entries


def write_strings_file(path: Path, preamble: List[str], entries: List[StringsEntry], *, group_by_prefix: bool = True) -> None:
    out_lines: List[str] = []

    if preamble:
        while preamble and preamble[-1].strip() == "":
            preamble.pop()
        out_lines.extend(preamble)
        out_lines.append("")

    prev_group: Optional[str] = None
    for e in entries:
        grp = _group_prefix(e.key)
        if prev_group is not None and grp != prev_group:
            out_lines.append("")
        prev_group = grp

        if e.comments:
            comments = list(e.comments)
            while comments and comments[0].strip() == "":
                comments.pop(0)
            while comments and comments[-1].strip() == "":
                comments.pop()
            out_lines.extend(comments)

        out_lines.append(f"\"{e.key}\" = \"{e.value}\";")

    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def sort_strings_entries(preamble: List[str], entries: List[StringsEntry]) -> Tuple[List[str], List[StringsEntry]]:
    entries_sorted = sorted(entries, key=lambda e: (_group_prefix(e.key), e.key))
    return preamble, entries_sorted


def _collect_duplicates(entries: List[StringsEntry]) -> List[str]:
    seen = set()
    dups = set()
    for e in entries:
        if e.key in seen:
            dups.add(e.key)
        else:
            seen.add(e.key)
    return sorted(dups)


def _apply_duplicate_policy(entries: List[StringsEntry], policy: str) -> List[StringsEntry]:
    """处理重复 key。policy:
    - keep_first: 只保留第一次出现的 key
    - delete_all: 重复 key（出现>=2）全部删除
    """
    if policy not in {"keep_first", "delete_all"}:
        return entries

    dups = set(_collect_duplicates(entries))
    if not dups:
        return entries

    if policy == "keep_first":
        kept = []
        seen = set()
        for e in entries:
            if e.key in dups:
                if e.key in seen:
                    continue
                seen.add(e.key)
            kept.append(e)
        return kept

    return [e for e in entries if e.key not in dups]


def _base_keys_by_file(cfg: StringsI18nConfig) -> Dict[str, set]:
    """读取 Base.lproj 下每个 *.strings 的 key 集合。key 用于判定冗余字段。"""
    base_dir = (cfg.lang_root / cfg.base_folder).resolve()
    if not base_dir.exists():
        raise ConfigError(f"未找到 base_folder: {base_dir}")
    keys_map: Dict[str, set] = {}
    for fp in sorted(base_dir.glob("*.strings")):
        _, entries = parse_strings_file(fp)
        keys_map[fp.name] = set(e.key for e in entries)
    return keys_map


def scan_redundant_keys(cfg: StringsI18nConfig, base_keys_map: Dict[str, set]) -> Dict[str, List[str]]:
    """冗余字段：Base 中没有，但其他语言中有的 key。返回 {locale_code: ["File.strings:key", ...]}"""
    locales: List[Locale] = []
    if cfg.source_locale:
        locales.append(cfg.source_locale)
    locales.extend(cfg.core_locales or [])
    locales.extend(cfg.target_locales or [])

    report: Dict[str, List[str]] = {}
    for loc in locales:
        loc_dir = (cfg.lang_root / f"{loc.code}.lproj").resolve()
        if not loc_dir.exists():
            continue
        redundant: List[str] = []
        for fp in sorted(loc_dir.glob("*.strings")):
            base_keys = base_keys_map.get(fp.name, set())
            _, entries = parse_strings_file(fp)
            for e in entries:
                if e.key not in base_keys:
                    redundant.append(f"{fp.name}:{e.key}")
        if redundant:
            redundant = sorted(set(redundant), key=lambda s: (s.split(":",1)[0], s.split(":",1)[1]))
            report[loc.code] = redundant
    return report


def _format_key_report(report: Dict[str, List[str]], *, title: str, max_keys_per_file: int = 30) -> str:
    lines: List[str] = []
    lines.append(title)
    lines.append("")
    for lang, items in sorted(report.items(), key=lambda kv: kv[0]):
        by_file: Dict[str, List[str]] = {}
        for it in items:
            if ":" in it:
                fn, key = it.split(":", 1)
            else:
                fn, key = "(unknown)", it
            by_file.setdefault(fn, []).append(key)

        total = sum(len(v) for v in by_file.values())
        lines.append(f"【{lang}】共 {total} 个")
        for fn in sorted(by_file.keys()):
            keys = sorted(set(by_file[fn]))
            shown = keys[:max_keys_per_file]
            remain = len(keys) - len(shown)
            preview = ", ".join(shown)
            if remain > 0:
                preview = preview + f", …（还有 {remain} 个）"
            wrapped = textwrap.fill(preview, width=100, subsequent_indent=" " * (len(fn) + 6))
            lines.append(f"  - {fn} ({len(keys)}): {wrapped}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write_report_file(cfg: StringsI18nConfig, content: str, *, name: str) -> Optional[Path]:
    """doctor 阶段不落盘报告：按需求禁用所有 report 文件写入。"""
    return None


def _resolve_placeholder_mismatch_policy(
    cfg: StringsI18nConfig,
    mismatches: Dict[str, Dict[str, List[Tuple[str, List[str], List[str]]]]],
    *,
    max_items: int = 3,
) -> str:
    if not mismatches:
        return "keep"

    flat: List[Tuple[str, str, str, List[str], List[str]]] = []
    for lang, by_file in mismatches.items():
        for fn, items in by_file.items():
            for (k, bph, tph) in items:
                flat.append((lang, fn, k, bph, tph))
    flat.sort(key=lambda x: (x[0], x[1], x[2]))

    lines: List[str] = []
    lines.append("⚠️ 发现占位符不一致（示例）：")
    for i, (lang, fn, k, bph, tph) in enumerate(flat[:max_items], 1):
        lines.append(f"  {i}. {lang}/{fn}  key={k}")
        lines.append(f"     Base: {bph}")
        lines.append(f"     Lang: {tph}")
    if len(flat) > max_items:
        lines.append(f"  …（还有 {len(flat) - max_items} 条未展示）")
    lines.append("")
    lines.append("修复建议：")
    lines.append("  - 推荐：删除这些条目，让 translate 按 Base 重新生成（最安全，避免运行时崩溃）")
    lines.append("  - 或者：人工修正目标语言 value 的占位符，使其与 Base 完全一致")
    content = "\n".join(lines) + "\n"
    print(content)
    p = _write_report_file(cfg, content, name="placeholder_mismatch_preview")
    if p is not None:
        print(f"📄 已输出报告文件：{p}")

    opt = (cfg.options or {}).get("placeholder_mismatch_policy")
    if opt in {"keep", "delete"}:
        print(f"✅ 使用配置 placeholder_mismatch_policy={opt}")
        return opt

    if not sys.stdin.isatty():
        return "keep"

    while True:
        ans = input("如何处理占位符不一致？(d=删除这些条目 / k=保留继续) [k]: ").strip().lower()
        if ans == "" or ans == "k":
            return "keep"
        if ans == "d":
            return "delete"
        print("请输入 d 或 k")


def _apply_placeholder_mismatch_delete(
    cfg: StringsI18nConfig,
    mismatches: Dict[str, Dict[str, List[Tuple[str, List[str], List[str]]]]],
) -> int:
    deleted = 0
    for lang, by_file in mismatches.items():
        loc_dir = (cfg.lang_root / f"{lang}.lproj").resolve()
        if not loc_dir.exists():
            continue
        for fn, items in by_file.items():
            fp = (loc_dir / fn).resolve()
            if not fp.exists():
                continue
            try:
                preamble, entries = parse_strings_file(fp)
            except Exception:
                continue
            bad_keys = {k for (k, _, _) in items}
            if not bad_keys:
                continue
            new_entries = [e for e in entries if e.key not in bad_keys]
            if len(new_entries) != len(entries):
                deleted += (len(entries) - len(new_entries))
                write_strings_file(fp, preamble, new_entries, group_by_prefix=False)
    return deleted


def _resolve_redundant_policy(cfg: StringsI18nConfig, report: Dict[str, List[str]]) -> str:
    if not report:
        return "keep"

    content = _format_key_report(report, title="⚠️ 发现冗余字段（Base 中没有，但其他语言存在）：", max_keys_per_file=4)
    print(content)
    p = _write_report_file(cfg, content, name="redundant_keys")
    if p is not None:
        print(f"📄 已输出报告文件：{p}")

    opt = (cfg.options or {}).get("redundant_key_policy")
    if opt in {"keep", "delete"}:
        print(f"✅ 使用配置 redundant_key_policy={opt}")
        return opt

    while True:
        ans = input("是否删除这些冗余字段？(y=删除 / n=保留 / c=取消本次 sort) [n]: ").strip().lower()
        if ans == "" or ans == "n":
            return "keep"
        if ans == "y":
            return "delete"
        if ans == "c":
            return "cancel"
        print("请输入 y / n / c")


def scan_duplicate_keys(cfg: StringsI18nConfig) -> Dict[str, List[str]]:
    result: Dict[str, set] = {}

    def add(lang_label: str, keys: List[str]) -> None:
        if not keys:
            return
        s = result.get(lang_label)
        if s is None:
            s = set()
            result[lang_label] = s
        s.update(keys)

    base_dir = (cfg.lang_root / cfg.base_folder).resolve()
    if base_dir.exists():
        for fp in sorted(base_dir.glob("*.strings")):
            _, entries = parse_strings_file(fp)
            add("Base", _collect_duplicates(entries))

    locales: List[Locale] = []
    if cfg.source_locale:
        locales.append(cfg.source_locale)
    locales.extend(cfg.core_locales or [])
    locales.extend(cfg.target_locales or [])

    for loc in locales:
        loc_dir = (cfg.lang_root / f"{loc.code}.lproj").resolve()
        if not loc_dir.exists():
            continue
        for fp in sorted(loc_dir.glob("*.strings")):
            _, entries = parse_strings_file(fp)
            add(loc.code, _collect_duplicates(entries))

    return {k: sorted(list(v)) for k, v in result.items()}


def _resolve_duplicate_policy(cfg: StringsI18nConfig, dup_report: Dict[str, List[str]]) -> str:
    if not dup_report:
        return "keep_first"

    opt = (cfg.options or {}).get("duplicate_key_policy")
    if isinstance(opt, str) and opt in {"keep_first", "delete_all"}:
        return opt

    print("\n⚠️ 检测到重复 key：")
    for lang, keys in dup_report.items():
        print(f"- {lang}: {keys}")
    print("\n请选择处理策略：\n  1) 只保留第一个（keep_first）\n  2) 全部删除（delete_all）\n  3) 取消本次 sort\n")
    try:
        choice = input("输入 1/2/3（默认 1）：").strip()
    except EOFError:
        choice = ""

    if choice == "2":
        return "delete_all"
    if choice == "3":
        return "cancel"
    return "keep_first"


def sort_base_strings_files(cfg: StringsI18nConfig, *, duplicate_policy: str) -> int:
    base_dir = cfg.lang_root / cfg.base_folder
    if not base_dir.exists():
        raise ConfigError(f"Base 目录不存在：{base_dir}")

    files = sorted(base_dir.glob("*.strings"))
    if not files:
        print(f"⚠️ Base.lproj 下未找到 *.strings：{base_dir}")
        return 0

    changed = 0
    for fp in files:
        preamble, entries = parse_strings_file(fp)

        entries = _apply_duplicate_policy(entries, duplicate_policy)
        _, entries_sorted = sort_strings_entries(preamble, entries)

        old_text = fp.read_text(encoding="utf-8") if fp.exists() else ""
        tmp_path = fp.with_suffix(fp.suffix + ".__tmp__")
        write_strings_file(tmp_path, preamble, entries_sorted, group_by_prefix=True)
        new_text = tmp_path.read_text(encoding="utf-8")
        tmp_path.unlink(missing_ok=True)

        if old_text != new_text:
            fp.write_text(new_text, encoding="utf-8")
            changed += 1

    return changed


def sort_other_locale_strings_files(cfg: StringsI18nConfig, *, duplicate_policy: str, base_keys_map: Dict[str, set], redundant_policy: str) -> int:
    locales: List[Locale] = []
    if cfg.source_locale:
        locales.append(cfg.source_locale)
    locales.extend(cfg.core_locales or [])
    locales.extend(cfg.target_locales or [])

    changed = 0
    for loc in locales:
        loc_dir = (cfg.lang_root / f"{loc.code}.lproj").resolve()
        if not loc_dir.exists():
            continue

        files = sorted(loc_dir.glob("*.strings"))
        for fp in files:
            preamble, entries = parse_strings_file(fp)

            entries = _apply_duplicate_policy(entries, duplicate_policy)

            if redundant_policy == "delete":
                base_keys = base_keys_map.get(fp.name, set())
                entries = [e for e in entries if e.key in base_keys]

            entries_sorted = sorted(entries, key=lambda e: e.key)

            old_text = fp.read_text(encoding="utf-8") if fp.exists() else ""
            tmp_path = fp.with_suffix(fp.suffix + ".__tmp__")
            write_strings_file(tmp_path, preamble, entries_sorted, group_by_prefix=False)
            new_text = tmp_path.read_text(encoding="utf-8")
            tmp_path.unlink(missing_ok=True)

            if old_text != new_text:
                fp.write_text(new_text, encoding="utf-8")
                changed += 1

    return changed


def _scan_base_camelcase_conflicts_for_sort(cfg: StringsI18nConfig) -> Dict[str, Dict[str, List[str]]]:
    """给 sort 用：按 Base 文件扫描 camelCase 冲突，返回 {filename: {prop: [keys...]}}"""
    base_dir = (cfg.lang_root / cfg.base_folder).resolve()
    out: Dict[str, Dict[str, List[str]]] = {}
    if not base_dir.exists():
        return out
    for fp in sorted(base_dir.glob("*.strings")):
        try:
            _, entries = parse_strings_file(fp)
        except Exception:
            continue
        conflicts = scan_camelcase_conflicts(entries)
        if conflicts:
            out[fp.name] = conflicts
    return out


def run_sort(cfg: StringsI18nConfig) -> None:
    # sort 之前需要先检测文件完整性：确保每个语言目录下的 *.strings 与 Base.lproj 一致
    if run_doctor(cfg) != 0:
        print("❌ sort 中止：doctor 未通过")
        return

    # ✅ 再额外“明确打印一次”camelCase 冲突（满足你“sort 检查时也要打印出来处理”的要求）
    camel_conflicts_by_file = _scan_base_camelcase_conflicts_for_sort(cfg)
    if camel_conflicts_by_file:
        print("\n❌ sort 检测到 Base Swift camelCase 属性名冲突（请先手动处理 key；sort 不会自动修）：")
        for fn in sorted(camel_conflicts_by_file.keys()):
            conflicts = camel_conflicts_by_file[fn]
            print(f"\n[{fn}]")
            for prop in sorted(conflicts.keys()):
                print(f"- {prop}: {conflicts[prop]}")
        return

    try:
        created_dirs, created_files = ensure_strings_files_integrity(cfg)
    except ConfigError as e:
        print(f"❌ sort 中止：{e}")
        return

    if created_dirs or created_files:
        print(f"✅ 完整性修复：创建目录 {created_dirs} 个，创建 .strings 文件 {created_files} 个")
    else:
        print("✅ 完整性检查通过：各语言 *.strings 文件集与 Base 一致")

    dup_report = scan_duplicate_keys(cfg)
    policy = _resolve_duplicate_policy(cfg, dup_report)
    if policy == "cancel":
        print("❌ sort 已取消（未做任何修改）")
        return

    try:
        base_keys_map = _base_keys_by_file(cfg)
    except ConfigError as e:
        print(f"❌ sort 中止：{e}")
        return

    redundant_report = scan_redundant_keys(cfg, base_keys_map)
    redundant_policy = _resolve_redundant_policy(cfg, redundant_report)
    if redundant_policy == "cancel":
        print("❌ sort 已取消（未做任何修改）")
        return

    try:
        base_changed = sort_base_strings_files(cfg, duplicate_policy=policy)
    except ConfigError as e:
        print(f"❌ sort 中止：{e}")
        return

    try:
        other_changed = sort_other_locale_strings_files(
            cfg,
            duplicate_policy=policy,
            base_keys_map=base_keys_map,
            redundant_policy=redundant_policy,
        )
    except ConfigError as e:
        print(f"❌ sort 中止：{e}")
        return

    if base_changed:
        print(f"✅ Base.lproj 排序完成：更新 {base_changed} 个 .strings 文件")
    else:
        print("✅ Base.lproj 已是有序状态：无需改动")

    if other_changed:
        print(f"✅ 其他语言排序完成：更新 {other_changed} 个 .strings 文件")
    else:
        print("✅ 其他语言已是有序状态：无需改动")
