#!/usr/bin/env python3
"""
小红书视频批量下载 + 转文字稿工具 (ffmpeg 优化版)

优化点：
  - 优先选择独立音频流；来源不提供时回退到视频流
  - 用 ffmpeg 预转换为 16kHz mono WAV，绕过 av 19.x 兼容性问题
  - 不再需要 transcribe_direct.py hack

用法:
  python3 batch_transcribe.py                          # 从 urls/urls.txt 读取链接
  python3 batch_transcribe.py URL1 URL2 ...             # 直接传链接
  python3 batch_transcribe.py --model large-v3         # 指定模型(默认 large-v3)
  python3 batch_transcribe.py --model small            # 用更小的模型(更快但精度低)
  python3 batch_transcribe.py --skip-download           # 转录 audio/ 目录下已有文件
"""

import os
import sys
import subprocess
import argparse
import re
import time
import hashlib
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime

# 项目根目录
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
AUDIO_DIR = PROJECT_DIR / "audio"
TRANSCRIPT_DIR = PROJECT_DIR / "transcripts"
URLS_FILE = PROJECT_DIR / "urls" / "urls.txt"
FFMPEG_BIN = PROJECT_DIR / "bin" / "ffmpeg"

AUDIO_DIR.mkdir(parents=True, exist_ok=True)
TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)


def get_ffmpeg_path():
    """返回 ffmpeg 二进制路径（优先用项目内置的 bin/ffmpeg）"""
    if FFMPEG_BIN.exists() and os.access(FFMPEG_BIN, os.X_OK):
        return str(FFMPEG_BIN)
    # fallback: 系统 PATH 中的 ffmpeg
    result = subprocess.run(["which", "ffmpeg"], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def read_urls_from_file(filepath):
    """从文件读取 URL 列表，忽略空行和注释。
    支持直接粘贴小红书 App 分享文本（自动提取其中的 URL）。
    """
    urls = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                # 从分享文本中提取 URL
                extracted = extract_urls_from_text(line)
                urls.extend(extracted)
    return urls


def extract_urls_from_text(text):
    """从文本中提取小红书 URL（支持 App 分享文案中夹杂的链接）。
    小红书分享格式通常是：
      "标题文案 https://www.xiaohongshu.com/explore/xxx?xsec_token=yyy&xsec_source=pc_feed"
    """
    # 匹配完整的小红书 URL（含 query 参数）
    pattern = r'https?://(?:www\.)?(?:xiaohongshu\.com|xhslink\.com)/[^\s]+'
    return re.findall(pattern, text)


def extract_id_from_url(url):
    """从小红书 URL 中提取笔记 ID 作为文件名"""
    match = re.search(r"/explore/([a-f0-9]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"/discovery/item/([a-f0-9]+)", url)
    if match:
        return match.group(1)
    # xhslink 短链
    match = re.search(r"xhslink\.com/([a-zA-Z0-9]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"xsec_token=([a-zA-Z0-9]+)", url)
    if match:
        return match.group(1)[:16]
    # fallback: 用 URL 的 hash
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def validate_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"www.xiaohongshu.com", "xiaohongshu.com", "xhslink.com", "www.xhslink.com"}:
        raise ValueError("只支持 HTTPS 小红书分享链接")
    return url


def download_audio(url, output_dir, ffmpeg_path):
    """用 yt-dlp 优先下载独立音频流，必要时回退到视频流。

    与旧方案的区别：
      旧: yt-dlp -f b 下载完整 mp4（55MB），再用 av 解码
      新: yt-dlp -f ba/b -x，独立音频不可用时仍需下载视频
    """
    video_id = extract_id_from_url(url)
    output_template = str(output_dir / f"{video_id}.%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "ba/b",                  # 优先音频流，否则下载视频再提取
        "-x",
        "--audio-format", "m4a",        # 转换为 m4a 格式
        "--audio-quality", "0",         # 最佳音频质量
        "--ffmpeg-location", ffmpeg_path,  # 指定 ffmpeg 路径
        "-o", output_template,
        "--no-playlist",
        "--print", "after_move:filepath",
        "--print", "title",
        url,
    ]

    print(f"  [下载] 正在处理笔记: {video_id}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        print("  [下载失败] 请检查链接、网络及 yt-dlp 版本")
        # 如果音频提取失败，尝试下载完整视频
        print(f"  [重试] 尝试下载完整视频文件...")
        cmd_fallback = [
            sys.executable, "-m", "yt_dlp",
            "-f", "b",
            "-o", output_template,
            "--no-playlist",
            "--print", "after_move:filepath",
            "--print", "title",
            url,
        ]
        result = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print("  [下载失败] 回退方案也未成功")
            return None, None

    # yt-dlp 的 --print 输出: 文件路径和标题
    lines = result.stdout.strip().split("\n")
    audio_path = None
    title = video_id
    for line in lines:
        line = line.strip()
        if line.startswith("/") and Path(line).exists():
            audio_path = line
        elif not line.startswith("/") and not line.startswith("[") and not line.startswith("WARNING"):
            title = line

    if audio_path and Path(audio_path).exists():
        file_size = Path(audio_path).stat().st_size
        print(f"  [下载完成] {audio_path} ({file_size / 1024 / 1024:.1f}MB)")
        return audio_path, title
    else:
        # 兜底: 按约定路径找
        for ext in [".m4a", ".mp4", ".webm", ".mkv", ".flv", ".mp3", ".aac", ".wav"]:
            candidate = output_dir / f"{video_id}{ext}"
            if candidate.exists():
                print(f"  [下载完成] {candidate}")
                return str(candidate), title
        print(f"  [下载失败] 未找到音频文件")
        return None, None


def convert_to_wav(input_path, output_dir, ffmpeg_path):
    """用 ffmpeg 将音频文件转换为 16kHz 单声道 WAV（Whisper 标准输入格式）。

    这一步绕过了 faster-whisper 内部 av 19.x 的兼容性问题：
    faster-whisper 可以直接读取标准 WAV 文件，无需通过 av 解码。

    参数:
        input_path: 输入文件路径（m4a/mp4/mp3 等）
        output_dir: WAV 输出目录
        ffmpeg_path: ffmpeg 二进制路径

    返回:
        WAV 文件路径，失败返回 None
    """
    input_file = Path(input_path)
    if input_file.suffix.lower() == ".wav":
        return str(input_file)
    wav_path = output_dir / f"{input_file.stem}.wav"

    # 如果 WAV 已存在且比源文件新，跳过转换
    if wav_path.exists() and wav_path.stat().st_mtime >= input_file.stat().st_mtime:
        print(f"  [转换] WAV 已存在，跳过: {wav_path.name}")
        return str(wav_path)

    cmd = [
        ffmpeg_path,
        "-i", str(input_path),
        "-ar", "16000",      # 采样率 16kHz（Whisper 要求）
        "-ac", "1",          # 单声道
        "-f", "wav",         # WAV 格式
        "-y",                # 覆盖已存在文件
        str(wav_path),
    ]

    print(f"  [转换] 提取音频为 WAV: {input_file.name} -> {wav_path.name}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        print(f"  [转换失败] {result.stderr[-300:]}")
        return None

    wav_size = wav_path.stat().st_size
    print(f"  [转换完成] {wav_path.name} ({wav_size / 1024 / 1024:.1f}MB)")
    return str(wav_path)


def transcribe_audio(audio_path, model_size="large-v3", language="zh"):
    """用 faster-whisper 将音频转录为文字。

    由于 faster-whisper 1.2.1 + av 19.x 存在兼容性问题（av.open() 不接受 metadata_errors 参数），
    本函数会先尝试直接转录，如果失败则用 Python 内置模块解码 WAV 为 numpy array 后再转录。
    """
    from faster_whisper import WhisperModel

    print(f"  [转录] 加载模型 {model_size} ...")
    model = WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",  # int8 量化，速度快内存省
    )

    # 对于 WAV 文件，直接用 Python wave 模块读取（完全绕过 av）
    if audio_path.lower().endswith(".wav"):
        audio_data = _read_wav_as_numpy(audio_path)
        if audio_data is not None:
            print(f"  [转录] 开始转录 (WAV → numpy, 绕过 av) ...")
            return _transcribe_numpy(model, audio_data, language)

    # 非 WAV 文件，先尝试 faster-whisper 直接读取
    print(f"  [转录] 开始转录 {Path(audio_path).name} ...")
    start_time = time.time()

    try:
        segments, info = model.transcribe(
            audio_path,
            language=language,
            beam_size=5,
            vad_filter=True,  # 过滤静音段
            vad_parameters={"min_silence_duration_ms": 500},
        )

        print(f"  [转录] 检测到语言: {info.language}, 概率: {info.language_probability:.2%}")
        print(f"  [转录] 音频时长: {info.duration:.1f}s")

        segments_list = list(segments)
        elapsed = time.time() - start_time
        print(f"  [转录] 完成! 耗时 {elapsed:.1f}s (速度: {info.duration/elapsed:.1f}x 实时)")

        return segments_list, info

    except TypeError as e:
        if "metadata_errors" in str(e) or "open()" in str(e):
            # av 兼容性问题，改用 numpy 方式
            print(f"  [转录] av 兼容性问题，改用 numpy 方式解码...")
            audio_data = _decode_with_av(audio_path)
            return _transcribe_numpy(model, audio_data, language)
        raise


def _read_wav_as_numpy(wav_path):
    """用 Python 内置 wave 模块读取 WAV 文件为 numpy array。
    完全不依赖 av 库，绕过 av 19.x 兼容性问题。

    要求 WAV 文件已经是 16kHz 单声道格式（由 ffmpeg 转换保证）。
    """
    import wave
    import numpy as np

    try:
        with wave.open(wav_path, "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            raw_data = wf.readframes(n_frames)

        print(f"  [解码] WAV: {channels}ch, {sample_width}B, {framerate}Hz, {n_frames} frames")

        # 转换为 numpy array
        if sample_width == 2:
            audio_array = np.frombuffer(raw_data, dtype=np.int16)
        elif sample_width == 4:
            audio_array = np.frombuffer(raw_data, dtype=np.int32)
        elif sample_width == 1:
            audio_array = np.frombuffer(raw_data, dtype=np.uint8)
            audio_array = (audio_array.astype(np.float32) - 128) / 128.0
            return audio_array
        else:
            print(f"  [解码] 不支持的采样宽度: {sample_width}")
            return None

        # 转为 float32 归一化
        if audio_array.dtype == np.int16:
            audio_array = audio_array.astype(np.float32) / 32768.0
        elif audio_array.dtype == np.int32:
            audio_array = audio_array.astype(np.float32) / 2147483648.0

        # 如果多声道，取第一声道
        if channels > 1:
            audio_array = audio_array[::channels]

        print(f"  [解码] 完成: {len(audio_array)} samples, {len(audio_array)/framerate:.1f}s")
        return audio_array

    except Exception as e:
        print(f"  [解码] WAV 读取失败: {e}")
        return None


def _transcribe_numpy(model, audio_array, language):
    """用 numpy array 方式调用 faster-whisper 转录。"""
    import time as _time

    print(f"  [转录] 开始转录 (numpy array, {len(audio_array)} samples) ...")
    start_time = _time.time()

    segments, info = model.transcribe(
        audio_array,
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )

    print(f"  [转录] 检测到语言: {info.language}, 概率: {info.language_probability:.2%}")
    print(f"  [转录] 音频时长: {info.duration:.1f}s")

    segments_list = list(segments)
    elapsed = _time.time() - start_time
    print(f"  [转录] 完成! 耗时 {elapsed:.1f}s (速度: {info.duration/elapsed:.1f}x 实时)")

    return segments_list, info


def _decode_with_av(audio_path):
    """用 av 手动解码音频为 numpy array（fallback 方案）。
    仅在文件不是 WAV 格式且 faster-whisper 直接读取失败时使用。
    """
    import av
    import numpy as np

    container = av.open(audio_path)
    audio_stream = None
    for stream in container.streams:
        if stream.type == "audio":
            audio_stream = stream
            break

    if audio_stream is None:
        container.close()
        raise ValueError("未找到音频流")

    resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
    chunks = []
    for frame in container.decode(audio_stream):
        resampled = resampler.resample(frame)
        for r in resampled:
            arr = r.to_ndarray()
            chunks.append(arr)
    container.close()

    flat_chunks = []
    for arr in chunks:
        if arr.ndim > 1:
            arr = arr[0]
        flat_chunks.append(arr)

    audio_array = np.concatenate(flat_chunks, axis=0)
    if audio_array.dtype == np.int16:
        audio_array = audio_array.astype(np.float32) / 32768.0
    elif audio_array.dtype == np.int32:
        audio_array = audio_array.astype(np.float32) / 2147483648.0

    return audio_array


def save_transcript(segments, info, title, video_id, output_dir, source_url, model_name=""):
    """将转录结果保存为多种格式的文件"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 纯文本（方便后续蒸馏分析）
    txt_path = output_dir / f"{video_id}.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"# 标题: {title}\n")
        f.write(f"# 来源笔记: {video_id}\n")
        f.write(f"# 转录时间: {timestamp}\n")
        f.write(f"# 音频时长: {info.duration:.1f}s\n")
        f.write(f"# 语言: {info.language} (概率: {info.language_probability:.2%})\n")
        if model_name:
            f.write(f"# 模型: {model_name}\n")
        f.write(f"{'='*60}\n\n")
        for seg in segments:
            f.write(seg.text.strip() + "\n")

    # 带时间戳的 SRT 字幕格式
    srt_path = output_dir / f"{video_id}.srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            start = format_timestamp(seg.start)
            end = format_timestamp(seg.end)
            f.write(f"{i}\n")
            f.write(f"{start} --> {end}\n")
            f.write(f"{seg.text.strip()}\n\n")

    # 带时间戳的逐段文本（方便定位）
    ts_path = output_dir / f"{video_id}_timestamped.txt"
    with open(ts_path, "w", encoding="utf-8") as f:
        f.write(f"# 标题: {title}\n")
        f.write(f"# 来源笔记: {video_id}\n")
        f.write(f"{'='*60}\n\n")
        for seg in segments:
            mins = int(seg.start // 60)
            secs = seg.start % 60
            f.write(f"[{mins:02d}:{secs:05.2f}] {seg.text.strip()}\n")

    print(f"  [保存] 纯文本: {txt_path.name}")
    print(f"  [保存] 字幕SRT: {srt_path.name}")
    print(f"  [保存] 时间戳: {ts_path.name}")
    return txt_path


def format_timestamp(seconds):
    """将秒数格式化为 SRT 时间戳格式 HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def main():
    parser = argparse.ArgumentParser(description="小红书视频批量下载 + 转文字稿 (ffmpeg 优化版)")
    parser.add_argument("urls", nargs="*", help="小红书视频链接(不填则从 urls/urls.txt 读取)")
    parser.add_argument("--model", default="large-v3", choices=["tiny", "base", "small", "medium", "large-v3"], help="Whisper 模型大小 (默认 large-v3)")
    parser.add_argument("--language", default="zh", help="语言代码 (默认 zh)")
    parser.add_argument("--skip-download", action="store_true", help="跳过下载，直接转录 audio/ 目录下已有文件")
    parser.add_argument("--keep-audio", action="store_true", help="保留音频文件（默认转录后删除以节省空间）")
    args = parser.parse_args()

    # 获取 ffmpeg 路径
    ffmpeg_path = get_ffmpeg_path()
    if ffmpeg_path:
        print(f"[ffmpeg] 使用: {ffmpeg_path}")
    else:
        print("[警告] 未找到 ffmpeg，将下载完整视频文件")

    if not ffmpeg_path:
        parser.error("需要 ffmpeg，请先安装并加入 PATH")

    # 获取任务列表
    if args.skip_download:
        # 直接处理 audio 目录下的文件（支持音频和视频格式）
        audio_files = []
        for ext in ["*.m4a", "*.mp3", "*.wav", "*.mp4", "*.webm", "*.mkv", "*.flv", "*.aac", "*.ogg"]:
            audio_files.extend(sorted(AUDIO_DIR.glob(ext)))
        if not audio_files:
            print("audio/ 目录下没有找到音频/视频文件")
            return
        tasks = [(str(f), f.stem) for f in audio_files]
        url_mode = False
    else:
        # 从命令行参数或 urls.txt 读取
        raw_inputs = args.urls if args.urls else []
        urls = []
        for raw in raw_inputs:
            # 命令行传入的可能是完整的分享文本，从中提取 URL
            extracted = extract_urls_from_text(raw)
            if extracted:
                urls.extend(extracted)
            elif raw.startswith("http"):
                urls.append(raw)

        # 如果命令行没有 URL，从文件读取
        if not urls:
            urls = read_urls_from_file(URLS_FILE)

        if not urls:
            print(f"未找到 URL。请在 {URLS_FILE} 中添加小红书视频链接，或直接在命令行传入。")
            print(f"示例: python3 {sys.argv[0]} https://www.xiaohongshu.com/explore/xxxxx")
            print(f"也可以直接粘贴 App 分享文本，脚本会自动提取其中的链接。")
            return
        tasks = [(validate_url(url), extract_id_from_url(url)) for url in dict.fromkeys(urls)]
        url_mode = True

    print(f"\n{'='*60}")
    print(f"小红书视频批量转录工具 (ffmpeg 优化版)")
    print(f"共 {len(tasks)} 个任务 | 模型: {args.model} | 语言: {args.language}")
    print(f"{'='*60}\n")

    success_count = 0
    fail_count = 0
    results = []

    for i, (url_or_path, task_id) in enumerate(tasks, 1):
        print(f"\n--- [{i}/{len(tasks)}] ---")

        if url_mode:
            # 下载阶段：优先独立音频流
            audio_path, title = download_audio(url_or_path, AUDIO_DIR, ffmpeg_path)
            if audio_path is None:
                fail_count += 1
                results.append((task_id, "下载失败", None))
                continue
            source_url = url_or_path
        else:
            audio_path = url_or_path
            title = task_id
            source_url = "(本地文件)"

        # 转换阶段：用 ffmpeg 转为 16kHz mono WAV
        wav_path = None
        if ffmpeg_path:
            wav_path = convert_to_wav(audio_path, AUDIO_DIR, ffmpeg_path)

        # 转录阶段：优先用 WAV，否则用原文件
        transcribe_input = wav_path if wav_path else audio_path
        try:
            segments, info = transcribe_audio(transcribe_input, args.model, args.language)
            txt_path = save_transcript(segments, info, title, task_id, TRANSCRIPT_DIR, source_url, args.model)
            success_count += 1
            results.append((task_id, "成功", str(txt_path)))
        except Exception as e:
            print(f"  [转录失败] {type(e).__name__}")
            fail_count += 1
            results.append((task_id, "转录失败", None))

        # 清理临时文件
        if not args.keep_audio:
            # 删除原始下载文件（m4a 或 mp4）
            if url_mode and Path(audio_path).exists():
                Path(audio_path).unlink()
                print(f"  [清理] 已删除: {Path(audio_path).name}")
            # 删除 WAV 文件
            if wav_path and Path(wav_path).exists() and Path(wav_path) != Path(audio_path):
                Path(wav_path).unlink()
                print(f"  [清理] 已删除: {Path(wav_path).name}")

    # 汇总报告
    print(f"\n{'='*60}")
    print(f"批量处理完成!")
    print(f"成功: {success_count} | 失败: {fail_count} | 总计: {len(tasks)}")
    print(f"文字稿目录: {TRANSCRIPT_DIR}")
    print(f"{'='*60}\n")

    for url, status, path in results:
        marker = "[OK]" if status == "成功" else "[FAIL]"
        print(f"  {marker} {status} | {url}")
        if path:
            print(f"        -> {path}")

    # 如果有多个成功，自动合并
    if success_count > 1:
        merge_script = SCRIPT_DIR / "merge_transcripts.py"
        if merge_script.exists():
            print(f"\n>>> 合并文字稿...")
            subprocess.run([sys.executable, str(merge_script)], cwd=str(PROJECT_DIR))


if __name__ == "__main__":
    main()
