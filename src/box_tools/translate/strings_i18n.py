import json
import os
import yaml
from pathlib import Path
from typing import Dict, List
import openai  # 假设使用 OpenAI API 进行翻译

# BOX_TOOL 信息，便于 box 工具集的管理
BOX_TOOL = {
    "id": "flutter.strings_i18n",
    "name": "strings_i18n",
    "category": "flutter",
    "summary": "iOS 项目多语言翻译工具，支持增量翻译、冗余检查、排序等功能",
    "usage": [
        "strings_i18n",
        "strings_i18n init",  # 初始化配置
        "strings_i18n doctor",  # 环境自检
        "strings_i18n sort",  # 排序
        "strings_i18n check",  # 冗余检查
        "strings_i18n clean --yes",  # 清理冗余
        "strings_i18n translate --api-key $OPENAI_API_KEY",  # 翻译命令
    ],
    "options": [
        {"flag": "--api-key", "desc": "OpenAI API key（可通过环境变量传递）"},
        {"flag": "--model", "desc": "指定翻译模型（默认为 gpt-4o）"},
        {"flag": "--full", "desc": "全量翻译（默认增量翻译）"},
        {"flag": "--yes", "desc": "clean 删除冗余时跳过确认"},
        {"flag": "--no-exitcode-3", "desc": "check 发现冗余时仍返回 0（默认返回 3）"},
    ],
    "examples": [
        {"cmd": "strings_i18n init", "desc": "生成 strings_i18n.yaml 配置文件"},
        {"cmd": "strings_i18n translate --api-key $OPENAI_API_KEY", "desc": "增量翻译缺失的 keys"},
        {"cmd": "strings_i18n clean --yes", "desc": "删除所有冗余 key（不询问）"},
    ],
    "dependencies": [
        "PyYAML>=6.0",  # 依赖 PyYAML
        "openai>=1.0.0",  # 依赖 OpenAI
    ],
}

# 读取配置文件
def load_config(config_path: str) -> Dict:
    if not Path(config_path).exists():
        raise FileNotFoundError(f"配置文件 {config_path} 不存在，请先使用 `strings_i18n init` 生成")
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config

# 生成 strings_i18n.yaml 配置模板
def create_config(config_path: str):
    default_config = {
        "source_locale": "en",
        "base_locale": "zh_hans",
        "base_roots": [
            "./TimeTrails/TimeTrails/TimeTrails/SupportFiles/Base.lproj/Localizable.strings",
            "./TimeTrails/TimeTrails/TimeTrails/SupportFiles/Base.lproj/InfoPlist.strings"
        ],
        "core_locales": ["zh_Hans", "zh_Hant", "en", "ja", "ko", "yue"],
        "languages": "./languages.json",
        "prompt_en": "Translate the following text into the target language.",
        "lang_root": "./TimeTrails/TimeTrails/TimeTrails/SupportFiles/",  # 用于生成目录
        "base_folder": "Base.lproj",
        "lang_files": [
            "Localizable.strings",
            "InfoPlist.strings"
        ],
        "options": {
            "sort_keys": True,
            "cleanup_extra_keys": True,
            "incremental_translate": True,
            "normalize_filenames": True,
        },
    }
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(default_config, f, allow_unicode=True, sort_keys=False)
    print(f"已生成配置文件：{config_path}")

# 加载语言文件
def load_languages(languages_json: str) -> Dict:
    with open(languages_json, 'r', encoding='utf-8') as f:
        languages = json.load(f)
    return languages

# 生成 .strings 文件
def generate_strings_file(locale: str, lang_root: str, lang_files: List[str]):
    locale_dir = Path(lang_root) / f"{locale}.lproj"
    if not locale_dir.exists():
        print(f"Creating directory {locale_dir}...")
        locale_dir.mkdir(parents=True, exist_ok=True)

    for lang_file in lang_files:
        locale_file = locale_dir / lang_file
        if not locale_file.exists():
            print(f"Creating {locale_file}...")
            with open(locale_file, 'w', encoding='utf-8') as f:
                f.write(f"/* Localization for {locale} */\n")
        else:
            print(f"{locale_file} already exists.")

# 翻译单个键
def translate_key(key: str, source_locale: str, target_locale: str, prompt_en: str, api_key: str) -> str:
    # 调用 OpenAI API 进行翻译
    translation = openai.Completion.create(
        model="gpt-4",  # 可以根据需求选择不同的模型
        prompt=f"{prompt_en}\n{key}",
        max_tokens=500,
        temperature=0.5,
        api_key=api_key
    )
    return translation['choices'][0]['text'].strip()

# 执行增量翻译
def incremental_translate(config: Dict, i18n_dir: Path, api_key: str):
    source_locale = config['source_locale']
    target_locales = [lang['code'] for lang in load_languages(config['languages']) if lang['code'] not in config['core_locales']]
    base_locale = config['base_locale']
    prompt_en = config['prompt_en']
    lang_root = config.get('lang_root', './TimeTrails/TimeTrails/TimeTrails/SupportFiles/')
    lang_files = config['lang_files']

    for locale in target_locales:
        print(f"正在翻译 {locale}...")
        generate_strings_file(locale, lang_root, lang_files)
        source_file = Path(lang_root) / f"{source_locale}.lproj" / f"{source_locale}.strings"
        target_file = Path(lang_root) / f"{locale}.lproj" / f"{locale}.strings"

        if not source_file.exists():
            print(f"源文件 {source_file} 不存在！跳过 {locale}")
            continue

        with open(source_file, 'r', encoding='utf-8') as f:
            source_lines = f.readlines()

        target_dict = {}
        if target_file.exists():
            with open(target_file, 'r', encoding='utf-8') as f:
                target_lines = f.readlines()
                target_dict = {line.split('=')[0].strip(): line.strip().split('=')[1].strip() for line in target_lines}

        # 增量翻译：仅翻译缺失的 key
        for line in source_lines:
            key, value = line.split('=') if '=' in line else (None, None)
            if key and key.strip() not in target_dict:
                print(f"添加缺失的键：{key.strip()}")
                translation = translate_key(key.strip(), source_locale, locale, prompt_en, api_key)
                target_lines.append(f"{key.strip()} = {translation};\n")

        # 将更新后的内容写回目标文件
        with open(target_file, 'w', encoding='utf-8') as f:
            f.writelines(target_lines)

# 删除冗余字段
def remove_redundant_fields(config: Dict, i18n_dir: Path):
    source_locale = config['source_locale']
    lang_root = config.get('lang_root', './TimeTrails/TimeTrails/TimeTrails/SupportFiles/')
    base_file = Path(lang_root) / f"{source_locale}.lproj" / f"{source_locale}.strings"

    if not base_file.exists():
        print(f"基础语言文件 {base_file} 不存在！")
        return

    with open(base_file, 'r', encoding='utf-8') as f:
        base_lines = f.readlines()

    base_keys = {line.split('=')[0].strip() for line in base_lines if '=' in line}

    for locale in os.listdir(i18n_dir):
        target_file = Path(i18n_dir) / f"{locale}.lproj" / f"{locale}.strings"
        if target_file.exists():
            with open(target_file, 'r', encoding='utf-8') as f:
                target_lines = f.readlines()

            target_keys = {line.split('=')[0].strip() for line in target_lines if '=' in line}
            redundant_keys = target_keys - base_keys

            if redundant_keys:
                print(f"冗余字段在 {locale}.strings 文件中:")
                for key in redundant_keys:
                    print(f"  {key}")
                delete = input(f"是否删除冗余字段在 {locale}.strings 中的键？(y/n): ").strip().lower()
                if delete == "y":
                    target_lines = [line for line in target_lines if line.split('=')[0].strip() not in redundant_keys]
                    with open(target_file, 'w', encoding='utf-8') as f:
                        f.writelines(target_lines)
                    print(f"冗余字段已从 {locale}.strings 中删除")
                else:
                    print(f"跳过删除 {locale}.strings 文件中的冗余字段")

# 排序语言文件
def sort_language_files(i18n_dir: Path):
    print(f"对语言文件进行排序：{i18n_dir}")
    for locale_dir in Path(i18n_dir).glob("*.lproj"):
        strings_file = locale_dir / f"{locale_dir.name}.strings"
        if strings_file.exists():
            with open(strings_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            lines.sort()
            with open(strings_file, 'w', encoding='utf-8') as f:
                f.writelines(lines)

# 交互式选择操作
def choose_action_interactive() -> str:
    print("请选择操作：")
    print("1 - 核心语言增量翻译")
    print("2 - 非核心语言增量翻译")
    print("3 - 删除冗余字段")
    print("4 - 排序语言文件")
    print("0 - 退出")

    choice = input("请输入 0 / 1 / 2 / 3 / 4（或 q 退出）: ").strip().lower()

    if choice == "0" or choice == "q":
        return "exit"
    if choice == "1":
        return "core_translation"
    if choice == "2":
        return "non_core_translation"
    if choice == "3":
        return "remove_redundant_fields"
    if choice == "4":
        return "sort_language_files"

    print("无效的输入，请重新选择。")
    return choose_action_interactive()

# 主函数，驱动程序逻辑
def main() -> int:
    config_path = "strings_i18n.yaml"
    config = load_config(config_path)
    action = choose_action_interactive()

    if action == "exit":
        print("退出程序。")
        return 0

    # 根据选择的操作执行不同功能
    if action == "core_translation":
        incremental_translate(config, Path("./i18n"), api_key="YOUR_OPENAI_API_KEY")
    elif action == "non_core_translation":
        incremental_translate(config, Path("./i18n"), api_key="YOUR_OPENAI_API_KEY")
    elif action == "remove_redundant_fields":
        remove_redundant_fields(config, Path("./i18n"))
    elif action == "sort_language_files":
        sort_language_files(Path("./i18n"))

    return 0

if __name__ == "__main__":
    main()
