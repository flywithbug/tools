from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

import yaml

# ----------------------------
# 常量 / 默认文件名
# ----------------------------
DEFAULT_TEMPLATE_NAME = "slang_i18n.yaml"   # 内置模板文件（带注释）
DEFAULT_LANGUAGES_NAME = "languages.json"  # 本地语言列表文件

LOCALE_META_KEY = "@@locale"               # i18n json 的 meta key（固定第一位）
I18N_FILE_SUFFIX = ".i18n.json"            # 业务文件后缀


# ----------------------------
# 异常类型
# ----------------------------
class ConfigError(RuntimeError):
    """用于启动阶段的配置错误（更友好的报错与解决建议）"""
    pass


# ----------------------------
# 数据模型（按你的默认模板 schema）
# ----------------------------
@dataclass(frozen=True)
class Locale:
    code: str
    name_en: str


@dataclass(frozen=True)
class I18nConfig:
    i18n_dir: Path                 # 绝对路径（按 project_root 解析）
    source_locale: Locale
    target_locales: List[Locale]
    openai_model: str
    max_workers: int
    prompts: Dict[str, object]
    options: Dict[str, object]


def override_i18n_dir(cfg: I18nConfig, i18n_dir: Path) -> I18nConfig:
    return I18nConfig(
        i18n_dir=i18n_dir.resolve(),
        source_locale=cfg.source_locale,
        target_locales=cfg.target_locales,
        openai_model=cfg.openai_model,
        max_workers=cfg.max_workers,
        prompts=cfg.prompts,
        options=cfg.options,
    )


# ----------------------------
# 内置文件读取（模板 / 默认 languages）
# ----------------------------
def _pkg_file(name: str) -> Path:
    # 默认把模板与默认 languages.json 放在 data.py 同目录
    return Path(__file__).with_name(name)


def ensure_languages_json(project_root: Path) -> Path:
    """
    如果本地没有 languages.json，则用内置默认 languages.json 生成一份，方便后续改动。
    """
    dst = (project_root / DEFAULT_LANGUAGES_NAME).resolve()
    if dst.exists():
        return dst

    src = _pkg_file(DEFAULT_LANGUAGES_NAME)
    if not src.exists():
        raise FileNotFoundError(f"内置默认 {DEFAULT_LANGUAGES_NAME} 不存在：{src}")

    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def load_target_locales_from_languages_json(languages_path: Path, source_code: str) -> List[Dict[str, str]]:
    """
    从 languages.json 生成 target_locales（code + name_en），并：
    - 按 code 去重（保序）
    - 剔除 source_code
    """
    arr = json.loads(languages_path.read_text(encoding="utf-8"))
    if not isinstance(arr, list):
        raise ValueError(f"{DEFAULT_LANGUAGES_NAME} 顶层必须是数组")

    seen = set()
    out: List[Dict[str, str]] = []

    for item in arr:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip()
        name_en = str(item.get("name_en", "")).strip()
        if not code or not name_en:
            continue
        if code == source_code:
            continue
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name_en": name_en})

    return out


# ----------------------------
# YAML 模板“保注释”局部替换：只替换 target_locales block
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
    next_key = re.search(r"(?m)^(?!target_locales:)[A-Za-z_][A-Za-z0-9_]*:\s*$", after)

    if next_key:
        end = start_match.end() + next_key.start()
    else:
        end = len(template_text)

    return template_text[:start] + new_block + template_text[end:]


# ----------------------------
# init：生成/校验配置，确保 languages.json + i18nDir 存在
# ----------------------------
def init_config(project_root: Path, cfg_path: Path) -> None:
    project_root = project_root.resolve()
    cfg_path = cfg_path.resolve()

    # 1) 确保 languages.json 存在（不存在则生成默认）
    languages_path = ensure_languages_json(project_root)

    # 2) 不存在 cfg：用模板生成（保留注释）+ 动态替换 target_locales
    if not cfg_path.exists():
        tpl = _pkg_file(DEFAULT_TEMPLATE_NAME)
        if not tpl.exists():
            raise FileNotFoundError(f"内置默认配置模板不存在：{tpl}")

        tpl_text = tpl.read_text(encoding="utf-8")
        raw_tpl = yaml.safe_load(tpl_text) or {}
        validate_config(raw_tpl)  # 模板自身也要合法（必须包含 i18nDir）

        source_code = raw_tpl["source_locale"]["code"]
        targets = load_target_locales_from_languages_json(languages_path, source_code)

        out_text = replace_target_locales_block(tpl_text, targets)
        cfg_path.write_text(out_text, encoding="utf-8")

    # 3) 校验配置（此处不强制要求 i18nDir 已存在，因为 init 会创建）
    raw = assert_config_ok(cfg_path, project_root=project_root, check_i18n_dir_exists=False)

    # 4) 创建 i18nDir 目录（按 project_root 解析）
    i18n_dir_path = (project_root / raw["i18nDir"]).resolve()
    i18n_dir_path.mkdir(parents=True, exist_ok=True)


# ----------------------------
# 启动优先校验入口（可选检查 i18nDir 是否存在）
# ----------------------------
def assert_config_ok(
        cfg_path: Path,
        project_root: Optional[Path] = None,
        check_i18n_dir_exists: bool = True,
) -> Dict:
    """
    启动时优先校验：
    - 文件存在性
    - YAML 可解析
    - schema + 语义校验
    - （可选）i18nDir 目录存在性
    """
    cfg_path = cfg_path.resolve()
    project_root = (project_root or cfg_path.parent).resolve()

    if not cfg_path.exists():
        raise ConfigError(
            f"配置文件不存在：{cfg_path}\n"
            f"解决方法：运行 `box_slang_i18n init` 生成默认配置。"
        )

    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise ConfigError(
            f"配置文件无法解析为 YAML：{cfg_path}\n"
            f"原因：{e}\n"
            f"解决方法：修复 YAML 格式或运行 `box_slang_i18n init` 重新生成。"
        )

    try:
        validate_config(raw)
    except Exception as e:
        raise ConfigError(
            f"配置文件校验失败：{cfg_path}\n"
            f"原因：{e}\n"
            f"解决方法：修复配置字段/类型，或运行 `box_slang_i18n init` 重新生成。"
        )

    if check_i18n_dir_exists:
        i18n_dir_path = (project_root / raw["i18nDir"]).resolve()
        if not i18n_dir_path.exists() or not i18n_dir_path.is_dir():
            raise ConfigError(
                f"i18nDir 目录不存在：{i18n_dir_path}\n"
                f"解决方法：\n"
                f"  1) 创建目录：mkdir -p {i18n_dir_path}\n"
                f"  2) 或运行 `box_slang_i18n init` 让工具初始化\n"
                f"  3) 或修改 {cfg_path.name} 里的 i18nDir 指向正确目录"
            )

    return raw


# ----------------------------
# load_config：把 raw dict 转成 I18nConfig（i18n_dir 解析为绝对路径）
# ----------------------------
def load_config(cfg_path: Path, project_root: Optional[Path] = None) -> I18nConfig:
    cfg_path = cfg_path.resolve()
    project_root = (project_root or cfg_path.parent).resolve()

    raw = assert_config_ok(cfg_path, project_root=project_root, check_i18n_dir_exists=True)

    i18n_dir = (project_root / raw["i18nDir"]).resolve()

    src = raw["source_locale"]
    targets = raw["target_locales"]

    return I18nConfig(
        i18n_dir=i18n_dir,
        source_locale=Locale(code=src["code"], name_en=src["name_en"]),
        target_locales=[Locale(code=t["code"], name_en=t["name_en"]) for t in targets],
        openai_model=str(raw["openAIModel"]),
        max_workers=int(raw["maxWorkers"]),
        prompts=raw["prompts"],
        options=raw["options"],
    )


# ----------------------------
# validate_config：字段 + 类型 + 关键语义校验（含 i18nDir）
# ----------------------------
def validate_config(raw: Dict) -> None:
    required_top = ["openAIModel", "maxWorkers", "i18nDir", "source_locale", "target_locales", "prompts", "options"]
    for k in required_top:
        if k not in raw:
            raise ValueError(f"配置缺少字段：{k}")

    if not isinstance(raw["openAIModel"], str) or not raw["openAIModel"].strip():
        raise ValueError("openAIModel 必须是非空字符串")

    mw = raw["maxWorkers"]
    if not isinstance(mw, int) or mw < 0:
        raise ValueError("maxWorkers 必须是 0（自动）或正整数（固定并发）")

    i18n_dir = raw["i18nDir"]
    if not isinstance(i18n_dir, str) or not i18n_dir.strip():
        raise ValueError("i18nDir 必须是非空字符串（例如 i18n）")

    src = raw["source_locale"]
    if not isinstance(src, dict):
        raise ValueError("source_locale 必须是 object")
    if "code" not in src or "name_en" not in src:
        raise ValueError("source_locale 必须包含 code + name_en")
    if not str(src["code"]).strip() or not str(src["name_en"]).strip():
        raise ValueError("source_locale.code/name_en 不能为空")

    targets = raw["target_locales"]
    if not isinstance(targets, list) or len(targets) == 0:
        raise ValueError("target_locales 必须是非空数组")

    codes: List[str] = []
    for i, t in enumerate(targets):
        if not isinstance(t, dict):
            raise ValueError(f"target_locales[{i}] 必须是 object")
        if "code" not in t or "name_en" not in t:
            raise ValueError(f"target_locales[{i}] 必须包含 code + name_en")
        code = str(t["code"]).strip()
        name = str(t["name_en"]).strip()
        if not code or not name:
            raise ValueError(f"target_locales[{i}].code/name_en 不能为空")
        codes.append(code)

    if len(set(codes)) != len(codes):
        raise ValueError("target_locales.code 存在重复，请去重")

    src_code = str(src["code"]).strip()
    if src_code in set(codes):
        raise ValueError("target_locales 里包含 source_locale.code，请移除（source 不能作为 target）")

    prompts = raw["prompts"]
    if not isinstance(prompts, dict):
        raise ValueError("prompts 必须是 object")
    if "default_en" not in prompts or not isinstance(prompts["default_en"], str):
        raise ValueError("prompts.default_en 必须存在且为字符串")

    options = raw["options"]
    if not isinstance(options, dict):
        raise ValueError("options 必须是 object")
    for k in ["sort_keys", "cleanup_extra_keys", "incremental_translate", "normalize_filenames"]:
        if k not in options:
            raise ValueError(f"options 缺少字段：{k}")


# ----------------------------
# JSON 扫描 + flat 校验 + 排序
# ----------------------------
def list_locale_files(i18n_dir: Path) -> List[Path]:
    if not i18n_dir.exists():
        return []
    return sorted(i18n_dir.glob("**/*.json"))


def is_meta_key(key: str) -> bool:
    return isinstance(key, str) and key.startswith("@@")


def ensure_flat_json(obj: Any, file_path: Path) -> Dict[str, Any]:
    """
    规则：
    - JSON 顶层必须是 object
    - 禁止嵌套 object/array（flat）
    - 对普通 key：value 必须是 string（当前允许 None）
    - 对 @@* 元字段：允许非 string（例如 @@dirty: true）
    """
    if not isinstance(obj, dict):
        raise ValueError(f"JSON 顶层必须是 object: {file_path}")

    for k, v in obj.items():
        if isinstance(v, (dict, list)):
            raise ValueError(f"检测到嵌套 JSON（不允许）：{file_path} key={k}")

        if is_meta_key(k):
            continue

        if v is not None and not isinstance(v, str):
            raise ValueError(f"value 必须是 string：{file_path} key={k} type={type(v).__name__}")

    return obj


def sort_json_keys(data_obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    排序规则：
    1) @@locale 固定第一（如果存在）
    2) 其它 @@* 元字段按 key 字典序排在顶部
    3) 普通 key 按 key 字典序排在后面
    """
    out: Dict[str, Any] = {}

    # 1) @@locale 固定第一
    if LOCALE_META_KEY in data_obj:
        out[LOCALE_META_KEY] = data_obj[LOCALE_META_KEY]

    # 2) 其它 @@*（排除 @@locale）字典序
    other_meta_items: List[Tuple[str, Any]] = sorted(
        ((k, v) for k, v in data_obj.items() if is_meta_key(k) and k != LOCALE_META_KEY),
        key=lambda kv: kv[0],
    )
    out.update(dict(other_meta_items))

    # 3) 普通 key 字典序
    normal_items: List[Tuple[str, Any]] = sorted(
        ((k, v) for k, v in data_obj.items() if not is_meta_key(k)),
        key=lambda kv: kv[0],
    )
    out.update(dict(normal_items))

    return out


def read_json(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        # 给出：文件、行列、以及错误行附近片段，便于定位
        line = e.lineno
        col = e.colno

        lines = text.splitlines()
        # 取错误行上下各 2 行（可按需调）
        start = max(0, line - 3)
        end = min(len(lines), line + 2)

        snippet = []
        for i in range(start, end):
            prefix = ">>" if (i + 1) == line else "  "
            snippet.append(f"{prefix} {i+1:>4}: {lines[i]}")

        snippet_text = "\n".join(snippet)

        raise ValueError(
            f"❌ JSON 格式错误：{path}\n"
            f"位置：第 {line} 行，第 {col} 列\n"
            f"原因：{e.msg}\n"
            f"附近内容：\n{snippet_text}\n"
        ) from None

    return ensure_flat_json(obj, path)


def write_json(path: Path, data_obj: Dict[str, Any]) -> None:
    ensure_flat_json(data_obj, path)
    path.write_text(json.dumps(data_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ----------------------------
# i18n 文件命名规范检查（用于 doctor / sync）
# folderName = 文件夹名转 lowerCamelCase
# 文件名：{{folderName}}_{{code}}.i18n.json
# ----------------------------
def to_lower_camel(folder: str) -> str:
    """
    folder -> lowerCamelCase
    分隔符：任何非字母数字（_ - 空格 等）
    """
    s = folder.strip()
    if not s:
        return s

    parts = [p for p in re.split(r"[^0-9A-Za-z]+", s) if p]
    if not parts:
        return s

    cap = [p[:1].upper() + p[1:].lower() if p else "" for p in parts]
    joined = "".join(cap)
    return joined[:1].lower() + joined[1:]


def expected_i18n_filename(module_dir: Path, locale_code: str) -> str:
    folder_name = to_lower_camel(module_dir.name)
    return f"{folder_name}_{locale_code}{I18N_FILE_SUFFIX}"


def list_module_dirs(i18n_dir: Path) -> List[Path]:
    if not i18n_dir.exists():
        return []
    return sorted([p for p in i18n_dir.iterdir() if p.is_dir()])


def all_locale_codes(cfg: I18nConfig) -> List[str]:
    codes = [cfg.source_locale.code] + [t.code for t in cfg.target_locales]
    seen = set()
    out: List[str] = []
    for c in codes:
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


@dataclass
class DoctorIssue:
    kind: str        # "missing" | "bad_name" | "no_module_dirs"
    message: str
    path: Optional[Path] = None


def check_i18n_naming_and_existence(cfg: I18nConfig) -> List[DoctorIssue]:
    """
    doctor 用检查：
    - i18nDir 下必须有业务子目录（模块目录）
    - 每个模块目录下，每个 locale 必须存在规范文件名：{folderName}_{code}.i18n.json
    - 目录内出现 *.i18n.json 但不是规范命名的文件 -> 报 bad_name（不自动改名）
    """
    issues: List[DoctorIssue] = []

    module_dirs = list_module_dirs(cfg.i18n_dir)
    if not module_dirs:
        issues.append(DoctorIssue(
            kind="no_module_dirs",
            message=f"i18nDir 下未发现任何业务子目录：{cfg.i18n_dir}",
            path=cfg.i18n_dir,
        ))
        return issues

    locale_codes = all_locale_codes(cfg)

    for md in module_dirs:
        expected_names = {expected_i18n_filename(md, code) for code in locale_codes}

        # 1) 只检查 *.i18n.json（避免误伤其它 json）
        for fp in sorted(md.glob(f"*{I18N_FILE_SUFFIX}")):
            if fp.name not in expected_names:
                issues.append(DoctorIssue(
                    kind="bad_name",
                    message=(
                        f"文件名不符合规范：{fp.name}；应为 "
                        f"{to_lower_camel(md.name)}_{{code}}{I18N_FILE_SUFFIX}"
                    ),
                    path=fp,
                ))

        # 2) 检查缺失文件
        for code in locale_codes:
            expected = md / expected_i18n_filename(md, code)
            if not expected.exists():
                issues.append(DoctorIssue(
                    kind="missing",
                    message=f"缺少语言文件：{expected.name}",
                    path=expected,
                ))

    return issues


def sync_i18n_files(cfg: I18nConfig) -> int:
    """
    自动创建缺失的语言文件（仅处理 kind=missing）：
    - 文件名按规范：{folderName}_{code}.i18n.json
    - 内容最小化：只写 @@locale（并保证 @@locale 第一行）
    返回创建的文件数量
    """
    created = 0
    issues = check_i18n_naming_and_existence(cfg)

    for it in issues:
        if it.kind != "missing" or not it.path:
            continue

        p = it.path
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            continue

        name = p.name
        if not name.endswith(I18N_FILE_SUFFIX):
            code = cfg.source_locale.code
        else:
            base = name[: -len(I18N_FILE_SUFFIX)]  # 去掉 .i18n.json
            code = base.split("_")[-1] if "_" in base else cfg.source_locale.code

        obj: Dict[str, Any] = {LOCALE_META_KEY: code}
        obj = sort_json_keys(obj)  # 确保 @@locale 固定第一
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        created += 1

    return created


# ----------------------------
# 冗余字段检查：source_locale 没有，但其他语言有
# ----------------------------
@dataclass
class RedundantKeyIssue:
    file: Path
    keys: List[str]


def _source_file_for_module(cfg: I18nConfig, module_dir: Path) -> Path:
    return module_dir / expected_i18n_filename(module_dir, cfg.source_locale.code)


def check_redundant_keys(cfg: I18nConfig) -> List[RedundantKeyIssue]:
    """
    冗余字段定义：
    - 以 source_locale 文件为唯一真相源
    - 忽略所有 @@* 元字段
    - 其他语言文件中存在但 source 没有的 key -> 冗余
    """
    issues: List[RedundantKeyIssue] = []

    for md in list_module_dirs(cfg.i18n_dir):
        src_file = _source_file_for_module(cfg, md)
        if not src_file.exists():
            continue

        src_obj = read_json(src_file)
        src_keys = {k for k in src_obj.keys() if not is_meta_key(k)}

        for fp in sorted(md.glob(f"*{I18N_FILE_SUFFIX}")):
            if fp == src_file:
                continue

            obj = read_json(fp)
            extra = sorted([k for k in obj.keys() if (not is_meta_key(k)) and (k not in src_keys)])
            if extra:
                issues.append(RedundantKeyIssue(file=fp, keys=extra))

    return issues


def delete_redundant_keys(redundant: List[RedundantKeyIssue]) -> int:
    """
    删除冗余字段（不备份）
    返回影响文件数
    """
    affected = 0
    for it in redundant:
        fp = it.file
        if not fp.exists():
            continue

        obj = read_json(fp)
        for k in it.keys:
            obj.pop(k, None)

        obj = sort_json_keys(obj)
        fp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        affected += 1
    return affected


# ----------------------------
# actions
# ----------------------------
def run_sort(cfg: I18nConfig) -> None:
    """
    排序前必须先通过 doctor（doctor 允许交互修复）
    """
    if run_doctor(cfg) != 0:
        print("❌ sort 中止：doctor 检测未通过")
        return

    files = list_locale_files(cfg.i18n_dir)
    if not files:
        print(f"⚠️ 未找到任何 JSON 文件：{cfg.i18n_dir}")
        return

    changed = 0
    for fp in files:
        original = fp.read_text(encoding="utf-8")

        data_obj = read_json(fp)
        sorted_obj = sort_json_keys(data_obj)

        new_text = json.dumps(sorted_obj, ensure_ascii=False, indent=2) + "\n"
        if new_text != original:
            fp.write_text(new_text, encoding="utf-8")
            changed += 1

    print(f"✅ sort 完成：扫描 {len(files)} 个文件，改动 {changed} 个")


def run_check(cfg: I18nConfig) -> int:
    # 最小骨架：保证 JSON 可读且 flat（含 @@* 宽松规则）
    for fp in list_locale_files(cfg.i18n_dir):
        read_json(fp)
    print("✅ check（当前为最小骨架）通过")
    return 0


def run_clean(cfg: I18nConfig) -> None:
    # 最小骨架
    print("✅ clean（当前为最小骨架）完成")


def delete_bad_name_files(cfg: I18nConfig) -> int:
    """
    删除命名不规范的 *.i18n.json 文件（不备份）
    返回删除的文件数量。
    """
    deleted = 0
    issues = check_i18n_naming_and_existence(cfg)

    for it in issues:
        if it.kind != "bad_name" or not it.path:
            continue

        src = it.path
        if not src.exists() or not src.is_file():
            continue

        if not src.name.endswith(I18N_FILE_SUFFIX):
            continue

        try:
            src.unlink()
            print(f"✅ 删除：{src}")
            deleted += 1
        except Exception as e:
            print(f"⚠️ 删除失败：{src}，原因：{e}")

    return deleted


def _print_redundant_table(issues: List[RedundantKeyIssue]) -> None:
    """
    精简输出：文件名 + 冗余字段列表（逗号分隔）
    """
    print("\n❌ 检测到冗余字段（source_locale 中不存在）：")
    for it in issues:
        # 只显示文件名（不打很长的路径），需要路径就改为 str(it.file)
        keys_joined = ", ".join(it.keys)
        print(f"- {it.file.name}: {keys_joined}")


def run_doctor(cfg: I18nConfig) -> int:
    if not cfg.i18n_dir.exists():
        print(f"❌ i18nDir 不存在：{cfg.i18n_dir}")
        return 1

    # 1) 命名/缺失检查
    issues = check_i18n_naming_and_existence(cfg)

    if issues:
        for it in issues:
            where = f" ({it.path})" if it.path else ""
            print(f"❌ [{it.kind}] {it.message}{where}")

        # 缺失 -> sync
        if any(it.kind == "missing" for it in issues):
            try:
                ans = input("\n检测到缺失语言文件，是否执行 sync 自动创建？(y/N) ").strip().lower()
            except EOFError:
                ans = ""
            if ans in ("y", "yes"):
                created = sync_i18n_files(cfg)
                print(f"✅ sync 完成：创建 {created} 个缺失文件\n")

        # bad_name -> 删除（不重命名）
        issues2 = check_i18n_naming_and_existence(cfg)
        if any(it.kind == "bad_name" for it in issues2):
            try:
                ans = input("\n检测到不符合规范命名的语言文件，是否删除？(y/N) ").strip().lower()
            except EOFError:
                ans = ""
            if ans in ("y", "yes"):
                deleted = delete_bad_name_files(cfg)
                print(f"✅ delete 完成：删除 {deleted} 个文件\n")

    # 2) 冗余字段检查（source_locale 没有，其他语言有）
    redundant = check_redundant_keys(cfg)
    if redundant:
        _print_redundant_table(redundant)

        try:
            ans = input("\n是否删除这些冗余字段？(y/N) ").strip().lower()
        except EOFError:
            ans = ""

        if ans in ("y", "yes"):
            affected = delete_redundant_keys(redundant)
            print(f"✅ 冗余字段清理完成：影响 {affected} 个文件\n")

    # 3) 最终检查：命名/缺失 + 冗余
    final_issues = check_i18n_naming_and_existence(cfg)
    final_redundant = check_redundant_keys(cfg)

    if not final_issues and not final_redundant:
        print("✅ doctor 通过")
        return 0

    if final_issues:
        for it in final_issues:
            where = f" ({it.path})" if it.path else ""
            print(f"❌ [{it.kind}] {it.message}{where}")

    if final_redundant:
        print("\n❌ 仍存在冗余字段：")
        for it in final_redundant:
            print(f"- {it.file.name}: {len(it.keys)} 个")

    return 1
