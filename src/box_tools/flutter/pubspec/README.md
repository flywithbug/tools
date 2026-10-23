# box_pubspec

Flutter 组件的依赖升级、版本管理与发布工具。默认启动交互菜单：

```text
1. upgrade             依赖升级
2. publish             依赖发布
3. publish_app         发布并集成到 App
4. publish_app_package 发布并集成到 App 后打包
5. version             版本升级
6. doctor              环境检测
0. exit                退出
```

主菜单、App 打包环境和平台选择直接回车时，默认选择第一项。

菜单 3、4 对接 GitHub Actions 发布链路：组件提交推送 → package workflow 发布成功 → App 核验发布凭据并合入精确版本。菜单 3 只集成；菜单 4 选择环境和平台，App 完成依赖校验、Nexus 发号后推送 `Apex-Build` 提交，由现有流程打包和审批。

```sh
box_pubspec
box_pubspec publish --app-integrate release-3.69.0
box_pubspec publish --app-integrate latest --app-package qa --app-platform ios
box_pubspec publish --app-integrate release-3.69.0 --app-package store --app-platform both
box_pubspec publish --app-integrate latest --dry-run
```

`prod` 是普通测试包，`store` 才是商店审核包。App 集成不再调用本地 `apex-release`，不需要本地 App 仓库，也不能配合 `-p true` 重复发布。菜单中的 `publish_app`、`publish_app_package` 是交互快捷项，命令行使用 `publish` 加上述参数。

组件需处于已推送的 release 分支。App 分支和平台以其 `release_tool/config/targets.json` 为准。显式 `latest` 由 App 云端查询远程最新 release；回车仍优先沿用当前 release 分支或本机缓存。失败时先检查 package 与 App Actions；已发布组件无需再次升级版本。

部署要求及详细流程见 [pub_publish.md](pub_publish.md)。`pub_version.py` 负责版本操作，`pub_upgrade.py` 负责依赖升级，`pub_publish.py` 负责组件发布请求。
