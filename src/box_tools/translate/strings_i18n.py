import json
import os
import shutil
import sys
import yaml
from pathlib import Path
from typing import List, Dict

# BOX_TOOL 元数据（工具元数据示例）
BOX_TOOL = {
    "id": "i18n.strings",           # 唯一标识（类别.工具名）
    "name": "strings_i18n",         # 工具名称
    "category": "i18n",             # 分类（可选）
    "summary": "iOS Xcode 多语言字符串工具：支持增量翻译、全量翻译、冗余字段删除及排序功能",  # 工具简介
    "usage": [
        "strings_i18n init",         # 初始化配置文件
        "strings_i18n translate",    # 执行翻译
        "strings_i18n sort",         # 排序语言文件
        "strings_i18n remove_redundant",  # 删除冗余字段
    ],
    "options": [
        {"flag": "--full-translation", "desc": "执行全量翻译"},
        {"flag": "--core-locales", "desc": "核心语言"},
        {"flag": "--non-core-locales", "desc": "非核心语言"},
    ],
    "examples": [
        {"cmd": "strings_i18n init", "desc": "生成配置文件 strings_i18n.yaml"},
        {"cmd": "strings_i18n translate --full-translation", "desc": "执行全量翻译"},
        {"cmd": "strings_i18n sort", "desc": "对所有语言文件进行排序"},
        {"cmd": "strings_i18n remove_redundant", "desc": "删除冗余字段"},
    ],
    "docs": "src/docs/strings_i18n.md",  # 文档路径（相对路径）
}

# 读取配置文件
def load_config(file_path: str) -> Dict:
    if not os.path.exists(file_path):
        print(f"配置文件 {file_path} 不存在！")
        print("请使用 `strings_i18n init` 初始化配置文件。")
        sys.exit(1)

    # 检查 YAML 文件是否合规
    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            config = yaml.safe_load(f)
            if not isinstance(config, dict):
                raise ValueError("配置文件内容格式错误！")
            return config
        except yaml.YAMLError as e:
            print(f"配置文件 {file_path} 格式错误！")
            print(f"错误详情: {e}")
            sys.exit(1)

# 生成配置文件
def init_config(base_dir: str) -> None:
    config_file = os.path.join(base_dir, 'strings_i18n.yaml')

    if os.path.exists(config_file):
        print(f"配置文件 {config_file} 已存在，正在检查文件格式…")
        try:
            config = load_config(config_file)  # 通过 load_config 加载配置文件，检查格式
            print(f"配置文件 {config_file} 格式正确。")
        except Exception as e:
            print(f"配置文件 {config_file} 格式错误！")
            print(f"错误详情: {e}")
            print("请手动修复配置文件格式或删除配置文件后重新运行 `strings_i18n init`。")
            sys.exit(1)
    else:
        print(f"配置文件 {config_file} 不存在，正在生成默认配置文件…")
        config = {
            "baseLocale": "zh_hans",
            "coreLocales": ["en", "zh_Hant", "zh_Hans", "ja", "ko", "yue"],
            "locales": ["en", "zh_Hant", "zh_Hans", "ja", "ko", "yue", "fr", "de"],
        }

        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
        print(f"配置文件 {config_file} 已生成。")


# 读取语言文件（.strings 格式）
def load_language_file(file_path: str) -> Dict[str, str]:
    strings_data = {}
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip().strip('"')
                value = value.strip().strip('"')
                strings_data[key] = value
    return strings_data

# 保存语言文件（.strings 格式）
def save_language_file(file_path: str, data: Dict[str, str]) -> None:
    with open(file_path, 'w', encoding='utf-8') as f:
        for key, value in data.items():
            f.write(f'"{key}" = "{value}";\n')

# 获取语言文件路径
def get_locale_file_path(base_dir: str, locale: str) -> str:
    return os.path.join(base_dir, f"{locale}.lproj", "Localizable.strings")

# 核心语言增量翻译
def translate_core(base_locale: str, core_locales: List[str], all_locales: List[str], base_dir: str, full_translation: bool) -> None:
    base_locale_file = get_locale_file_path(base_dir, base_locale)
    base_data = load_language_file(base_locale_file)

    for locale in core_locales:
        if locale == base_locale:
            continue

        target_locale_file = get_locale_file_path(base_dir, locale)
        target_data = load_language_file(target_locale_file)

        if not full_translation:
            for key in list(target_data.keys()):
                if key in base_data:
                    del target_data[key]

        for key, value in base_data.items():
            if key not in target_data:
                target_data[key] = value

        save_language_file(target_locale_file, target_data)

# 非核心语言增量翻译
def translate_non_core(base_locale: str, core_locales: List[str], all_locales: List[str], base_dir: str, full_translation: bool) -> None:
    non_core_locales = [loc for loc in all_locales if loc not in core_locales]

    for locale in non_core_locales:
        base_locale_file = get_locale_file_path(base_dir, base_locale)
        base_data = load_language_file(base_locale_file)

        target_locale_file = get_locale_file_path(base_dir, locale)
        target_data = load_language_file(target_locale_file)

        if not full_translation:
            for key in list(target_data.keys()):
                if key in base_data:
                    del target_data[key]

        for key, value in base_data.items():
            if key not in target_data:
                target_data[key] = value

        save_language_file(target_locale_file, target_data)

# 删除冗余字段：baseLocale中没有的字段，列出并判断是否删除
def remove_redundant_fields(base_locale: str, core_locales: List[str], all_locales: List[str], base_dir: str) -> None:
    base_locale_file = get_locale_file_path(base_dir, base_locale)
    base_data = load_language_file(base_locale_file)

    redundant_fields = []

    for locale in all_locales:
        if locale == base_locale:
            continue
        locale_file = get_locale_file_path(base_dir, locale)
        locale_data = load_language_file(locale_file)

        for key in list(locale_data.keys()):
            if key not in base_data:
                redundant_fields.append((locale, key, locale_data[key]))

    if redundant_fields:
        print("以下字段在源语言中缺失，但在其他语言中存在：")
        for locale, key, value in redundant_fields:
            print(f"语言: {locale}, 键: {key}, 值: {value}")
        user_input = input("是否删除这些冗余字段？输入 'y' 删除，'n' 保留: ").strip().lower()
        if user_input == 'y':
            for locale, key, value in redundant_fields:
                locale_file = get_locale_file_path(base_dir, locale)
                locale_data = load_language_file(locale_file)
                if key in locale_data:
                    del locale_data[key]
                    save_language_file(locale_file, locale_data)
                    print(f"已删除冗余字段: {key} (语言: {locale})")
        else:
            print("保留冗余字段。")
    else:
        print("未发现冗余字段。")

# 排序语言文件
def sort_language_files(base_dir: str, core_locales: List[str], all_locales: List[str]) -> None:
    for locale in all_locales:
        locale_file = get_locale_file_path(base_dir, locale)
        locale_data = load_language_file(locale_file)

        sorted_data = {key: locale_data[key] for key in sorted(locale_data.keys())}

        save_language_file(locale_file, sorted_data)
        print(f"已对 {locale} 语言文件进行排序")

# 删除指定目录及其内容
def delete_directory(directory_path: str) -> None:
    if os.path.exists(directory_path):
        shutil.rmtree(directory_path)
        print(f"已删除目录: {directory_path}")
    else:
        print(f"目录不存在: {directory_path}")

# 命令行交互式选择
def choose_action_interactive() -> str:
    print("请选择操作：")
    print("1 - 核心语言增量翻译")
    print("2 - 非核心语言增量翻译")
    print("3 - 删除冗余字段")
    print("4 - 排序语言文件")
    print("0 - 退出")

    choice = input("请输入 0 / 1 / 2 / 3 / 4（或 q 退出）: ").strip().lower()

    if choice in ["0", "q", "quit", "exit"]:
        return "exit"

    if choice == "1":
        return "incremental_translate_core"
    if choice == "2":
        return "incremental_translate_non_core"
    if choice == "3":
        return "remove_redundant"
    if choice == "4":
        return "sort_files"

    return "invalid"

# 生成配置文件
def init_config(base_dir: str) -> None:
    config_file = os.path.join(base_dir, 'strings_i18n.yaml')

    if os.path.exists(config_file):
        print(f"配置文件 {config_file} 已存在，正在检查文件格式…")
        config = load_config(config_file)
        print(f"配置文件 {config_file} 格式正确。")
    else:
        print(f"配置文件 {config_file} 不存在，正在生成默认配置文件…")
        config = {
            "baseLocale": "zh_hans",
            "coreLocales": ["en", "zh_Hant", "zh_Hans", "ja", "ko", "yue"],
            "locales": ["en", "zh_Hant", "zh_Hans", "ja", "ko", "yue", "fr", "de"],
        }

        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
        print(f"配置文件 {config_file} 已生成。")

# 主函数
def main():
    base_dir = os.getcwd()  # 默认使用当前工作目录
    config_file = os.path.join(base_dir, 'strings_i18n.yaml')

    if not os.path.exists(config_file):
        print("配置文件 strings_i18n.yaml 不存在！")
        print("请使用 `strings_i18n init` 初始化配置文件。")
        sys.exit(1)

    config = load_config(config_file)
    base_locale = config.get('baseLocale', 'zh_hans')
    core_locales = config.get('coreLocales', ['en', 'zh_Hant', 'zh_Hans', 'ja', 'ko', 'yue'])
    all_locales = config.get('locales', core_locales)

    while True:
        action = choose_action_interactive()

        if action == "exit":
            break

        if action == "incremental_translate_core":
            translate_core(base_locale, core_locales, all_locales, base_dir, full_translation=False)
        elif action == "incremental_translate_non_core":
            translate_non_core(base_locale, core_locales, all_locales, base_dir, full_translation=False)
        elif action == "remove_redundant":
            remove_redundant_fields(base_locale, core_locales, all_locales, base_dir)
        elif action == "sort_files":
            sort_language_files(base_dir, core_locales, all_locales)
        else:
            print("无效操作，请重新选择。")

if __name__ == "__main__":
    main()
