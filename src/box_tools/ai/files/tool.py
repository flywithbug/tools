from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from box_tools._share.openai_translate.models import OpenAIModel
from box_tools._share.openai_translate.translate_pool import TranslateJob, translate_files


# BOX_TOOL = {
#     "id": "ai.box_ai_files",
#     "name": "box_ai_files",
#     "category": "ai",
#     "summary": (
#         "AI 多文件翻译测试工具：读取 YAML 配置，检查 i18nDir 与源语言文件，"
#         "并将 <source_locale.code>.json 翻译为 target_locales 下的 <code>.json（多文件并发）。"
#     ),
#     "usage": [
#         "box_ai_files",
#         "box_ai_files doctor",
#         "box_ai_files translate",
#         "box_ai_files --config slang_i18n.yaml",
#         "box_ai_files translate --max-workers 4",
#     ],
#     "options": [
#         {"flag": "--config <path>", "desc": "配置文件路径（默认 slang_i18n.yaml）"},
#         {"flag": "--max-workers <n>", "desc": "覆盖配置 maxWorkers（0=自动）"},
#     ],
#     "examples": [
#         {"cmd": "box_ai_files doctor", "desc": "检查 i18nDir 与源语言文件是否存在"},
#         {"cmd": "box_ai_files translate --max-workers 6", "desc": "以 6 并发翻译所有目标语言"},
#     ],
# }


# =========================
# Config
# =========================

@dataclass(frozen=True)
class LocaleSpec:
    code: str
    name_en: str


@dataclass(frozen=True)
class Config:
    openAIModel: str
    maxWorkers: int
    i18nDir: str
    source_locale: LocaleSpec
    target_locales: List[LocaleSpec]
    prompts_default_en: str
    prompts_by_locale_en: Dict[str, str]


def _read_yaml(path: Path) -> Dict[str, Any]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise FileNotFoundError(f"配置文件不存在：{path}")
    except Exception as e:
        raise RuntimeError(f"读取配置失败：{path} ({e})")


def _parse_config(d: Dict[str, Any]) -> Config:
    def req(key: str) -> Any:
        if key not in d:
            raise KeyError(f"配置缺少字段：{key}")
        return d[key]

    src = req("source_locale")
    targets = req("target_locales")

    prompts = d.get("prompts", {}) or {}
    default_en = prompts.get("default_en", "") or ""
    by_locale_en = prompts.get("by_locale_en", {}) or {}

    return Config(
        openAIModel=str(d.get("openAIModel", "gpt-4o")),
        maxWorkers=int(d.get("maxWorkers", 0)),
        i18nDir=str(req("i18nDir")),
        source_locale=LocaleSpec(code=str(src["code"]), name_en=str(src["name_en"])),
        target_locales=[LocaleSpec(code=str(x["code"]), name_en=str(x["name_en"])) for x in targets],
        prompts_default_en=str(default_en),
        prompts_by_locale_en={str(k): str(v) for k, v in by_locale_en.items()},
    )


def _auto_workers() -> int:
    cpu = os.cpu_count() or 4
    return max(2, min(8, cpu))


def _resolve_workers(cfg_workers: int, override: Optional[int], jobs_count: int) -> int:
    if override is not None:
        n = max(1, int(override))
    else:
        n = _auto_workers() if int(cfg_workers) == 0 else max(1, int(cfg_workers))
    return min(n, max(1, jobs_count))


def _compose_prompt(cfg: Config, tgt_code: str) -> Optional[str]:
    parts: List[str] = []
    if (cfg.prompts_default_en or "").strip():
        parts.append(cfg.prompts_default_en.strip())
    extra = cfg.prompts_by_locale_en.get(tgt_code, "")
    if (extra or "").strip():
        parts.append(extra.strip())
    if not parts:
        return None
    return "\n\n".join(parts).strip() + "\n"


# =========================
# Doctor checks
# =========================

def doctor(config_path: Path) -> int:
    try:
        cfg = _parse_config(_read_yaml(config_path))
    except Exception as e:
        print(f"❌ doctor: {e}")
        return 1

    root = Path.cwd()
    i18n_dir = root / cfg.i18nDir
    if not i18n_dir.exists() or not i18n_dir.is_dir():
        print(f"❌ doctor: i18nDir 不存在或不是目录：{i18n_dir}")
        return 1

    src_file = i18n_dir / f"{cfg.source_locale.code}.json"
    if not src_file.exists():
        print(f"❌ doctor: 源语言文件不存在：{src_file}")
        return 1

    print("✅ doctor: OK")
    print(f"  i18nDir: {i18n_dir}")
    print(f"  source: {cfg.source_locale.code}.json ({cfg.source_locale.name_en})")
    print(f"  targets: {len(cfg.target_locales)}")
    return 0


# =========================
# Translate
# =========================

def translate(config_path: Path, *, max_workers_override: Optional[int] = None) -> int:
    cfg = _parse_config(_read_yaml(config_path))

    root = Path.cwd()
    i18n_dir = root / cfg.i18nDir
    if not i18n_dir.exists() or not i18n_dir.is_dir():
        print(f"❌ i18nDir 不存在或不是目录：{i18n_dir}")
        return 1

    src_file = i18n_dir / f"{cfg.source_locale.code}.json"
    if not src_file.exists():
        print(f"❌ 源语言文件不存在：{src_file}")
        return 1

    # Create missing target files and build jobs
    jobs: List[TranslateJob] = []
    for loc in cfg.target_locales:
        tgt_file = i18n_dir / f"{loc.code}.json"
        if not tgt_file.exists():
            tgt_file.write_text("{}", encoding="utf-8")
        prompt = _compose_prompt(cfg, loc.code)
        jobs.append(
            TranslateJob(
                source_file_path=str(src_file),
                target_file_path=str(tgt_file),
                # IMPORTANT: use English language names for prompt stability
                src_locale=cfg.source_locale.name_en,
                tgt_locale=loc.name_en,
                prompt_en=prompt,
                name=f"{loc.code}.json",
                batch_size=40,
                pre_sort=True,
            )
        )

    if not jobs:
        print("ℹ️ 没有目标语言需要翻译。")
        return 0

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ 缺少 OPENAI_API_KEY 环境变量")
        return 1

    # model
    model: Optional[OpenAIModel | str]
    try:
        model = OpenAIModel(cfg.openAIModel)
    except Exception:
        model = cfg.openAIModel  # allow raw string

    max_workers = _resolve_workers(cfg.maxWorkers, max_workers_override, len(jobs))

    started = time.time()
    translate_files(
        jobs=jobs,
        api_key=api_key,
        model=model,
        max_workers=max_workers,
        pending_brief_lines=3,
        fail_fast=False,
    )
    elapsed = time.time() - started
    print(f"\n✅ 全部结束，总耗时：{elapsed:.1f}s")
    return 0


# =========================
# CLI
# =========================

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="box_ai_files", add_help=True)
    p.add_argument("--config", default="slang_i18n.yaml", help="配置文件路径（默认 slang_i18n.yaml）")
    p.add_argument("--max-workers", type=int, default=None, help="覆盖配置 maxWorkers（0=自动）")

    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("doctor", help="环境诊断（检查 i18nDir 与源语言文件）")
    sub.add_parser("translate", help="翻译（默认执行）")

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    config_path = Path(args.config)

    cmd = args.cmd or "translate"
    if cmd == "doctor":
        return doctor(config_path)
    if cmd == "translate":
        return translate(config_path, max_workers_override=args.max_workers)

    print(f"未知命令：{cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
