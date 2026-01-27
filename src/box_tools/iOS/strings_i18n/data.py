from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

import yaml

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
    # 这里只做最小诊断：路径存在 + Base.lproj 存在
    if not cfg.lang_root.exists():
        print(f"❌ lang_root 不存在：{cfg.lang_root}")
        return 1

    base_dir = (cfg.lang_root / cfg.base_folder).resolve()
    if not base_dir.exists():
        print(f"❌ Base 目录不存在：{base_dir}")
        return 1

    languages_ok = cfg.languages_path.exists()
    if not languages_ok:
        print(f"❌ languages.json 不存在：{cfg.languages_path}")
        return 1

    print("✅ doctor 通过（骨架：仅做路径与基础结构检查）")
    return 0


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
        # 没有任何 .strings：这通常意味着工程结构不对或未生成本地化文件
        raise ConfigError(
            f"Base 目录下未发现任何 .strings 文件：{base_dir}"
            f"解决方法：确认 Xcode 是否已生成 Localizable.strings 等文件，或检查 lang_root/base_folder 配置。"
        )

    locales: List[Locale] = []
    # source + core + target（Base 本身不需要对齐）
    if cfg.source_locale:
        locales.append(cfg.source_locale)
    locales.extend(cfg.core_locales or [])
    locales.extend(cfg.target_locales or [])

    created_dirs = 0
    created_files = 0

    for loc in locales:
        # 约定：<code>.lproj（例如：en.lproj / zh-Hant.lproj）
        loc_dir = (cfg.lang_root / f"{loc.code}.lproj").resolve()
        if not loc_dir.exists():
            loc_dir.mkdir(parents=True, exist_ok=True)
            created_dirs += 1

        existing = {p.name for p in loc_dir.glob('*.strings') if p.is_file()}
        for base_file in base_strings:
            if base_file.name not in existing:
                target = loc_dir / base_file.name
                # 创建空文件（UTF-8），后续 translate/sort 会填充/排序
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

            # 清理 comments：去掉末尾多余空行，确保“注释在字段上方”
            while pending_comments and pending_comments[-1].strip() == "":
                pending_comments.pop()

            entries.append(StringsEntry(key=key, value=value, comments=pending_comments))
            pending_comments = []
            seen_first_entry = True
            continue

        if not seen_first_entry:
            # 文件头部：完整保留（通常是版权/说明注释）
            preamble.append(line)
            continue

        # entry 之间的内容：认为是“下一个 entry 的注释/空行”
        if line.strip() == "" or _is_comment_line(line):
            pending_comments.append(line)
        else:
            # 兼容非标准行：不丢内容，归到下一个 entry 的注释块中
            pending_comments.append(line)

    return preamble, entries


def write_strings_file(path: Path, preamble: List[str], entries: List[StringsEntry]) -> None:
    out_lines: List[str] = []

    # 写 header/preamble（原样）
    if preamble:
        # 去掉末尾多余空行（避免文件头太松）
        while preamble and preamble[-1].strip() == "":
            preamble.pop()
        out_lines.extend(preamble)
        out_lines.append("")  # header 与正文之间留一空行

    # entries 已经排序/分组完成；写回时保证：注释紧贴在字段上方
    prev_group: Optional[str] = None
    for e in entries:
        grp = _group_prefix(e.key)
        if prev_group is not None and grp != prev_group:
            out_lines.append("")  # 组之间空一行
        prev_group = grp

        # 写注释
        if e.comments:
            # 去掉注释块首尾多余空行
            comments = list(e.comments)
            while comments and comments[0].strip() == "":
                comments.pop(0)
            while comments and comments[-1].strip() == "":
                comments.pop()
            out_lines.extend(comments)

        # 写 entry（统一格式化）
        out_lines.append(f"\"{e.key}\" = \"{e.value}\";")

    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def sort_strings_entries(preamble: List[str], entries: List[StringsEntry]) -> Tuple[List[str], List[StringsEntry]]:
    # 根据前缀分组 + key 排序
    entries_sorted = sorted(entries, key=lambda e: (_group_prefix(e.key), e.key))
    return preamble, entries_sorted


def sort_base_strings_files(cfg: StringsI18nConfig) -> int:
    """对 Base.lproj 下的所有 *.strings 文件排序（保留注释，注释写在字段上方）。"""
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
        _, entries_sorted = sort_strings_entries(preamble, entries)

        # 判断是否变化（简单对比 key 顺序）
        old_keys = [e.key for e in entries]
        new_keys = [e.key for e in entries_sorted]
        if old_keys != new_keys:
            write_strings_file(fp, preamble, entries_sorted)
            changed += 1

    return changed

def run_sort(cfg: StringsI18nConfig) -> None:
    # sort 之前需要先检测文件完整性：确保每个语言目录下的 *.strings 与 Base.lproj 一致
    if run_doctor(cfg) != 0:
        print("❌ sort 中止：doctor 未通过")
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

    # 排序 Base.lproj（保留注释；注释在字段上方；按 key 排序并按前缀分组）
    try:
        changed = sort_base_strings_files(cfg)
    except ConfigError as e:
        print(f"❌ sort 中止：{e}")
        return

    if changed:
        print(f"✅ Base.lproj 排序完成：更新 {changed} 个 .strings 文件")
    else:
        print("✅ Base.lproj 已是有序状态：无需改动")



