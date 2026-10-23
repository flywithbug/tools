项目结构
tools/
├── README.md
├── install.sh
├── publish.sh
├── pyproject.toml
├── scripts/
│   ├── README.md
│   ├── publish_tool.py
│   ├── temp/
│   │   ├── README.md
│   │   ├── __init__.py
│   │   └── tool.py
│   └── tree_tool.py
├── src/
│   ├── box/
│   │   ├── README.md
│   │   ├── __init__.py
│   │   └── tool.py
│   └── box_tools/
│       ├── __init__.py
│       ├── _share/
│       │   ├── __init__.py
│       │   └── openai_translate/
│       │       ├── __init__.py
│       │       ├── chat.py
│       │       ├── client.py
│       │       ├── models.py
│       │       └── translate.py
│       ├── ai/
│       │   ├── __init__.py
│       │   ├── chat/
│       │   │   ├── README.md
│       │   │   ├── __init__.py
│       │   │   └── tool.py
│       │   └── translate/
│       │       ├── README.md
│       │       ├── __init__.py
│       │       └── tool.py
│       ├── flutter/
│       │   ├── pub_publish/
│       │   │   ├── READEME.md
│       │   │   ├── __init__.py
│       │   │   └── tool.py
│       │   ├── pub_upgrade/
│       │   │   ├── README.md
│       │   │   ├── __init__.py
│       │   │   └── tool.py
│       │   ├── pub_version/
│       │   │   ├── README.md
│       │   │   ├── __init__.py
│       │   │   └── tool.py
│       │   ├── riverpod_gen/
│       │   │   ├── README.md
│       │   │   ├── __init__.py
│       │   │   └── tool.py
│       │   └── slang_i18n/
│       │       ├── PRD.md
│       │       ├── README.md
│       │       ├── __init__.py
│       │       ├── actions_core.py
│       │       ├── actions_translate.py
│       │       ├── config.py
│       │       ├── json_ops.py
│       │       ├── languages.json
│       │       ├── layout.py
│       │       ├── models.py
│       │       └── tool.py
│       └── iOS/
│           ├── __init__.py
│           └── strings_i18n/
│               ├── README.md
│               └── __init__.py
├── temp.md
├── tests/
│   ├── conftest.py
│   ├── pubspec.yaml
│   ├── test_ai_chat_tool.py
│   ├── test_box_ai_chat.py
│   ├── test_box_ai_translate.py
│   ├── test_box_pub_publish.py
│   ├── test_box_pub_upgrade.py
│   ├── test_box_pub_version.py
│   ├── test_box_riverpod_gen.py
│   ├── test_box_slang_i18n.py
│   └── test_openai_translate_translate.py
└── tree.sh