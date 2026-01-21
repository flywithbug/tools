from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

try:
    from openai import OpenAI  # noqa: F401
except Exception:
    OpenAI = None  # type: ignore

from .config import CONFIG_FILE, read_config
from .fs import I18N_DIR, ensure_i18n_dir, get_active_groups


EXIT_BAD = 2


def doctor(cfg_path: Path, api_key: Optional[str]) -> None:
    ok = True

    if OpenAI is None:
        ok = False
        print("❌ OpenAI SDK 不可用：pipx: pipx inject box 'openai>=1.0.0'")
    else:
        print("✅ OpenAI SDK OK")

    try:
        import yaml  # type: ignore
        _ = yaml
        print("✅ PyYAML OK")
    except Exception:
        ok = False
        print("❌ PyYAML 不可用：pipx: pipx inject box pyyaml")

    i18n_dir = Path.cwd() / I18N_DIR
    if not i18n_dir.exists() or not i18n_dir.is_dir():
        ok = False
        print("❌ 未找到 i18n/（请在项目根目录执行）")
    else:
        groups = get_active_groups(i18n_dir)
        # 不做太多推断，只提示当前策略
        if any(c.is_dir() for c in i18n_dir.iterdir()):
            print(f"✅ i18n/ OK（检测到子目录：仅处理 {len(groups)} 个模块目录）")
        else:
            print("✅ i18n/ OK（无子目录：处理根目录 json）")

    if not cfg_path.exists():
        ok = False
        print(f"❌ 未找到 {CONFIG_FILE}（请先 slang_i18n init）")
    else:
        try:
            cfg = read_config(cfg_path)
            print(
                f"✅ {CONFIG_FILE} OK (model={cfg.openai_model} source={cfg.source_locale.code}({cfg.source_locale.name_en}) "
                f"targets={len(cfg.target_locales)})"
            )
        except Exception as e:
            ok = False
            print(f"❌ {CONFIG_FILE} 解析失败：{e}")

    ak = api_key or os.getenv("OPENAI_API_KEY")
    if not ak:
        print("⚠️ 未提供 API Key：--api-key 或环境变量 OPENAI_API_KEY（翻译时需要）")
        print("   macOS/Linux: export OPENAI_API_KEY=\"sk-...\"")
        print("   Windows(PowerShell): setx OPENAI_API_KEY \"sk-...\"")
    else:
        print("✅ API Key 已配置（来源：参数或环境变量）")

    if not ok:
        raise SystemExit(EXIT_BAD)
    print("✅ doctor 完成")
