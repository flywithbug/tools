# box_riverpod_gen

用于快速生成 **Riverpod StateNotifier + State** 模板文件的命令行工具，适合在 Flutter 项目中统一状态管理代码结构，减少样板代码的重复劳动。

该工具会一次性生成：

- `*_notifier.dart`：StateNotifier + Provider 定义
- `*_state.c.dart`：State 数据类（可选 `copy_with_extension`）

---

## 功能特性

- 支持多种类名输入形式  
  `Product` / `product` / `product_item` / `ProductItem`
- 自动规范化为 Dart 推荐命名风格
- 默认生成 `StateNotifierProvider.autoDispose`
- 支持 legacy / modern 两种 Riverpod import
- 可选生成 `copy_with_extension` + `*.g.dart`
- 已存在文件时可安全阻止覆盖（或使用 `--force`）

---

## 使用方式

```bash
box_riverpod_gen [ClassName] [options]
```

### 示例

交互式输入类名：

```bash
box_riverpod_gen
```

在当前目录生成代码：

```bash
box_riverpod_gen Product
```

在指定目录生成：

```bash
box_riverpod_gen product_item --out lib/features/product
```

覆盖已存在文件：

```bash
box_riverpod_gen Product --force
```

不生成 copy_with_extension：

```bash
box_riverpod_gen Product --no-copywith
```

---

## 参数说明

### 位置参数

| 参数 | 说明 |
|----|----|
| `name` | 类名（可选；不传则进入交互输入） |

支持的输入形式：

- `Product`
- `product`
- `product_item`
- `ProductItem`

最终都会规范化为合法的 Dart `PascalCase` 类名。

---

### 可选参数

#### `--out`

输出目录（默认当前目录）

```bash
--out lib/features/product
```

#### `--force`

当目标文件已存在时强制覆盖

```bash
--force
```

#### `--no-copywith`

不生成 `copy_with_extension` 注解及 `part '*.g.dart'`

```bash
--no-copywith
```

#### `--legacy`（默认）

使用 legacy Riverpod API：

```dart
import 'package:flutter_riverpod/legacy.dart';
```

#### `--modern`

使用新版 Riverpod API：

```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
```

---

## 生成的文件结构

示例：

```bash
box_riverpod_gen Product
```

生成：

```
product_notifier.dart
product_state.c.dart
```

---

## build_runner 说明

当未使用 `--no-copywith` 时，需要手动执行：

```bash
flutter pub run build_runner build -d
```

---

## 退出码约定

| 退出码 | 含义 |
|------|------|
| 0 | 成功 |
| 1 | 文件已存在且未使用 `--force` |
| 2 | 类名非法 |
| 130 | 用户中断（Ctrl+C） |

---

## 设计目标

- 模板结构稳定、可预测
- 命名规范自动纠正
- 仅生成结构，不强加业务逻辑
- 适合被脚本工具集统一调度
