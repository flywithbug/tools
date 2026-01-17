import yaml
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

# 从 comm 目录导入 translate 模块
from .comm.translate import OpenAIModel, TranslationError, translate_flat_dict

# 工具元数据
BOX_TOOL = {
    "id": "flutter.strings_i18n",
    "name": "strings_i18n",
    "category": "flutter",
    "summary": "增量翻译和语言文件管理工具，支持排序、删除冗余字段等功能",
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

class StringsI18n:
    def __init__(self, config_path: str, languages_json: str, i18n_dir: str):
        self.config = self.load_config(config_path)
        self.languages = self.load_languages(languages_json)
        self.i18n_dir = Path(i18n_dir)

    def load_config(self, config_path: str) -> Dict:
        """加载配置文件"""
        if not Path(config_path).exists():
            raise FileNotFoundError(f"配置文件 {config_path} 不存在，请先使用 `strings_i18n init` 生成")
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)  # 确保以 YAML 格式加载配置
        return config

    def load_languages(self, languages_json: str) -> Dict:
        """加载语言文件，解析为字典"""
        with open(languages_json, 'r', encoding='utf-8') as f:
            languages = json.load(f)
        return languages

    def get_base_locale(self) -> str:
        """获取基础语言"""
        return self.config['base_locale']

    def get_base_roots(self) -> list:
        """获取 base 根路径"""
        return self.config['base_roots']

    def get_core_locales(self) -> list:
        """获取核心语言"""
        return self.config['core_locales']

    def get_target_locales(self) -> list:
        """获取目标语言"""
        # 从 languages.json 动态生成 target_locales
        target_locales = [lang['code'] for lang in self.languages]
        return target_locales

    def get_source_locale(self) -> str:
        """获取源语言"""
        return self.config['source_locale']

    def generate_strings_file(self, locale: str):
        """根据多语言生成 .strings 文件"""
        strings_root = self.config.get('strings_root', 'i18n')  # 获取翻译文件的根目录
        locale_file = Path(strings_root) / f"{locale}.lproj" / f"{locale}.strings"
        if not locale_file.exists():
            print(f"Creating {locale_file}...")
            locale_dir = locale_file.parent
            locale_dir.mkdir(parents=True, exist_ok=True)
            with open(locale_file, 'w', encoding='utf-8') as f:
                f.write(f"/* Localization for {locale} */\n")
        else:
            print(f"{locale_file} already exists.")

    def translate_key(self, key: str, source_locale: str, target_locale: str) -> str:
        """调用 translate.py 中的翻译功能"""
        print(f"Translating key '{key}' from {source_locale} to {target_locale}")
        prompt_en = self.config.get('prompt_en', '')  # 获取额外的提示词
        api_key = os.getenv('OPENAI_API_KEY')  # 获取 API key

        translation = translate_flat_dict(
            prompt_en=prompt_en,
            src_dict={key: key},
            src_lang=source_locale,
            tgt_locale=target_locale,
            model=OpenAIModel.GPT_4O.value,  # 使用默认的翻译模型
            api_key=api_key,
        )
        return translation.get(key, f"Translated-{key}")  # 默认返回翻译后的值

    def incremental_translate(self, source_locale: str, target_locale: str):
        """增量翻译：只翻译缺失的键"""
        strings_root = self.config.get('strings_root', 'i18n')  # 获取翻译文件的根目录
        base_locale = self.get_base_locale()

        # 核心语言增量翻译：使用 base 目录中的语言文件作为源语言
        if target_locale == base_locale:
            base_roots = self.get_base_roots()
            source_file = None
            for base_root in base_roots:
                base_root_path = Path(base_root)
                if base_root_path.exists():
                    source_file = base_root_path
                    break
            if not source_file:
                print(f"Error: Base root files not found!")
                return
        else:
            # 非核心语言增量翻译：使用 source_locale 目录中的语言文件作为源语言
            source_file = Path(strings_root) / f"{source_locale}.lproj" / f"{source_locale}.strings"

        target_file = Path(strings_root) / f"{target_locale}.lproj" / f"{target_locale}.strings"

        if not source_file.exists():
            print(f"Error: {source_file} does not exist!")
            return

        with open(source_file, 'r', encoding='utf-8') as sf:
            source_lines = sf.readlines()

        if not target_file.exists():
            print(f"Error: {target_file} does not exist!")
            return

        with open(target_file, 'r+', encoding='utf-8') as tf:
            target_lines = tf.readlines()

        target_dict = {line.split('=')[0].strip(): line.strip().split('=')[1].strip() for line in target_lines}

        # 增量翻译：只翻译在 target 中不存在的 keys
        for line in source_lines:
            key, value = line.split('=') if '=' in line else (None, None)
            if key and key.strip() not in target_dict:
                print(f"Adding missing key: {key.strip()}")
                translation = self.translate_key(key.strip(), source_locale, target_locale)
                target_lines.append(f"{key.strip()} = {translation};\n")

        # 将更新的内容写回目标文件
        with open(target_file, 'w', encoding='utf-8') as tf:
            tf.writelines(target_lines)

    def remove_redundant_fields(self):
        """删除冗余字段"""
        base_locale = self.get_base_locale()
        strings_root = self.config.get('strings_root', 'i18n')  # 获取翻译文件的根目录
        base_file = Path(strings_root) / f"{base_locale}.lproj" / f"{base_locale}.strings"

        if not base_file.exists():
            print(f"Error: {base_file} does not exist!")
            return

        with open(base_file, 'r', encoding='utf-8') as bf:
            base_lines = bf.readlines()

        # 获取 base 目录下的所有键
        base_keys = {line.split('=')[0].strip() for line in base_lines if '=' in line}

        for locale in self.get_target_locales():
            target_file = Path(strings_root) / f"{locale}.lproj" / f"{locale}.strings"
            if target_file.exists():
                with open(target_file, 'r', encoding='utf-8') as tf:
                    target_lines = tf.readlines()

                # 获取目标文件的所有键
                target_keys = {line.split('=')[0].strip() for line in target_lines if '=' in line}

                # 找出冗余的键
                redundant_keys = target_keys - base_keys

                if redundant_keys:
                    print(f"\n冗余字段在 {locale}.strings 文件中:")
                    for key in redundant_keys:
                        print(f"  {key}")
                    # 提示用户是否删除冗余字段
                    delete = input(f"是否删除冗余字段在 {locale}.strings 中的键？(y/n): ").strip().lower()
                    if delete == "y":
                        target_lines = [line for line in target_lines if line.split('=')[0].strip() not in redundant_keys]
                        with open(target_file, 'w', encoding='utf-8') as tf:
                            tf.writelines(target_lines)
                        print(f"冗余字段已从 {locale}.strings 中删除")
                else:
                    print(f"{locale}.strings 文件没有冗余字段")

    def sort_language_files(self):
        """排序语言文件"""
        print("Sorting language files...")

    def translate(self):
        """根据配置文件和语言信息进行翻译"""
        base_locale = self.get_base_locale()
        source_locale = self.get_source_locale()
        core_locales = self.get_core_locales()
        target_locales = self.get_target_locales()

        # 生成基础语言的 .strings 文件
        self.generate_strings_file(base_locale)

        # 对每个目标语言进行翻译操作
        for locale in target_locales:
            print(f"Translating {locale} using {source_locale} as source...")
            self.generate_strings_file(locale)
            # 增量翻译，填充缺失的键
            self.incremental_translate(source_locale, locale)

def create_config(config_path: str):
    """创建默认配置文件"""
    default_config = {
        "options": {
            "cleanup_extra_keys": True,
            "incremental_translate": True,
            "normalize_filenames": True,
            "sort_keys": True,
        },
        "prompt_en": "Translate the following text into the target language.",
        "base": "Base.lproj",  # 默认翻译源目录
        "base_locale": "zh_hans",  # 指定 base 目录下多语言的语言 code
        "source_locale": "en",  # 源语言
        "strings_root": "./TimeTrails/TimeTrails/TimeTrails/SupportFiles",  # 翻译文件保存的根目录
        "target_locales": [],  # 动态填充 target_locales
        "base_roots": [
            "./TimeTrails/TimeTrails/TimeTrails/SupportFiles/Base.lproj/Localizable.strings",
            "./TimeTrails/TimeTrails/TimeTrails/SupportFiles/Base.lproj/InfoPlist.strings"
        ],
        "core_locales": ["zh_Hans", "zh_Hant", "en", "ja", "ko", "yue"],
        "languages": "./languages.json"
    }
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(default_config, f, allow_unicode=True)
    print(f"Config file {config_path} created successfully!")

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

def main() -> int:
    config_path = "strings_i18n.yaml"
    languages_json = "languages.json"
    i18n_dir = "i18n"

    try:
        i18n = StringsI18n(config_path=config_path, languages_json=languages_json, i18n_dir=i18n_dir)
    except FileNotFoundError:
        print(f"配置文件 {config_path} 未找到。")
        print("正在创建配置文件...")
        create_config(config_path)
        i18n = StringsI18n(config_path=config_path, languages_json=languages_json, i18n_dir=i18n_dir)

    action = choose_action_interactive()

    if action == "exit":
        print("退出程序。")
        return 0
    elif action == "core_translation":
        print("进行核心语言增量翻译...")
        i18n.incremental_translate(i18n.get_source_locale(), i18n.get_base_locale())
    elif action == "non_core_translation":
        print("进行非核心语言增量翻译...")
        i18n.incremental_translate(i18n.get_source_locale(), i18n.get_base_locale())
    elif action == "remove_redundant_fields":
        i18n.remove_redundant_fields()
    elif action == "sort_language_files":
        i18n.sort_language_files()

    return 0

if __name__ == '__main__':
    main()
