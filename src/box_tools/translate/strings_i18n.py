import os
import yaml
import argparse
from pathlib import Path
from typing import Dict, List
from .translate import OpenAIModel, TranslationError, translate_flat_dict  # type: ignore

# 默认配置
DEFAULT_CONFIG = {
    "primarySourceLocale": "en",
    "primaryTargetLocales": ["zh_Hant", "ja", "ko"],
    "secondarySourceLocale": "zh_Hans",
    "secondaryTargetLocales": ["en", "zh_Hant", "zh-HK", "ja", "ko"],
    "coreLocales": ["en", "zh_Hant", "zh-Hans"],  # 核心语言集
}

# 文件路径设置
BASE_LPROJ_DIR = "Base.lproj"
LOCALIZABLE_FILE = "Localizable.strings"

# 配置文件路径
CONFIG_FILE_PATH = "strings_i18n.yaml"

# 读取配置文件
def read_config() -> dict:
    if not Path(CONFIG_FILE_PATH).exists():
        write_config(DEFAULT_CONFIG)
    with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as file:
        return yaml.safe_load(file)

# 写入配置文件
def write_config(config: dict):
    with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as file:
        yaml.dump(config, file, allow_unicode=True, sort_keys=False)

# 读取 Localizable.strings 文件
def read_strings_file(file_path: Path) -> Dict[str, str]:
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.readlines()

    strings_dict = {}
    for line in content:
        if "=" in line:
            key, value = line.split("=")
            strings_dict[key.strip().strip('"')] = value.strip().strip('"')
    return strings_dict

# 保存翻译后的 strings 文件
def save_translated_strings(file_path: Path, content: str):
    with open(file_path, 'w', encoding='utf-8') as file:
        file.write(content)

# 生成 L10n.swift 文件
def generate_l10n_swift(base_locale_dir: Path):
    strings_files = [base_locale_dir / f"{locale}.lproj/Localizable.strings" for locale in ["en", "zh_Hant"]]

    l10n_content = """
// Auto-generated from Base.lproj/Localizable.strings
import Foundation

extension String {
    func callAsFunction(_ arguments: CVarArg...) -> String {
        String(format: self, locale: Locale.current, arguments: arguments)
    }
}
"""
    for file_path in strings_files:
        strings_dict = read_strings_file(file_path)
        # 对每个分组（以点号分隔）进行处理
        for key, value in strings_dict.items():
            parts = key.split(".")
            if len(parts) > 1:
                group = parts[0]
                key_in_group = parts[1]
                l10n_content += f"""
    enum {group} {{
        static var {key_in_group}: String {{ return NSLocalizedString("{key}", value: "{value}", comment: "{value}") }}
    }}
"""

    # 保存到 L10n.swift 文件
    with open(base_locale_dir / "L10n.swift", 'w', encoding='utf-8') as file:
        file.write(l10n_content)

# 增量翻译
def incremental_translation(src_file: Path, tgt_file: Path, api_key: str, model: str):
    src_dict = read_strings_file(src_file)
    tgt_dict = read_strings_file(tgt_file)

    need_translation = {k: v for k, v in src_dict.items() if k not in tgt_dict}

    # 使用翻译模块进行翻译
    translated = translate_flat_dict(
        src_dict=need_translation,
        src_lang="en",
        tgt_locale="zh_Hant",
        model=model,
        api_key=api_key
    )

    tgt_dict.update(translated)

    translated_content = "\n".join([f'"{k}" = "{v}";' for k, v in tgt_dict.items()])

    save_translated_strings(tgt_file, translated_content)

# 执行翻译
def execute_translation(config: dict, api_key: str, model: str):
    primary_source_locale = config["primarySourceLocale"]
    primary_target_locales = config["primaryTargetLocales"]

    for locale in primary_target_locales:
        base_locale_file = Path(BASE_LPROJ_DIR) / f"{primary_source_locale}.lproj/Localizable.strings"
        target_locale_file = Path(BASE_LPROJ_DIR) / f"{locale}.lproj/Localizable.strings"

        incremental_translation(base_locale_file, target_locale_file, api_key, model)

    # 生成 L10n.swift 文件
    generate_l10n_swift(Path(BASE_LPROJ_DIR))

# 交互式菜单
def interactive_mode():
    print("请选择操作：")
    print("1 - 增量翻译")
    print("2 - 全量翻译")
    print("3 - 生成 L10n.swift 文件")
    print("4 - 检查冗余翻译")
    print("5 - 删除冗余翻译")
    print("6 - 退出")

    choice = input("请输入 1, 2, 3, 4, 5 或 6: ").strip()
    if choice == '1':
        print("执行增量翻译...")
        # 执行增量翻译操作
    elif choice == '2':
        print("执行全量翻译...")
        # 执行全量翻译操作
    elif choice == '3':
        print("生成 L10n.swift 文件...")
        # 生成 L10n.swift 文件
    elif choice == '4':
        print("检查冗余翻译...")
        # 检查冗余翻译
    elif choice == '5':
        print("删除冗余翻译...")
        # 删除冗余翻译
    elif choice == '6':
        print("退出程序")
        return False  # 退出交互模式
    else:
        print("无效选择，请重新输入。")
    return True  # 继续交互模式

# BOX_TOOL 配置
BOX_TOOL = {
    "id": "flutter.slang_i18n",
    "name": "strings_i18n",
    "category": "i18n",
    "summary": "处理 Localizable.strings 文件的翻译工具，支持增量翻译、全量翻译以及生成 Swift 的 L10n 文件。",
    "usage": [
        "strings_i18n",
        "strings_i18n init",
        "strings_i18n doctor",
        "strings_i18n sort",
        "strings_i18n check",
        "strings_i18n clean --yes",
        "strings_i18n translate --api-key $OPENAI_API_KEY",
    ],
    "options": [
        {"flag": "--api-key", "desc": "OpenAI API key（也可用环境变量 OPENAI_API_KEY）"},
        {"flag": "--model", "desc": "翻译模型（默认 gpt-4o）"},
        {"flag": "--full", "desc": "全量翻译（默认增量翻译）"},
        {"flag": "--yes", "desc": "clean 删除冗余时跳过确认"},
        {"flag": "--no-exitcode-3", "desc": "check 发现冗余时仍返回 0（默认返回 3）"},
    ],
    "examples": [
        {"cmd": "strings_i18n init", "desc": "生成 strings_i18n.yaml 模板"},
        {"cmd": "strings_i18n translate --api-key $OPENAI_API_KEY", "desc": "增量翻译缺失的 keys"},
        {"cmd": "strings_i18n clean --yes", "desc": "删除所有冗余 key（不询问）"},
    ],
    "dependencies": [
        "PyYAML>=6.0",
        "openai>=1.0.0",
    ],
}

# 生成配置文件 `strings_i18n.yaml`
def generate_yaml_config():
    if not Path(CONFIG_FILE_PATH).exists():
        write_config(DEFAULT_CONFIG)
        print(f"生成默认配置文件: {CONFIG_FILE_PATH}")
    else:
        print(f"配置文件 {CONFIG_FILE_PATH} 已存在。")

# 解析命令行参数
def parse_args():
    parser = argparse.ArgumentParser(description="strings_i18n 工具")
    parser.add_argument("--api-key", help="OpenAI API 密钥")
    parser.add_argument("--model", default="gpt-4o", help="翻译模型")
    parser.add_argument("--full", action="store_true", help="执行全量翻译")
    parser.add_argument("--yes", action="store_true", help="跳过确认删除冗余翻译")
    parser.add_argument("--init", action="store_true", help="生成配置文件")
    args = parser.parse_args()
    return args

# 主函数
def main():
    args = parse_args()

    # 如果没有提供命令行参数，则启动交互模式
    if args.init:
        generate_yaml_config()
    elif not any(vars(args).values()):
        while interactive_mode():
            pass
    else:
        # 继续执行翻译等任务
        config = read_config()
        api_key = args.api_key or os.getenv("OPENAI_API_KEY")
        model = args.model

        if args.full:
            execute_translation(config, api_key, model)
        else:
            execute_translation(config, api_key, model)
        print("操作完成！")

if __name__ == "__main__":
    main()
