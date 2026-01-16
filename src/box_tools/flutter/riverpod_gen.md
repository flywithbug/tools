```markdown
# riverpod_gen

`riverpod_gen` 用于生成 **Riverpod StateNotifier + State** 的模板文件：

- `*_notifier.dart`
- `*_state.c.dart`

它支持交互输入，也支持命令行参数（适合脚手架/脚本）。

---

## 安装

`riverpod_gen` 随工具集一起安装。

安装后可执行：

```sh
riverpod_gen --help
```

---

## 使用方式

### 交互模式（默认）

直接运行：

```sh
riverpod_gen
```

会提示输入：

- 类名（例如 `Product`）
- 输出目录（留空则当前目录）

---

### 非交互模式

直接传入类名：

```sh
riverpod_gen Product
riverpod_gen product_item
riverpod_gen ProductItem --out lib/features/product
```

支持的输入形式：

- `Product`
- `product`
- `ProductItem`
- `product_item`

都会被规范化为 Dart 类名（PascalCase）与文件名（snake_case）。

---

## 输出文件

以 `ProductItem` 为例，会生成：

- `product_item_notifier.dart`
- `product_item_state.c.dart`

---

## 选项

### 指定输出目录

```sh
riverpod_gen Product --out lib/features/product
```

### 覆盖已存在文件

默认如果目标文件已存在会报错退出。需要覆盖时使用：

```sh
riverpod_gen Product --force
```

### 不生成 CopyWith（可选）

默认 `state` 会包含 `copy_with_extension` 注解与 `part '*.g.dart'`。

如果你不需要这套生成逻辑：

```sh
riverpod_gen Product --no-copywith
```

### Riverpod import（legacy / modern）

默认使用：

```dart
import 'package:flutter_riverpod/legacy.dart';
```

如需使用现代导入：

```sh
riverpod_gen Product --modern
```

将改为：

```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
```

---

## 示例

### 在当前目录生成

```sh
riverpod_gen Product
```

### 生成到指定目录

```sh
riverpod_gen product_item --out lib/features/product
```

### 覆盖已有文件

```sh
riverpod_gen Product --force
```

---

## 常见问题

### 为什么 state 文件名是 `*_state.c.dart`？

这是为了配合常见的代码生成文件命名约定（例如 `copy_with_extension` 的 `part` 输出），避免与手写文件混淆。

### 生成了 `part '*.g.dart'`，还需要做什么？

需要运行 build_runner 才会生成 `*.g.dart`：

```sh
flutter pub run build_runner build -d
```

---
```
