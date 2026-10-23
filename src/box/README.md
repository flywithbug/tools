````markdown
# box

`box` 是一个轻量、可靠的命令行工具，用于统一管理和运行你的工具命令。

它提供一个稳定的 CLI 入口，并内置 **自诊断 / 自更新 / 自卸载** 能力，适合长期使用。

---

## 安装

### 一行安装（推荐）

```sh
sh -c "$(curl -fsSL https://raw.githubusercontent.com/flywithbug/tools/master/install.sh)"
````

安装完成后，`box` 会作为全局命令可用。

验证安装：

```sh
box --help
```

---

## 快速开始

查看可用命令：

```sh
box help
```

检查当前环境（推荐首次安装后运行）：

```sh
box doctor
```

---

## 命令说明

### `box help`

显示 `box` 的帮助信息和可用命令列表。

```sh
box help
# 或
box --help
```

---

### `box doctor`

诊断当前运行环境，帮助你快速定位常见问题。

```sh
box doctor
```

检查内容包括：

* Python 解释器路径与版本
* `pipx` 是否已安装
* `box` 命令是否在 `PATH` 中
* 推荐的配置目录是否存在

当 `box` 无法正常工作时，**请优先运行此命令**。

---

### `box update`

将 `box` 更新到最新版本。

```sh
box update
```

说明：

* 如果通过 `pipx` 安装：使用 `pipx upgrade / reinstall`
* 如果未检测到 `pipx`：给出安全的修复建议

这是推荐的升级方式，无需重新运行安装脚本。

---

### `box version`

显示当前 `box` 的版本号。

```sh
box version
```

---

### `box uninstall`

从系统中卸载 `box`。

```sh
box uninstall
```

这是最干净、最安全的卸载方式（适用于 `pipx` 安装）。

---

## 常见问题

### 出现 “space in the pipx home path” 警告

该警告表示 `pipx` 的安装路径包含空格（例如 `Application Support`），在某些环境下可能引发兼容性问题。

推荐将 `pipx` 目录迁移到无空格路径，例如：

```sh
PIPX_HOME=$HOME/.local/pipx
PIPX_BIN_DIR=$HOME/.local/bin
```

迁移后重新安装 `box` 即可。

---

### `box` 命令不存在或无法执行

请确认：

1. 安装过程未报错
2. `box doctor` 输出中未提示 PATH 问题
3. `~/.local/bin`（或你的 pipx bin 目录）已加入 `PATH`

---

## 卸载与重装

如需完全重装：

```sh
box uninstall
sh -c "$(curl -fsSL https://raw.githubusercontent.com/flywithbug/tools/master/install.sh)"
```

---


## License

MIT

```
```
