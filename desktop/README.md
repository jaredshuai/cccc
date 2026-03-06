# CCCC Desktop

CCCC 桌面应用打包配置，使用 **Electrobun + Nuitka + uv** 构建。

## 目录结构

```
desktop/
├── electrobun/           # Electrobun 桌面应用项目
│   ├── src/
│   │   ├── bun/         # 主进程代码 (Bun/TypeScript)
│   │   └── views/       # WebView 前端
│   ├── electrobun.config.ts
│   ├── package.json
│   └── tsconfig.json
│
├── scripts/              # 构建脚本
│   ├── build.py         # 统一编排器（唯一真逻辑）
│   ├── build-nuitka.py  # Nuitka Python 打包脚本
│   ├── build.sh         # Linux/macOS 包装入口（转发到 build.py）
│   └── build.cmd        # Windows 包装入口（转发到 build.py）
│
├── dist/                 # 构建输出 (git ignored)
│   └── cccc-backend/    # Nuitka standalone 后端目录
│       └── cccc-backend.exe
│
└── README.md
```

## 架构

```
┌─────────────────────────────────────────────┐
│           Electrobun App (~14MB)            │
│  ┌───────────────────────────────────────┐  │
│  │     Main Process (Bun/TypeScript)     │  │
│  │                                       │  │
│  │  Bun.spawn() ──► cccc-backend.exe    │  │
│  │                       │               │  │
│  │  ┌────────────────────▼────────────┐  │  │
│  │  │      WebView (React UI)         │  │  │
│  │  │   http://localhost:8848/ui/     │  │  │
│  │  └─────────────────────────────────┘  │  │
│  └───────────────────────────────────────┘  │
│                                             │
│  ┌───────────────────────────────────────┐  │
│  │    cccc-backend.exe (Nuitka)          │  │
│  │    FastAPI + Uvicorn @ :8848          │  │
│  │    Size: ~50-80MB                     │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

## 前置要求

1. **uv** - Python 包管理器
   ```bash
   # Windows
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

   # macOS/Linux
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **bun** - JavaScript 运行时
   ```bash
   # Windows
   powershell -c "irm bun.sh/install.ps1 | iex"

   # macOS/Linux
   curl -fsSL https://bun.sh/install | bash
   ```

3. **C 编译器** (Nuitka 需要)
   - Windows: 安装 Visual Studio Build Tools 或 MinGW
   - macOS: 安装 Xcode Command Line Tools (`xcode-select --install`)
   - Linux: 安装 gcc (`sudo apt install gcc` 或 `sudo dnf install gcc`)

## 快速开始

统一构建接口（Windows/macOS/Linux 语义一致）：

```bash
build --stage <prep|web|backend|app|bundle|verify|all> \
      --platform <windows|macos|linux> \
      --channel <stable|canary> \
      --version <auto|x.y.z> \
      --force <stage|all> \
      --clean
```

在本项目中，请使用包装脚本调用：

```bash
# Windows PowerShell / CMD
cd desktop/scripts
build.cmd --stage all --platform windows --channel stable --version auto

# macOS / Linux
cd desktop/scripts
chmod +x build.sh
./build.sh --stage all --platform macos --channel stable --version auto
```

分步构建（支持中断续打）：

```bash
# 只做准备检查
build.cmd --stage prep

# 分阶段执行
build.cmd --stage web
build.cmd --stage backend
build.cmd --stage app
build.cmd --stage bundle
build.cmd --stage verify

# 强制重跑阶段
build.cmd --stage app --force app
build.cmd --stage all --force all

# 显式清理
build.cmd --clean --stage all
```

默认值：
- `--stage all`
- `--channel stable`
- `--version auto`（自动解析为 `<appVersion>-<gitSha>`）
- `--platform` 为当前主机平台

续打状态标记路径：
- `desktop/.build-state/<platform>/<channel>/<version>/<stage>.ok`

渠道说明：
- 对外统一 `stable/canary`
- 输入 `dev` 会被自动映射为 `canary`

## 构建选项

### Nuitka 脚本选项

```bash
uv run python build-nuitka.py --help

# 可用选项:
--platform <platform>  目标平台 (windows, macos, linux)
--clean               清理构建产物
--standalone          强制构建为独立目录
--onefile             强制构建为单文件
--fast                强制启用 fast(ccache)
--no-fast             禁用 fast 模式
```

### Electrobun 原生命令（调试用）

```bash
bun run dev           # 开发模式
bun run build         # 当前平台
bun run build:win     # Windows (stable)
bun run build:win:dev # Windows (legacy dev, 等价 canary 语义)
bun run build:mac     # macOS
bun run build:linux   # Linux
```

## 输出位置

| 组件 | 输出路径 |
|------|----------|
| Python 后端 | `desktop/dist/cccc-backend/cccc-backend(.exe)` |
| Electrobun 原始输出 | `desktop/electrobun/dist/<channel>-*` |
| Electrobun 构建归档 | `desktop/electrobun/artifacts/` |
| 统一发布目录 | `desktop/release/<platform>/<channel>/<version>/` |
| 发布清单 | `desktop/release/<platform>/<channel>/<version>/manifest.json` |
| CI 交付目录 | `desktop/delivery/<platform>/<channel>/<version>/` |
| CI 交付清单 | `desktop/delivery/<platform>/<channel>/<version>/manifest.json` |
| Windows Portable 入口 | `desktop/release/windows/<channel>/<version>/portable/bin/launcher.exe` |
| Windows 安装包 | `desktop/release/windows/<channel>/<version>/<channel>-win-x64-*-Setup.exe` |

## 常见问题

### Q: Nuitka 编译失败

确保安装了 C 编译器：
- Windows: Visual Studio Build Tools (推荐) 或 MinGW-w64
- macOS: `xcode-select --install`
- Linux: `sudo apt install gcc g++`

### Q: 找不到 bun

确保 bun 已添加到 PATH：
- Windows: 重启终端或手动添加 `%USERPROFILE%\.bun\bin` 到 PATH
- macOS/Linux: 执行 `source ~/.bashrc` 或 `source ~/.zshrc`

### Q: 找不到 uv

确保 uv 已安装并添加到 PATH。

### Q: 后端启动超时

检查 `desktop/dist/cccc-backend/` 目录下是否有 `cccc-backend`（Windows 为 `.exe`）文件。
首次构建可能需要较长时间，Nuitka 会下载依赖。

## 技术栈

| 工具 | 用途 | 版本 |
|------|------|------|
| Electrobun | 桌面应用壳 | ^1.13.0 |
| Nuitka | Python 编译器 | latest |
| uv | Python 包管理 | latest |
| bun | JS 运行时 | ^1.0.0 |
| TypeScript | 类型安全 | ^5.7.0 |

## 许可证

Apache-2.0
