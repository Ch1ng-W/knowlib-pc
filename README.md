# Knowlib PC（桌面版）

> 截图 → 本地 OCR → AI 解题 → 悬浮窗给答案和解析。
> 和手机端 Knowlib 同一套思路，先做「搜题」这一块。

![platform](https://img.shields.io/badge/platform-Windows%2010%2F11-blue)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)

## 它能做什么

- **框一块屏幕区域**，自动盯着它；内容一变就识别
- **本地 OCR（RapidOCR）**，不联网也能识字，约 0.3–0.7 秒
- **调用你自己的 AI**（DeepSeek / OpenAI 兼容 / 本地 Ollama），出**答案 + 解析**
- **悬浮窗置顶**，半透明、可拖动、可复制、流式输出
- 截图前自动隐藏答案窗，不会把自己的答案拍进去造成死循环

## 快速开始

1. 下载 Releases 里的 `Knowlib-PC-x.y.z-setup.exe` 安装
2. 首次启动会弹设置：选服务商 → 填 API Key → 点 **测试连接**
   - DeepSeek：`https://api.deepseek.com/v1`，模型 `deepseek-chat`
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

- `config.json`：与 exe 同目录（写不进去则落到 `%APPDATA%\XiaoTuanSouTi`）
- `history.jsonl`：每次问答的题面、答案、耗时

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

安装包用 Inno Setup 编译 `installer.iss`。

## 隐私说明

- 截图只在本机做 OCR；**只有识别出的文字**会发给你自己配置的 AI 服务商
- 本项目不收集任何数据，也不内置任何 Key
- AI 生成内容仅供参考，请自行核对

## License

MIT

## 更新日志

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
