# 小红书视频转录工具 (xhs-distill)

批量下载小红书视频并转录为中文文字稿，用于博主说话风格蒸馏、内容分析、字幕生成等场景。

基于 `yt-dlp` + `ffmpeg` + `faster-whisper`（OpenAI Whisper），本地运行，无需 API Key。

## 工作流程

```
小红书分享链接
  │
  ▼ yt-dlp -f ba/b -x                # 优先音频流；无独立音轨时下载视频并提取
  │
  ▼ ffmpeg -ar 16000 -ac 1            # 转为 16kHz 单声道 WAV
  │
  ▼ Python wave → numpy array         # 读取 WAV（绕过 av 兼容性问题）
  │
  ▼ faster-whisper (CPU + int8)       # 语音转文字
  │
  ▼ .txt + .srt + _timestamped.txt    # 输出三种格式
```

## 快速开始

### 1. 安装

```bash
git clone <repo-url>
cd xhs-distill
./install.sh
```

`install.sh` 在项目虚拟环境中安装 Python 依赖。需自行安装 ffmpeg 并加入 PATH。

<details>
<summary>手动安装（如果 install.sh 不适用）</summary>

```bash
pip3 install -r requirements.txt

# macOS:  brew install ffmpeg
# Ubuntu: sudo apt install ffmpeg
# 或从 https://ffmpeg.org/download.html 下载
```

</details>

### 2. 获取小红书视频链接

在小红书 App 中：
1. 打开博主的**视频**笔记
2. 点击右上角「分享」→「复制链接」
3. 链接需包含 `xsec_token` 参数（App 分享时自动生成）
4. URL 中 `type=video` 表示视频笔记（图文笔记无法转录）

### 3. 运行

```bash
# 方式一：编辑 urls/urls.txt 添加链接，然后运行
./run.sh

# 方式二：直接传链接（支持粘贴完整 App 分享文案）
./run.sh "https://www.xiaohongshu.com/discovery/item/xxxxx?type=video&xsec_token=yyyy"

# 方式三：批量传多个链接
./run.sh "链接1" "链接2" "链接3"

# 保留音频文件（默认转录后自动清理）
./run.sh --keep-audio "链接"

# 转录已有文件（跳过下载）
./run.sh --skip-download
```

## 目录结构

```
xhs-distill/
├── install.sh                 # 一键安装脚本
├── run.sh                     # 一键运行脚本
├── requirements.txt           # Python 依赖
├── .gitignore
├── scripts/
│   ├── batch_transcribe.py    # 核心脚本：下载 + 转录 + 输出
│   └── merge_transcripts.py   # 合并所有文字稿为 all_transcripts.md
├── urls/
│   └── urls.txt               # 小红书视频链接列表（编辑此文件）
├── bin/                       # 可选的本地 ffmpeg 二进制（不会提交）
├── audio/                     # 下载的音频文件（转录后自动清理）
└── transcripts/               # 转录输出
    ├── <视频ID>.txt            #   纯文本稿
    ├── <视频ID>.srt            #   SRT 字幕格式
    └── <视频ID>_timestamped.txt  # 带时间戳的逐段文本
```

## 输出格式

每个视频输出三种格式：

| 格式 | 后缀 | 说明 |
|------|------|------|
| 纯文本 | `.txt` | 包含元信息头 + 逐行文字，适合蒸馏分析 |
| SRT 字幕 | `.srt` | 标准字幕格式，可用于视频播放器 |
| 时间戳文本 | `_timestamped.txt` | `[MM:SS.ss] 文本` 格式，方便定位 |

多个视频转录完成后自动合并为 `transcripts/all_transcripts.md`。

## 模型选择

| 模型 | 大小 | 速度 (M 芯片) | 中文准确率 | 适用场景 |
|------|------|---------------|-----------|----------|
| tiny | ~75MB | 极快 | 低 | 快速预览 |
| base | ~145MB | 快 | 一般 | 粗略转录 |
| small | ~480MB | ~4x 实时 | 不错 | 速度优先 |
| medium | ~1.5GB | ~2x 实时 | 好 | **推荐：平衡选择** |
| large-v3 | ~3GB | ~1x 实时 | 最好 | 精度优先 |

```bash
# 指定模型（默认 large-v3）
./run.sh --model medium "链接"
python3 scripts/batch_transcribe.py --model small "链接"
```

首次使用时模型会自动从 HuggingFace 镜像站下载，之后缓存在本地。

## 技术细节

### ffmpeg 音频提取

优先选择独立音频流；若来源只提供合并的音视频流，仍需下载完整视频再提取音频。实际流量取决于来源提供的格式。

### av 19.x 兼容性问题

faster-whisper 1.2.1 内部调用 `av.open()` 时传入了 `metadata_errors` 参数，但 av 19.x 已移除该参数，导致直接传文件路径会报 `TypeError`。

解决方案：
1. 用 ffmpeg 将音频转为标准 WAV 格式（16kHz mono）
2. 用 Python 内置 `wave` 模块读取 WAV 为 numpy array
3. 将 numpy array 直接传给 `model.transcribe()`，完全绕过 av

### 代理处理

脚本沿用系统代理设置。若需使用镜像，可自行设置 `HF_ENDPOINT` 环境变量。

## 已知限制

- 仅支持视频笔记，不支持图文笔记（URL 中需有 `type=video`）
- 小红书链接的 `xsec_token` 有时效性，过期后需重新分享获取
- Apple Silicon (M 系列) 上使用 CPU 推理，速度约 2-4x 实时（取决于模型）
- 首次下载 large-v3 模型约 3GB，建议首次使用 medium 模型

## 环境要求

- Python 3.10+
- ffmpeg（自行安装并加入 PATH）
- 网络连接（下载视频 + 首次下载模型）

## License

MIT

## 发布与使用边界

仅处理自己拥有权利或已获授权的视频。遵守平台规则，不使用账号凭据、Cookie 或绕过访问控制。不要将下载的音视频、转录稿、带 token 的分享链接提交到公开仓库。转录可能有错漏，应人工核对。
