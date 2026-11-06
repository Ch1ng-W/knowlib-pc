# Knowlib PC · 电脑端截图搜题 / AI 解题助手（Windows）

> **框一块屏幕 → 自动识别题目 → 悬浮窗秒出答案和解析。**
> 适合网课、在线题库、PDF 试卷、刷题软件上做题：题目别抄了，框住就行。

[![platform](https://img.shields.io/badge/platform-Windows%2010%2F11-blue)](https://github.com/Ch1ng-W/knowlib-pc/releases)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)]()
[![license](https://img.shields.io/badge/license-MIT-green)]()
[![download](https://img.shields.io/badge/download-knowlib.fun-2f6feb)](https://knowlib.fun/download/Knowlib-PC-latest.exe)

**关键词**：电脑搜题 · 截图搜题 · 屏幕识别答题 · AI 解题助手 · 悬浮窗答案 ·
网课答题 · 在线考试练习 · 题库管理 · 本地 OCR · DeepSeek 解题 · 数学公式识别 ·
Windows 桌面工具 · 离线题库 · OpenAI 兼容 · Ollama 本地模型

---

## 它解决什么问题

在电脑上做题，题目在网页/PDF/客户端里，复制不了、也没法拍照搜。常见做法是
手打题目 → 粘贴到网页版 AI → 再切回来对照，一题要来回切三四次。

Knowlib PC 把这条链路压成一步：**框住那块屏幕区域，剩下的它一直盯着**。
题目一变（翻页、下一题、换卷），自动识别、自动问 AI，答案直接飘在屏幕上，
不用切窗口、不用复制粘贴。

## 适用场景

- **网课 / 在线考试系统**：题目在浏览器里不能复制，框住答题区即可
- **PDF / 电子书试卷**：翻页就出答案，不用一页页截图
- **刷题 App / 客户端**：把题目区框住，切题自动跟
- **考研 / 公考 / 医学 / 计算机等级**：题目长、公式多，本地 OCR + LaTeX 渲染照样能认
- **复习题库沉淀**：做过的题自动进本地题库，下次同题秒回，不烧 token

## 核心功能

- **框选屏幕区域**，持续监控；画面没变化就不 OCR、不请求（省 CPU、省 token）
- **本地 OCR（RapidOCR / ONNX）**：截图只在本机识别，0.3–0.7 秒，不联网也能跑
- **接你自己的 AI**：DeepSeek、通义千问、智谱 GLM、Kimi、硅基流动、任意 OpenAI 兼容中转、本地 Ollama
- **悬浮答案窗**：置顶、半透明、可拖动、流式输出；宽高、答案字号、解析字号、两种文字颜色都能实时调
- **选择题高亮**：识别出选项后直接标出正确项，不是干巴巴一行字母
- **图片作答**：OCR 认不准时，直接把截图发给多模态模型（视觉模型可预置选择）
- **本地题库**：答过的题自动入库（SQLite），精确 + 模糊匹配（抗 OCR 错字），支持手动编辑、插入图片、CSV/MD/JSON 导入、导出 MD/PDF
- **历史记录**：每次问答留档，勾选即可加入题库
- **自动更新**：优先走国内直连 `knowlib.fun`，失败回退 GitHub
- 截图前自动隐藏答案窗，不会把自己的答案拍进去造成死循环

## 快速开始

1. 下载 Releases 里的 `Knowlib-PC-x.y.z-setup.exe` 安装（或官网直连：<https://knowlib.fun/download/Knowlib-PC-latest.exe>）
2. 首次启动会弹设置：选服务商 → 填 API Key → 点 **测试连接**
   - DeepSeek 官方：`https://api.deepseek.com/v1`，模型 `deepseek-chat`，看图模型 `deepseek-flash`
   - 阿里百炼：`https://dashscope.aliyuncs.com/compatible-mode/v1`，`qwen-plus` / `qwen-vl-max`
   - 智谱 GLM：`https://open.bigmodel.cn/api/paas/v4`，`glm-4-plus` / `glm-4v-plus`
   - 月之暗面 Kimi：`https://api.moonshot.cn/v1`，`moonshot-v1-32k` / vision 版
   - 硅基流动：`https://api.siliconflow.cn/v1`，`deepseek-ai/DeepSeek-V3`
   - 本地 Ollama：`http://127.0.0.1:11434/v1`，模型 `qwen2.5vl:7b`，Key 随便填
3. 点「框选区域」，把题目框住
4. 点「开始」，或直接按 `F3`

## 热键

| 键 | 作用 |
|---|---|
| `F1` | 重新框选区域 |
| `F2` | 暂停 / 继续 |
| `F3` | 立即识别一次 |
| `F4` | 重问（换说法再解） |
| `F5` | 显示 / 隐藏答案窗 |

## 配置与数据

- `%APPDATA%\Knowlib\config.json`：设置（含 API Key）
- `%APPDATA%\Knowlib\bank.db`：本地题库
- `%APPDATA%\Knowlib\history.jsonl`：每次问答的题面、答案、耗时
- `%APPDATA%\Knowlib\bank_images\`：题库里插入的图片
- 便携模式：exe 同目录放 `portable.flag`，上述文件就放在 exe 旁边

## 常见问题

**Q：会把我的屏幕截图传出去吗？**
不会。截图只在本机做 OCR，只有**识别出来的文字**会发给你自己配置的 AI；开「图片作答」时才会上传那张区域截图。

**Q：必须用 DeepSeek 吗？**
不必须。任何 OpenAI 兼容接口都能用；想完全离线就本地跑 Ollama + qwen2.5vl。

**Q：识别不准怎么办？**
先框小一点，只框题干和选项；或在设置里把「图片作答」改成「自动/总是用图片」，让多模态模型直接看图。

**Q：会被检测吗？**
本项目只是屏幕取词 + 调 API，不做任何注入或模拟操作。是否允许使用请遵守你所在平台的规定。

**Q：费用多少？**
软件免费开源。AI 调用按你自己的服务商计费；本地题库命中时完全不消耗 token。

## 从源码运行

```bash
pip install PyQt5 mss Pillow openai rapidocr_onnxruntime pynput
pip install "numpy<2" "onnxruntime==1.19.2" "opencv-python==4.10.0.84"
python app.py
```

> ⚠️ 必须让 `rapidocr_onnxruntime` 在 `PyQt5` **之前**导入，否则 onnxruntime 与 Qt 的 DLL 加载顺序冲突会直接 `0xC0000005` 崩溃。

## 打包

```bash
pyinstaller --noconfirm --clean --windowed --name Knowlib --icon app.ico ^
  --collect-all rapidocr_onnxruntime --collect-all onnxruntime ^
  --hidden-import openai --hidden-import mss --hidden-import pynput app.py
```

安装包用 Inno Setup 编译 `installer_knowlib.iss`。

## 隐私说明

- 截图只在本机做 OCR；只有识别出的文字（或你开启图片作答时的那张截图）会发给你自己配置的 AI
- 本项目不收集任何数据，也不内置任何 Key
- AI 生成内容仅供参考，请自行核对

## English

**Knowlib PC** is a Windows desktop screenshot-to-answer assistant: select a screen
region, and it watches it — when the question changes, it OCRs locally and asks
your own AI (DeepSeek / OpenAI-compatible / local Ollama), then shows the answer
in a floating always-on-top overlay. Built-in local question bank, history, and
MD/PDF export. MIT licensed.

## License

MIT

## 更新日志

### v1.3.9
- 答案窗**字体颜色可改**：答案颜色、解析颜色两组色板 + 取色器
- 修复**解析字号调整看不出效果**（Markdown 渲染里写死了 13px，覆盖了设置）
- 视觉模型改成**可选择的预置列表**，切服务商自动带出推荐值
- 历史记录 / 题库的预览正文颜色加深、字号加大，不再发灰

### v1.3.8
- 弹窗全部重做：自绘深色卡片 + 投影 + 图标圆点，危险操作红色按钮
- 题库「删除这条」补上二次确认

### v1.3.7
- 答案窗新增**高度**滑杆与「高度自适应」；字号拆成**答案字号 / 解析字号**
- 题库支持**直接编辑**（双击进入）、新建题目、解析里**插入图片**
- DeepSeek 预置视觉模型改为 `deepseek-flash`

### v1.3.6
- 答案窗外观调整**实时生效并自动保存**，不用再点保存
- 答案窗开着时直接改真窗口；没开着就在上次位置弹示例卡

### v1.3.5
- 历史记录支持 Markdown/公式渲染与多选入库
- 题库支持 CSV / MD / TXT / JSON 多格式导入
- 服务商预置扩展到 7 家（自动填端点、模型、视觉模型）
- 答案窗外观实时预览

### v1.3.4
- OCR 不可靠时改用**图片直接作答**（多模态），失败自动回退文字

### v1.3.3
- 答案窗可调外观 + 选择题高亮 + token 用量显示 + 纠错覆盖题库

### v1.1.0
- 自动更新改走**国内直连**：优先读 `https://knowlib.fun/download/version.json`，失败才回退 GitHub
- 下载也优先从官网拉（`Knowlib-PC-latest.exe`），GitHub 作为备用

### v1.0.9
- 标题栏改用 Knowlib 官方白色线条 logo（不再用字母 K）
- 托盘图标使用官方 app 图标
- 官网提供国内直连下载：https://knowlib.fun/download/Knowlib-PC-latest.exe

### v1.0.8
- 修复：弹窗（更新确认 / 保存提示 / 清空确认）在深色主题下看不清的问题
- 所有消息框统一深色样式，默认按钮高亮

### v1.0.7
- **数据不再放安装目录**：配置、题库、历史统一迁到 `%APPDATA%\Knowlib`
  - 升级 / 卸载 / 换安装路径都不会丢数据（首次运行自动从旧位置复制，只复制不删除）
  - 想便携使用：在 exe 同目录放一个空的 `portable.flag`，数据就跟着程序走

### v1.0.6
- 新增**本地题库**：答过的题自动入库（SQLite），下次同题/近似题秒回答案，不消耗 token
  - 精确匹配 + 模糊匹配（抗 OCR 错字，相似度 ≥0.86）
  - 题库窗：搜索、复制、删除、CSV 导入导出、命中统计
- 新增**自动更新**：启动时检查 GitHub 最新版，右上角出现绿色「更新」按钮，一键下载并静默安装
- 设置里可开关「优先查本地题库」「启动时检查更新」

### v1.0.5
- 全新界面外壳：无边框窗口 + 自绘标题栏（可拖动）+ 圆角卡片 + 状态灯
- 开始/停止按钮随状态切换配色（蓝 → 红）
- 监控区域改成右上角小标签，日志区更大
- 答案窗圆角、渐变底色同步换新

### v1.0.4
- 新增**历史记录窗**：左侧列表 + 右侧题目/答案，支持关键词搜索、单条复制、清空
- 新增**系统托盘**：关闭窗口不再退出，后台继续监控；托盘菜单可开始/停止、开关答案窗、看历史
- 新增**开机自启**（设置里勾选，静默进托盘，不弹窗）
- 新增**单实例**：重复启动只会把已有窗口叫出来
- 答案窗**记住拖动后的位置**，下次直接出现在你放的地方
- 修复「复制」只复制解析、不复制答案的问题

### v1.0.3
- 答案独立大字号显示，解析单独排版
- 同一道题不重复请求（省 token）

### v1.0.2
- 画面没变化就不 OCR、不请求，自动降频省 CPU

### v1.0.1
- 答案窗避开监控区域，不再每秒闪烁
