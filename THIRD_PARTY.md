# 第三方组件

KiriPanel 以 GPL-3.0 发布（见 [LICENSE](LICENSE)）。下面这些是随仓库一起分发、
或被打进发布包里的第三方内容，各自的权利归原作者。

| 组件 | 在哪 | 协议 | 说明 |
|---|---|---|---|
| [Moonlight-N3DS](https://github.com/zoeyjodon/moonlight-N3DS) | `console/moonlight-n3ds/`（子模块） | GPL-3.0 | 3DS 端的串流程序，本项目是它的 fork。该目录下有它自己的 LICENSE 与第三方声明 |
| [moonlight-common-c](https://github.com/moonlight-stream/moonlight-common-c) 等 | `console/moonlight-n3ds/third_party/`（子模块） | 见各自目录 | 由 Moonlight-N3DS 引入 |
| [MinHook](https://github.com/TsudaKageyu/minhook) 1.3.3 | `plugin/native/minhook/` | BSD-2-Clause | `GetProcAddress` 挂钩；许可证在 `minhook/LICENSE.txt` |
| `tp_stub.h` / `tp_stub.cpp` | `plugin/native/tp_stub/` | KiriKiri 2 (TVP2) 源码许可 | W.Dee 与贡献者，KiriKiri 2 的插件接口桩；其许可允许编进插件使用，不视为衍生作品 |
| [Noto Sans SC](https://fonts.google.com/noto/specimen/Noto+Sans+SC) | `console/fonts/charset.txt` 生成的子集，编进 `3ds/data/hkfont.ttf` | SIL Open Font License 1.1 | 内置字体的字形来源；子集由 `console/fonts/makesubset.py` 生成 |
| [3DS 端图标/横幅素材](https://github.com/zoeyjodon/moonlight-N3DS) | `console/moonlight-n3ds/3ds/gfx/` | GPL-3.0 | 来自上游，个别图片被改字 |

## 关于游戏本身

仓库里**不包含任何游戏的脚本、图片、音频或存档**：适配器只包含调用游戏自身函数所需的
代码，以及少量界面用的中文短句（`pc/kiripanel/profiles/*.json` 里的选项说明），
面板预览图里的台词是为演示编造的。请自备正版游戏。
