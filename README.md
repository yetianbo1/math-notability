# NotabilityEditor

> **本仓库说明（2026-11-08）**
> 本仓库由 [SeedStudent/NotabilityEditor](https://github.com/SeedStudent/NotabilityEditor) 的 **0.7.9-Beta** 源码导入，作为个人二次开发的管理仓库。导入时未修改任何功能代码。
> 原创代码采用 **0BSD** 许可；部分源自 GZhonghui/NotabilityViewer 的代码适用 MIT；第三方依赖各按自身许可。授权范围与义务详见 `LICENSE`、`LICENSE_SCOPE`、`THIRD_PARTY_NOTICES`、`DISCLAIMER`。
> 本地构建环境为 Windows x64 + Python 3.11.9 + PyInstaller 6.22.3，产物不纳入版本管理。

> **本地二次开发版本：0.12.0-Beta**（上游导入版本仍为 0.7.9-Beta）。版本号只写在 `core/ne_app_version.py` 的 `APP_VERSION`；窗口标题、关于对话框、日志、PDF 生成器属性和 EXE 文件名都从该处读取。新增功能递增次版本号（如 `0.12.0-Beta`），仅修 bug 递增修订号（如 `0.10.1-Beta`）。改完执行 `.build-venv\Scripts\python.exe build_local.py`，会自动生成 `windows_version_info.ver` 并产出 `dist\NotabilityEditor-<版本>-local.exe`。
>
> 本地新增：图形画笔、浮窗草稿纸、Ctrl+滚轮缩放、选区缩放笔迹；题目卡为**一整块连续可写的白纸**（题目截图贴上部的底图，下面直接作答），新建/重开自动全屏、卡片自带与主页同款的完整工具条、写完停笔 1.5 秒自动保存；做过卡片的题目在 PDF 页面上显示淡蓝细框（仅屏幕显示，导出不带），Ctrl+单击框内可重开卡片。工具栏固定顶部横排。0.11.0 起：题目卡笔迹改用与主画布相同的位图缓存（写再多笔也保持恒定帧耗）、卡片内移除「阅读」工具，工具栏新增「加长」按钮可向下追加最多 6 屏草稿纸，普通滚轮上下滚动、Ctrl+滚轮仍是缩放，纸张长度随卡片一起保存、重开恢复。0.12.0 起：修复卡片关闭再打开时历史笔迹看不见（数据其实已保存，缺的是渲染场景注册）；按 Esc 或点关闭按钮都会立即保存并退出卡片，等后台真正写盘后弹「已保存」确认框，切换笔记/退出软件的批量关闭不弹框；卡片全屏时的 Esc 不再只是还原，「全屏/还原」改由工具栏按钮操作。

NotabilityEditor 是面向 Notability 笔记的本地阅读与编辑工具，支持 NTB 笔记编辑、NOTE 笔记阅读，以及 PDF 导入和导出。软件免费提供。

## 主要功能

- **笔记管理**：新建、打开和导入笔记，首页展示最近笔记，笔记页按分类分组；支持两级分类、分类管理、拖动及批量分类，按名称或路径搜索，使用封面或列表视图，支持全选、多选与移至回收站。
- **手写编辑**：画笔、荧光笔、遮字胶带、整笔及局部橡皮擦、矩形和自由选区、笔迹移动、撤销与重做。
- **页面与图片**：管理可编辑页面，插入图片，选择纸张尺寸、背景颜色及点阵／横线／空白底页，支持缩放、漫游、全屏及无缝跨页书写。
- **格式转换**：导入 PDF 创建 NTB 笔记，导出 PDF 时保留原始页面尺寸并同步缩放背景和笔迹；NOTE 以阅读与导出为主。
- **保存与恢复**：后台保存、原子替换、外部修改冲突检查及本地恢复快照；异常退出后可恢复已落盘的编辑。
- **Windows 集成**：按需注册应用和设置笔记默认打开方式，在设置及帮助菜单中主动检查更新、前往发布页。
- **本地触控工作台**：通过浏览器与本机服务打开和编辑 NTB，支持快照、保存、下载及 PDF 导出；当前不提供原生 Android 安装包。

分类栏空白处右键可新建一级分类，右键一级分类可新建二级分类；在“分类管理”中可重命名、移动和移除分类。拖动笔记到左侧分类即可整理，勾选多本后可批量拖动。

分类和笔记索引保存在 `%LOCALAPPDATA%\NotabilityEditor\home-library.json`，导入副本及新建笔记默认保存在该目录下的 `library` 中，恢复数据位于 `recovery` 中。直接打开的外部笔记保留原路径；导入笔记会创建副本。备份时请同时保存索引与实际笔记文件。

## 启动与部署

### 使用 Windows 程序

解压下载的文件后运行 EXE。需要安装到当前用户目录、创建快捷方式或设置笔记打开方式时，使用“设置 → 注册应用到 Windows…”。有效注册版本等于或高于当前运行版本时，不再显示注册提醒。

免费软件告知需要在前台阅读满五秒后确认。检查更新仅由用户主动点击触发，不会自动下载或安装更新。

### 从源码启动

Windows 构建环境使用 x64 Python 3.11。进入项目根目录，在 PowerShell 中执行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

依赖版本由 `requirements.txt` 固定。`main.py` 启动桌面主页和编辑器。

### 编译并安装

完整解压源码后双击 `INSTALL.bat`。它会调用 `deploy_windows.ps1`，准备 Python 环境、安装依赖、构建独立 EXE，并安装到当前用户目录及创建快捷方式。

默认安装位置为 `%LOCALAPPDATA%\Programs\NotabilityEditor`，构建产物位于项目的 `dist` 目录，部署日志位于 `deploy_logs` 目录。安装与注册使用统一版本比较，阻止旧版本覆盖较新程序；用户数据独立于程序目录保存。

### 启动本地触控工作台

安装依赖后执行：

```powershell
.\.venv\Scripts\python.exe tablet_server.py
```

服务默认监听 `127.0.0.1:8767` 并打开浏览器，可使用 `--port` 指定端口、`--no-browser` 关闭自动打开、`--data-dir` 指定工作目录。通过服务进入界面，不要直接双击 HTML 文件。

触控工作副本及恢复快照默认位于 `.tablet-data`，下载文件由用户选择保存位置。触控功能以 NTB 为主，已有图片和文字作为受保护内容显示，部分页面结构操作及 PDF 导入尚未接入。

## 源码架构

```text
项目根目录/
├── main.py                  # 桌面入口，组装主页、编辑器及启动流程
├── tablet_server.py         # 本地触控服务入口
├── core/                    # 软件身份、版本比较、几何和安全提交
├── document_core/           # 文档历史及不可变快照
├── formats/                 # NTB/NOTE 解析和原生格式写回
├── editing/                 # 笔迹、擦除、选区、撤销及保存恢复
├── import_export/           # 新建笔记、PDF 导入与导出
├── images/                  # 图片事件、几何和图层
├── pages/                   # 页面身份、能力校验及结构操作
├── rendering/               # 页面渲染、混合图层和缓存
├── ui/                      # 桌面主页、编辑页、菜单和工具栏
│   ├── home/                # 分类、笔记索引、卡片及批量操作
│   └── editor/              # 编辑视图容器
├── settings/                # 偏好、快捷键、启动提示和更新检查
├── platform_integration/    # Windows 安装、注册及用户数据迁移
├── infrastructure/          # 诊断和本地 AI 运行库
├── runtime/                 # 打包运行时钩子
├── rewritten/               # 触控后端、文件传输及编辑连接
├── android-tablet/design/   # 浏览器触控界面与共享控件
├── assets/                  # 图标、纸张模板和本地模型
├── tools/                   # 图标与诊断辅助工具
├── requirements.txt         # 运行依赖
├── INSTALL.bat              # Windows 一键部署入口
├── deploy_windows.ps1       # 环境准备、构建及安装脚本
├── VERSION_INFO.json        # 构建元数据
├── LICENSE                 # 原创内容许可
├── LICENSE_SCOPE           # 许可适用范围
├── THIRD_PARTY_NOTICES      # 上游与依赖声明
└── DISCLAIMER              # 免责声明
```

主要 Python 模块使用 `ne_` 文件名前缀。界面层负责展示与输入，格式层负责二进制解析和写回，文档及编辑层维护历史和快照；桌面与触控连接层调用共用处理引擎。

## 许可与免责

本项目有权授权的原创代码及文档使用 **0BSD** 许可，允许使用、复制、修改、商用和分发，无需署名。完整条款见 `LICENSE`，适用范围见 `LICENSE_SCOPE`。

源自上游 NotabilityViewer 的代码继续适用 MIT 许可；第三方依赖、模型和资源分别适用自身许可。0BSD 不会将全部源码或打包组件重新授权。来源及许可声明见 `THIRD_PARTY_NOTICES`。

软件按现状提供，不保证全部笔记格式或场景兼容。编辑、转换或覆盖保存前请备份原始笔记，并核对保存与导出结果。完整免责条款及适用法律限制见 `DISCLAIMER` 和 `LICENSE`。
