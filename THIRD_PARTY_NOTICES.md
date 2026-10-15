# 第三方声明

KV Agent Desk 本身按仓库根目录的 [MIT License](LICENSE) 发布。第三方代码、构建工具、品牌标志及协议参考仍受其各自许可或权利约束。

## 运行依赖

具体版本以 [`requirements.txt`](requirements.txt) 和 [`requirements-build.lock.txt`](requirements-build.lock.txt) 为准。

| 组件 | 用途 | 声明 |
| --- | --- | --- |
| [Pillow](https://github.com/python-pillow/Pillow) | 本机图像绘制与 PNG/JPEG 编码 | MIT-CMU；请保留其上游许可声明。 |
| [pywebview](https://github.com/r0x0r/pywebview) | Windows WebView2 桌面窗口 | BSD 3-Clause。 |
| [pystray](https://github.com/moses-palmer/pystray) | Windows 系统托盘图标与菜单 | GNU LGPL v3；分发包含该依赖的构建时应遵守其许可条款及适用的再链接要求。 |
| [pythonnet](https://github.com/pythonnet/pythonnet) 与 [clr-loader](https://github.com/pythonnet/clr-loader) | pywebview WinForms/WebView2 Windows 后端依赖 | MIT。 |
| [Bottle](https://github.com/bottlepy/bottle) | pywebview 后端依赖 | MIT。 |

精确依赖和传递依赖由构建锁文件列出。请以依赖发行包内附带的 LICENSE/COPYING 文本为准；分发时不得移除其中适用的版权和许可声明。

## 构建工具

[PyInstaller](https://github.com/pyinstaller/pyinstaller) 用于构建 Windows 单文件程序，属于构建工具而非应用运行逻辑。其 GPLv2-or-later 许可包含用于由 PyInstaller 构建的可执行程序的例外条款；请同时阅读该仓库内当前版本的许可与例外文本。版本以 `requirements-build.lock.txt` 为准。

## 图片与界面参考

- `agent_dashboard/assets/deepseek.svg` 来自 [DeepSeek-LLM 官方仓库](https://github.com/deepseek-ai/DeepSeek-LLM/blob/main/images/logo.svg)；`deepseek.ico` 来自 [DeepSeek 官方网站图标](https://www.deepseek.com/favicon.ico)，`deepseek.png` 从 ICO 原尺寸转换而来。商标归其权利人所有，图片仅用于识别相应账户卡片。
- KV Agent Desk 的 `app.ico` 与托盘图标由本项目绘制。
- 界面方向参考 [MacTR AI](https://github.com/m1ng-li/mac-thermalright-ai-monitor) 的 LCD Agent 仪表盘。本项目没有复制其源代码或图像资源。

## 设备协议与 API 参考

下列项目仅用于核对公开设备协议事实；`agent_dashboard/display.py` 是独立实现，未复制其实现代码：

- [trofeo-lcd-rs](https://github.com/haven80/trofeo-lcd-rs)，上游仓库采用 GPL-3.0-or-later。
- [thermalright-trcc-linux](https://github.com/Lexonight1/thermalright-trcc-linux)，上游项目采用 GPL-3.0-or-later。
- [Microsoft WinUSB_Initialize](https://learn.microsoft.com/windows/win32/api/winusb/nf-winusb-winusb_initialize)，用于确认 Windows 系统 WinUSB API 的调用方式。

账户额度查询对照供应商公开接口说明：[DeepSeek Get User Balance](https://api-docs.deepseek.com/api/get-user-balance/)。本项目只使用其余额查询接口，不复制供应商 SDK。
