from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import yaml

# ----------------------------
# 常量 / 默认文件名
# ----------------------------
DEFAULT_TEMPLATE_NAME = "slang_i18n.yaml"   # 内置模板文件（带注释）
DEFAULT_LANGUAGES_NAME = "languages.json"  # 本地语言列表文件
LOCALE_META_KEY = "@@locale"               # i18n json 的 meta key（如果你后面要用到）


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
    i18n_dir: Path
    source_locale: Locale
    target_locales: List[Locale]
    openai_model: str
    max_workers: int
    prompts: Dict[str, object]
    options: Dict[str, object]


def override_i18n_dir(cfg: I18nConfig, i18n_dir: Path) -> I18nConfig:
    return I18nConfig(
        i18n_dir=i18n_dir,
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
        raise FileNotFoundError(f"内置默认 languages.json 不存在：{src}")

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
        raise ValueError("languages.json 顶层必须是数组")

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
    匹配规则：从 `target_locales:` 开始，吃掉其后的所有缩进行（列表项、注释、空行），直到遇到下一段顶层 key。
    """
    new_block = _yaml_block_for_target_locales(new_locales)

    # 方案：找到 target_locales 段开始位置，然后找到“下一个顶层 key”位置作为段落结束。
    # 顶层 key 简化匹配：行首非空白 + 某些字符 + 冒号
    start_match = re.search(r"(?m)^target_locales:\s*$", template_text)
    if not start_match:
        raise ValueError("模板中未找到 target_locales: 段落")

    start = start_match.start()

    # 从 start 往后找下一段顶层 key（排除 target_locales 自己）
    after = template_text[start_match.end():]
    next_key = re.search(r"(?m)^(?!target_locales:)[A-Za-z_][A-Za-z0-9_]*:\s*$", after)

    if next_key:
        end = start_match.end() + next_key.start()
    else:
        end = len(template_text)

    # 计算替换区间：从 start 行开始到 end
    # 但我们要替换的是整个 target_locales block，所以应从 start 行起替换到 end
    return template_text[:start] + new_block + template_text[end:]


# ----------------------------
# init：生成/校验配置，确保 languages.json 存在
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
        validate_config(raw_tpl)  # 模板自身也要合法

        source_code = raw_tpl["source_locale"]["code"]
        targets = load_target_locales_from_languages_json(languages_path, source_code)

        out_text = replace_target_locales_block(tpl_text, targets)
        cfg_path.write_text(out_text, encoding="utf-8")

    # 3) 无论是否新建，都做一次校验（不重写，保留用户注释/排版）
    assert_config_ok(cfg_path)


# ----------------------------
# 启动优先校验入口
# ----------------------------
def assert_config_ok(cfg_path: Path) -> Dict:
    """
    启动时优先校验：
    - 文件存在性
    - YAML 可解析
    - schema + 语义校验
    """
    if not cfg_path.exists():
        raise ConfigError(
            f"配置文件不存在：{cfg_path}\n"
            f"解决方法：运行 `slang_i18n init` 生成默认配置。"
        )

    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise ConfigError(
            f"配置文件无法解析为 YAML：{cfg_path}\n"
            f"原因：{e}\n"
            f"解决方法：修复 YAML 格式或运行 `slang_i18n init` 重新生成。"
        )

    try:
        validate_config(raw)
    except Exception as e:
        raise ConfigError(
            f"配置文件校验失败：{cfg_path}\n"
            f"原因：{e}\n"
            f"解决方法：修复配置字段/类型，或运行 `slang_i18n init` 重新生成。"
        )

    return raw


# ----------------------------
# load_config：把 raw dict 转成 I18nConfig
# ----------------------------
def load_config(cfg_path: Path) -> I18nConfig:
    raw = assert_config_ok(cfg_path)

    i18n_dir = Path(str(raw.get("i18nDir", "i18n")))

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
# validate_config：字段 + 类型 + 关键语义校验
# ----------------------------
def validate_config(raw: Dict) -> None:
    required_top = ["openAIModel", "maxWorkers", "source_locale", "target_locales", "prompts", "options"]
    for k in required_top:
        if k not in raw:
            raise ValueError(f"配置缺少字段：{k}")

    if not isinstance(raw["openAIModel"], str) or not raw["openAIModel"].strip():
        raise ValueError("openAIModel 必须是非空字符串")

    mw = raw["maxWorkers"]
    if not isinstance(mw, int) or mw <= 0:
        raise ValueError("maxWorkers 必须是正整数")

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

    # 语义：target code 唯一
    if len(set(codes)) != len(codes):
        raise ValueError("target_locales.code 存在重复，请去重")

    # 语义：target 不应包含 source
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
# 本地文件管理 / actions（目前给最小骨架，后续你可继续扩）
# ----------------------------
def list_locale_files(i18n_dir: Path) -> List[Path]:
    if not i18n_dir.exists():
        return []
    return sorted(i18n_dir.glob("**/*.json"))


def read_json(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data_obj: Dict) -> None:
    path.write_text(json.dumps(data_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def backup_file(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, bak)
    return bak


def run_sort(cfg: I18nConfig) -> None:
    # 先留空：你如果有 flat/@@locale/sort 规则，可在这里补齐
    # 这里给个最小可运行版本：不做变更，只遍历验证 JSON 可读
    for fp in list_locale_files(cfg.i18n_dir):
        read_json(fp)
    print("✅ sort（当前为最小骨架）完成")


def run_check(cfg: I18nConfig) -> int:
    # 最小骨架：你可替换成 PRD 的 extra keys 检查逻辑
    for fp in list_locale_files(cfg.i18n_dir):
        read_json(fp)
    print("✅ check（当前为最小骨架）通过")
    return 0


def run_clean(cfg: I18nConfig) -> None:
    # 最小骨架：你可替换成 PRD 的 clean + backup + sort
    print("✅ clean（当前为最小骨架）完成")


def run_doctor(cfg: I18nConfig) -> int:
    # 最小骨架：先检查目录存在
    if not cfg.i18n_dir.exists():
        print(f"❌ i18nDir 不存在：{cfg.i18n_dir}")
        return 1
    print("✅ doctor（当前为最小骨架）通过")
    return 0
