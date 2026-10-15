# Instagram 控制台

面向 Apple Silicon Mac 的 iPhone USB 控制桌面应用。当前版本 **1.1.6**。

## 下载与安装

[点击下载最新版安装包](https://github.com/cwjgege/instagram-console/releases/latest/download/Instagram-Console-AppleSilicon.dmg)

[查看版本说明](https://github.com/cwjgege/instagram-console/releases/latest)

本仓库为公开仓库，安装包可直接下载，无需登录 GitHub。
打开 DMG，将应用拖入 Applications。安装包内置 Python、Tcl/Tk、Pillow、Node、Appium 和 XCUITest 驱动。
仍需在 Mac 安装完整 Xcode，并按应用向导完成 USB 信任、开发者模式和本机签名。
手机与 Mac 无需使用同一个 Apple ID。

当前为 ad-hoc 签名的内部测试版，尚未完成 Apple Developer ID 签名和公证。
支持 Apple Silicon；未完成第二台 Mac 的端到端验收。

完整步骤见 [安装与使用](docs/安装与使用.txt)。

## 功能

- 手动启动、停止；停止后等待任务退出与设备锁释放。
- 英文关键词搜索，识别视频结果、观看、返回并滚动。
- 中央安全区域手势、滑动后页面检查、USB 重连和有界页面恢复。
- 设备配置、控制组件安装/重新签名、签名到期提醒。
- 可配置内容分析与英文评论规则；默认关闭自动评论并使用预览。
- 20 分钟无社交互动诊断模式。

自动化不能保证账号增长、推荐国家或规避平台检测。遇到验证、限制或不能确认的操作结果时停止。

## 1.1.6 修复

移除安装和重连时强制卸载 WebDriverAgent 的行为，旧版保存的卸载参数也会在建立连接前纠正。
旧流程的 `useNewWDA=true` 会触发 Appium 卸载控件；删除某开发者最后一个 App 可能撤销手机端信任，造成信任后重装再次失效的循环。
新版保留编译、签名、实际连接和有效期验证，不绕过手机的开发者信任要求。
更新后先在 iPhone 完成本次签名账号的信任，再在应用重试安装。签名账号变化或证书失效仍可能需要重新信任。
96 项自动检查通过，包含安装、旧请求迁移和强制重连不请求卸载的回归检查。尚未在发生问题的新 Mac 验证成功。

## 1.1.5 历史修复

设备向导改为可滚动表单与固定结果区域，避免大字体/小屏幕裁切错误提示。无有效签名记录时明确提示，自动检测保留最后安装结果。安装时开启详细 Xcode 输出到 `appium.log`。93 项自动检查通过，另在模拟设备的 620×540 小窗口中检查布局。安装失败根因仍需新电脑的详细日志确认；本版未在该新电脑实测安装。

## 1.1.4 历史验证

已在一台 Apple Silicon Mac 与一台 iPhone 11 上累计实际运行 20 分 11 秒：
5 次启动、4 次中途重启、44 次翻页、5 次搜索、8 次搜索结果视频观看，无异常退出，社交互动均为 0。
两次 Instagram 离开前台均约 3 秒恢复，根因仍待定位。
用户真实鼠标确认升级后的按钮可响应；后续自动启停通过键盘激活按钮，不能视为多轮真实鼠标测试。
88 项自动检查通过。不同设备、系统与 Xcode 组合仍需实测。

## 开发与检查

使用 Python 3.14（含 Tcl/Tk 9）与 Node.js。安装项目依赖：

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
mkdir -p work
python -m unittest discover -s tests
```

构建 Apple Silicon 应用：

```sh
python tools/build_mac.py
```

构建脚本使用 npm 锁文件安装 Node 依赖，生成 `dist/Instagram 控制台.app`。
该脚本是移植后的构建入口，尚未在第二台 Mac 验证；已发布 DMG 使用本机经过验证的构建环境产生。
仓库不包含预编译运行环境、个人设备配置、API Key、Apple 签名凭据或运行日志。
首次源码运行会由 `src/default-config.json` 生成本地 `src/config.json`；直接运行源码时还需配置本地设备控制服务，推荐用户使用 DMG。

## 数据位置

打包版数据保存在 `~/Library/Application Support/Instagram Console/`。
API Key 只存于当前应用进程；使用模型服务会将采样内容发送到所选服务商。
不要将用户数据目录、签名证书、密钥或设备日志提交到仓库。
