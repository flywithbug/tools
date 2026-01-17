import json
import os
import shutil
import sys
from pathlib import Path
from typing import List, Dict


# 读取配置文件
def load_config(file_path: str) -> Dict:
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

# 读取语言文件（JSON 格式）
def load_language_file(file_path: str) -> Dict[str, str]:
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

# 保存语言文件（JSON 格式）
def save_language_file(file_path: str, data: Dict[str, str]) -> None:
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# 获取语言文件路径
def get_locale_file_path(base_dir: str, locale: str) -> str:
    return os.path.join(base_dir, f"{locale}.json")

# 对所有翻译进行增量或全量翻译
def translate_all(base_locale: str, core_locales: List[str], all_locales: List[str], base_dir: str, full_translation: bool) -> None:
    # 读取源语言文件（即 base_locale）
    base_locale_file = get_locale_file_path(base_dir, base_locale)
    base_data = load_language_file(base_locale_file)

    for locale in all_locales:
        if locale == base_locale:
            continue  # 跳过源语言文件

        # 读取目标语言文件
        target_locale_file = get_locale_file_path(base_dir, locale)
        target_data = load_language_file(target_locale_file)

        # 如果是增量翻译，跳过已经存在的翻译
        if not full_translation:
            for key in list(target_data.keys()):
                if key in base_data:
                    del target_data[key]  # 删除已有的翻译

        # 这里加入翻译逻辑，增量翻译或全量翻译的内容（可以调用API进行翻译）
        # 假设为简化演示直接复制源语言的翻译到目标语言文件
        for key, value in base_data.items():
            if key not in target_data:
                target_data[key] = value  # 增量或全量翻译

        # 保存翻译结果
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

        # 检查冗余字段：如果字段在 base_locale 中没有，但在目标语言中有
        for key in list(locale_data.keys()):
            if key not in base_data:
                redundant_fields.append((locale, key, locale_data[key]))

    # 输出冗余字段，供判断是否删除
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
    print("1 - 增量翻译")
    print("2 - 全量翻译（核心语言）")
    print("3 - 全量翻译（非核心语言）")
    print("4 - 删除冗余字段")
    print("5 - 排序语言文件")
    print("0 - 退出")

    choice = input("请输入 0 / 1 / 2 / 3 / 4（或 q 退出）: ").strip().lower()

    if choice in ["0", "q", "quit", "exit"]:
        return "exit"

    if choice == "1":
        return "incremental_translate"
    if choice == "2":
        return "full_translate_core"
    if choice == "3":
        return "full_translate_non_core"
    if choice == "4":
        return "remove_redundant"
    if choice == "5":
        return "sort_files"

    return "invalid"

# 主函数
def main():
    config_file = "strings_i18n.yaml"
    if not os.path.exists(config_file):
        print("配置文件 strings_i18n.yaml 不存在！")
        sys.exit(1)

    config = load_config(config_file)
    base_locale = config.get('baseLocale', 'zh_hans')
    core_locales = config.get('coreLocales', ['en', 'zh_Hant', 'zh_Hans', 'ja', 'ko', 'yue'])
    all_locales = config.get('locales', core_locales)

    base_dir = "./locales"  # 假设翻译文件放在 ./locales 目录下

    while True:
        action = choose_action_interactive()

        if action == "exit":
            break

        if action == "incremental_translate":
            translate_all(base_locale, core_locales, all_locales, base_dir, full_translation=False)
        elif action == "full_translate_core":
            translate_all(base_locale, core_locales, all_locales, base_dir, full_translation=True)
        elif action == "full_translate_non_core":
            non_core_locales = [loc for loc in all_locales if loc not in core_locales]
            translate_all(base_locale, core_locales, non_core_locales, base_dir, full_translation=True)
        elif action == "remove_redundant":
            remove_redundant_fields(base_locale, core_locales, all_locales, base_dir)
        elif action == "sort_files":
            sort_language_files(base_dir, core_locales, all_locales)
        else:
            print("无效操作，请重新选择。")

if __name__ == "__main__":
    main()
