from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from box_tools._share.openai_translate.models import OpenAIModel
from .model import Locale, Options, Prompts, ProjectConfig

CONFIG_FILE = "slang_i18n.yaml"

ALLOWED_OPENAI_MODELS = (
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
)


def _require_yaml():
    try:
        import yaml  # type: ignore
        return yaml
    except Exception:
        raise SystemExit(
            "❌ 缺少依赖 PyYAML（import yaml 失败）\n"
            "修复方式：\n"
            "1) pipx 安装：pipx inject box pyyaml\n"
            "2) 或在 pyproject.toml dependencies 加入 PyYAML>=6.0 后重新发布/安装\n"
        )


def _schema_error(msg: str) -> ValueError:
    return ValueError(
        "slang_i18n.yaml 格式错误：\n"
        f"- {msg}\n\n"
        "期望结构（新 schema）示例：\n"
        "openAIModel: gpt-4o\n"
        "source_locale:\n"
        "  code: en\n"
        "  name_en: English\n"
        "target_locales:\n"
        "  - code: zh_Hant\n"
        "    name_en: Traditional Chinese\n"
        "prompts:\n"
        "  default_en: |\n"
        "    Translate UI strings naturally.\n"
        "  by_locale_en:\n"
        "    zh_Hant: |\n"
        "      Use Taiwan Traditional Chinese UI style.\n"
        "options:\n"
        "  sort_keys: true\n"
        "  cleanup_extra_keys: true\n"
        "  incremental_translate: true\n"
        "  normalize_filenames: true\n"
    )


def _need_nonempty_str(obj: Dict[str, Any], key: str, path: str) -> str:
    v = obj.get(key)
    if not isinstance(v, str) or not v.strip():
        raise _schema_error(f"{path}.{key} 必须是非空字符串")
    return v.strip()


def _need_bool(obj: Dict[str, Any], key: str, path: str) -> bool:
    v = obj.get(key)
    if not isinstance(v, bool):
        raise _schema_error(f"{path}.{key} 必须是 bool（true/false）")
    return v


def _need_openai_model(cfg: Dict[str, Any]) -> str:
    v = cfg.get("openAIModel", OpenAIModel.GPT_4O.value)
    if v is None:
        v = OpenAIModel.GPT_4O.value
    if not isinstance(v, str) or not v.strip():
        raise _schema_error("openAIModel 必须是非空字符串")
    v = v.strip()
    if v not in set(ALLOWED_OPENAI_MODELS):
        raise _schema_error(f"openAIModel 不合法：{v!r}，可选：{', '.join(ALLOWED_OPENAI_MODELS)}")
    return v


def validate_config(raw: Any) -> ProjectConfig:
    if not isinstance(raw, dict):
        raise _schema_error("根节点必须是 YAML object/map")

    openai_model = _need_openai_model(raw)

    src_raw = raw.get("source_locale")
    if not isinstance(src_raw, dict):
        raise _schema_error("source_locale 必须是 object/map（包含 code / name_en）")
    src = Locale(
        code=_need_nonempty_str(src_raw, "code", "source_locale"),
        name_en=_need_nonempty_str(src_raw, "name_en", "source_locale"),
    )

    targets_raw = raw.get("target_locales")
    if not isinstance(targets_raw, list) or not targets_raw:
        raise _schema_error("target_locales 必须是非空数组（每项为 {code, name_en}）")

    seen: set[str] = set()
    targets: List[Locale] = []
    for i, it in enumerate(targets_raw):
        if not isinstance(it, dict):
            raise _schema_error(f"target_locales[{i}] 必须是 object/map（包含 code / name_en）")
        code = _need_nonempty_str(it, "code", f"target_locales[{i}]")
        name_en = _need_nonempty_str(it, "name_en", f"target_locales[{i}]")
        if code == src.code:
            raise _schema_error(f"target_locales[{i}].code 不应等于 source_locale.code（{src.code}）")
        if code in seen:
            raise _schema_error(f"target_locales[{i}].code 重复：{code}")
        seen.add(code)
        targets.append(Locale(code=code, name_en=name_en))

    prompts_raw = raw.get("prompts") or {}
    if not isinstance(prompts_raw, dict):
        raise _schema_error("prompts 必须是 object/map（可省略）")
    default_en = prompts_raw.get("default_en") or ""
    if not isinstance(default_en, str):
        raise _schema_error("prompts.default_en 必须是字符串（可为空）")
    by_locale_raw = prompts_raw.get("by_locale_en") or {}
    if not isinstance(by_locale_raw, dict):
        raise _schema_error("prompts.by_locale_en 必须是 object/map（可省略）")
    by_locale: Dict[str, str] = {}
    for k, v in by_locale_raw.items():
        if not isinstance(k, str) or not k.strip():
            raise _schema_error("prompts.by_locale_en 的 key 必须是非空字符串（locale code）")
        if not isinstance(v, str):
            raise _schema_error(f"prompts.by_locale_en[{k!r}] 必须是字符串")
        by_locale[k.strip()] = v
    prompts = Prompts(default_en=default_en, by_locale_en=by_locale)

    opts_raw = raw.get("options")
    if not isinstance(opts_raw, dict):
        raise _schema_error("options 必须是 object/map")
    normalize_filenames = opts_raw.get("normalize_filenames", True)
    if not isinstance(normalize_filenames, bool):
        raise _schema_error("options.normalize_filenames 必须是 bool（true/false）")

    options = Options(
        sort_keys=_need_bool(opts_raw, "sort_keys", "options"),
        cleanup_extra_keys=_need_bool(opts_raw, "cleanup_extra_keys", "options"),
        incremental_translate=_need_bool(opts_raw, "incremental_translate", "options"),
        normalize_filenames=normalize_filenames,
    )

    return ProjectConfig(
        openai_model=openai_model,
        source_locale=src,
        target_locales=targets,
        prompts=prompts,
        options=options,
    )


def read_config(path: Path) -> ProjectConfig:
    yaml = _require_yaml()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return validate_config(raw)


def read_config_or_throw(path: Path) -> ProjectConfig:
    if not path.exists():
        raise FileNotFoundError(f"❌ 未找到 {CONFIG_FILE}（请先 slang_i18n init）")
    return read_config(path)


def _config_template_text() -> str:
    # 直接沿用你现有脚本的模板（保留注释），避免用户迁移成本:contentReference[oaicite:1]{index=1}
    from textwrap import dedent
    return dedent(
        """\
        # slang_i18n.yaml
        # Flutter slang i18n 配置（NEW schema）
        #
        # 目录约定：
        # - 在项目根目录执行
        # - i18n/ 目录存在
        # - 若 i18n/ 下存在子目录：只处理子目录中的 *.i18n.json（视为模块）
        # - 若 i18n/ 下无子目录：处理 i18n/ 根目录中的 *.i18n.json

        # OpenAI 模型（默认 gpt-4o）
        # 可选值（枚举）：
        # - gpt-4o
        # - gpt-4o-mini
        # - gpt-4.1
        # - gpt-4.1-mini
        openAIModel: gpt-4o

        # 源语言（结构化：code + 英文语言名）
        source_locale:
          code: en
          name_en: English

        # 目标语言列表：每项包含 code + 英文语言名
        target_locales:
          - code: zh_Hant
            name_en: Traditional Chinese
          - code: ja
            name_en: Japanese
          - code: ko
            name_en: Korean
          - code: fr
            name_en: French

        prompts:
          default_en: |
            Translate UI strings naturally for a mobile app.
            Be concise, clear, and consistent.

          by_locale_en:
            zh_Hant: |
              Use Taiwan Traditional Chinese UI style.

        options:
          sort_keys: true
          cleanup_extra_keys: true
          incremental_translate: true
          normalize_filenames: true
        """
    )


def init_config(path: Path) -> None:
    _require_yaml()
    if path.exists():
        _ = read_config(path)  # 存在就校验，不覆盖
        print(f"✅ {CONFIG_FILE} 已存在且格式正确（不会覆盖）")
        return
    path.write_text(_config_template_text(), encoding="utf-8")
    print(f"📝 已生成 {CONFIG_FILE}（新 schema，含注释）")
