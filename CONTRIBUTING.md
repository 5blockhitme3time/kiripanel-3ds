# 参与开发

欢迎 issue（兼容性报告、bug）和 PR。下面是怎么把各部分跑起来、怎么给新游戏写适配器。

## 环境

| 部分 | 需要 |
|---|---|
| 游戏内插件 | Windows + Visual Studio（或 Build Tools）的 C++ 工作负载；`plugin\native\build.bat` 用 `vswhere` 找，也可以用环境变量 `VCVARS` 指定 `vcvarsall.bat` |
| PC 端 | Python 3.8+（无第三方依赖），打包需要 `pyinstaller` |
| 3DS 端 | devkitPro（devkitARM）+ portlibs（freetype、libpng、curl、mbedtls、opus、zlib、bzip2、libctru、citro2d/3d） |
| 面板本体 | 任意 Linux/WSL + system freetype（不用 3DS 工具链） |

3DS 工具链最省事的装法是 WSL + 上游的 `Dockerfile`（CI 就是这么编的）：

```bash
cd console/moonlight-n3ds
docker build --network=host -t moonlight-n3ds .
docker run --rm -v "$PWD":/moonlight-N3DS -w /moonlight-N3DS moonlight-n3ds make
```

## 跑测试

```bash
python -m unittest discover -s pc/tests          # PC 端 55 项
wsl bash console/panel_preview/build.sh          # 面板 96 项（ASan）+ 预览图
python plugin/native/proxytest.py plugin/native/out/x64/version.dll
python dev/regress.py --list                     # 真游戏回归的场景
```

真游戏回归需要本地有游戏：把路径写进 `dev/testgames.json`（**不入库**，
格式见 `dev/testgames.example.json`），或用 `KIRIPANEL_TEST_EXE` /
`KIRIPANEL_KAG3_GAME` 环境变量。**测游戏时永远不要碰真实存档**：
`-datapath` 指向副本或空目录；不认 `-datapath` 的启动器要先备份 `savedata`。

## 给新游戏写适配器

1. **看它属于哪一档**：管理窗口里点「检测兼容性」，或
   `python dev/compatrun.py GAME_DIR`。
2. **探索**：`python dev/probe.py start GAME_DIR`（临时装上开发用插件并启动），然后
   - `probe.py eval "kag.historyLayer"` / `eval -f 文件.tjs` 看对象
   - `probe.py shot 输出.png` 截游戏窗口
   - `probe.py src 脚本名` 把游戏自己读到的脚本拷出来
   - `probe.py state` 看钩子现在报的状态
3. **导出侦察报告**：游戏运行中在管理窗口点「导出诊断报告」（或发 `scout` 命令），
   会得到全局对象成员、已加载插件、图层树、疑似回想/选项/系统按钮的对象。
4. **写适配器**：照 `plugin/hook/adapter_base.tjs` 的接口写 `adapter_<名字>.tjs`
   （放 `plugin/hook/`，会被自动安装），`detect()` 返回分数，`core.tjs` 取最高分的那个；
   一直抛异常会被丢掉，游戏不受影响。
5. **验证**：`python dev/regress.py --game GAME_DIR --adapter <名字> --list` 先看场景，
   再跑 `story choice title` 等；插件在游戏里跑起来后也可以用 `--setup "kag.allskip=1"`
   这类 TJS 做前置。

### TJS 侧踩过的坑

- **只在 `kag.inStable` 的时候按键**，否则游戏会报致命错误；前进要用按钮自己的
  `onExecute`，不要用 `processLink`（后者会绕过稳定检查，也会绕过游戏自己的记录逻辑）。
- `incontextof global` 之后的函数看不到外层局部变量，也看不到自己的名字（递归要用全局函数）。
- `Array.join` 不能用；子串是 `substring(start, len)`。
- 钩子文件保持**纯 ASCII、无 BOM、LF**（引擎可能按 Shift-JIS、UTF-8 或系统代码页读它，
  只有 ASCII 在所有情况下都一样）。`install.check_ascii()` 会在打包时挡住违规。
- 隐藏电脑上的对话框要用**图层 opacity**，不能用 `visible`；而且必须包装
  `kag.internalStoreFlags/internalRestoreFlags`，否则存档会把隐藏状态存进去。

## 代码风格

跟周围代码保持一致：文件头写清楚这个文件负责什么、为什么这么做；注释解释
"为什么"而不是"做什么"；C++ 用 4 空格、`snake_case` 函数；Python 与
`pc/kiripanel/` 里的现有风格一致（无第三方依赖、标准库优先）；TJS 用 tab 缩进。

## 提交与 PR

- 一次提交做一件事，说明写清楚"为什么"。
- PR 里请写：改了哪一部分、怎么验证的（跑了哪些测试 / 在哪款游戏上试过）。
  涉及 3DS 端的改动请注明是**真机**验证还是只在 PC 上的面板测试里验证过。
- 新增对某款游戏的支持时，**不要**把游戏脚本、图片、存档或大段原文台词提进仓库；
  适配器里只放调用游戏自身函数所需的代码。
