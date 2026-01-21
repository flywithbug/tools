# tools

一个用于日常开发/发布的小工具集（Python 脚本工具库）。

## 安装

使用 pipx 从 GitHub 安装/强制覆盖安装：

```bash
pipx install --force "git+https://github.com/flywithbug/tools.git"
```


## 工具集介绍

## 目录

- [工具总览](#section)
- [工具集文档索引](#section)
- [box（工具集管理）](#box)
  - [`box`](#box-cli)
- [flutter/pub_version](#flutter-pub_version)
  - [`box_pub_version`](#box_tools-flutter-pub_version-cli)

---

<a id="section"></a>

## 工具总览

### box（工具集管理）

- **[`box`](#box-cli)**：工具集管理入口：诊断、更新、版本查看、卸载、工具列表（[文档](README.md)）

### flutter/pub_version

- **[`box_pub_version`](#box_tools-flutter-pub_version-cli)**：升级 Flutter pubspec.yaml 的 version（支持交互选择 minor/patch，可选 git 提交）（[文档](README.md)）

---

<a id="section"></a>

## 工具集文档索引

### box（工具集管理）

- **box**：[README.md](README.md)

### flutter/pub_version

- **box_pub_version**：[README.md](README.md)

---

<a id="box-cli"></a>

## box（工具集管理）

**简介**：工具集管理入口：诊断、更新、版本查看、卸载、工具列表

**命令**：`box`

**用法**

```bash
box --help
box help
box doctor
box update
box version
box uninstall
box tools
box tools --full
```

**参数说明**

- `--help`：显示帮助（等同 box help）
- `tools --full`：显示工具的详细信息（options/examples），并显示导入失败原因

**示例**

- `box doctor`：诊断环境（python/pipx/PATH）
- `box update`：更新工具集
- `box tools`：列出当前工具与简介

**文档**

[README.md](README.md)

---

<a id="flutter-pub_version"></a>

## flutter/pub_version

<a id="box_tools-flutter-pub_version-cli"></a>

### box_pub_version

**简介**：升级 Flutter pubspec.yaml 的 version（支持交互选择 minor/patch，可选 git 提交）

**命令**：`box_pub_version`

**用法**

```bash
box_pub_version
box_pub_version minor
box_pub_version patch --no-git
box_pub_version minor --file path/to/pubspec.yaml
```

**参数说明**

- `--file`：指定 pubspec.yaml 路径（默认 ./pubspec.yaml）
- `--no-git`：只改版本号，不执行 git add/commit/push

**示例**

- `box_pub_version`：进入交互菜单选择升级级别
- `box_pub_version patch --no-git`：仅更新补丁号，不提交

**文档**

[README.md](README.md)

---

