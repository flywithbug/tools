项目结构

tools/
├── README.md
├── install.sh
├── publish.sh
├── pyproject.toml
├── scripts/
│   ├── README.md
│   ├── publish_tools.py
│   ├── temp/
│   │   ├── README.md
│   │   ├── __init__.py
│   │   └── tool.py
│   └── tree_tools.py
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
│       │   └── riverpod_gen/
│       │       ├── README.md
│       │       ├── __init__.py
│       │       └── tool.py
│       └── translate/
│           └── __init__.py
├── temp.md
└── tests/
├── conftest.py
├── pubspec.yaml
├── test_box_pub_publish.py
├── test_box_pub_upgrade.py
├── test_box_pub_version.py
└── test_box_riverpod_gen.py
