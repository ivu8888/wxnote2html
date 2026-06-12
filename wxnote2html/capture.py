"""
ADB 截图 + 自动滚动模块
支持通过 USB 或 WiFi 连接的 Android 设备
"""

import os
import shutil
import subprocess
import sys
import time
import tempfile
from pathlib import Path
from io import BytesIO

# 强制 UTF-8 输出，解决 Windows bash 中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
from PIL import Image

# 常见 ADB 路径（Windows）
_ADB_CANDIDATES = [
    r"C:\Program Files\MuMu Player 12\nx_main\adb.exe",
]


def _find_adb() -> str:
    """查找 adb.exe，优先使用已知路径，否则回退到 PATH"""
    for candidate in _ADB_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which("adb")
    if found:
        return found
    return "adb"  # 最后的兜底


class ADBCapture:
    """通过 ADB 控制手机自动滚动截图"""

    def __init__(self, device_serial: str | None = None):
        """
        Args:
            device_serial: 设备序列号，None 则自动检测唯一设备
        """
        self.serial = device_serial
        self._base_cmd = [_find_adb()]
        if device_serial:
            self._base_cmd += ["-s", device_serial]

    @staticmethod
    def list_devices() -> list[str]:
        """列出所有连接的设备"""
        result = subprocess.run(
            [_find_adb(), "devices"], capture_output=True, text=True
        )
        devices = []
        for line in result.stdout.strip().split("\n")[1:]:
            if line.strip() and "\tdevice" in line:
                devices.append(line.split("\t")[0])
        return devices

    def _adb(self, *args, text: bool = True) -> subprocess.CompletedProcess:
        cmd = self._base_cmd + list(args)
        return subprocess.run(cmd, capture_output=True, text=text)

    def get_screen_size(self) -> tuple[int, int]:
        """获取屏幕分辨率 (宽, 高)"""
        result = self._adb("shell", "wm", "size")
        # output 可能多行，逐行找含 "x" 的行
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if "x" in line and not line.startswith("error"):
                size_str = line.split(":")[-1].strip()
                w, h = size_str.split("x")
                return int(w), int(h)
        raise RuntimeError(
            f"无法解析屏幕尺寸, raw output:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def screenshot(self) -> Image.Image:
        """截取当前屏幕，返回 PIL Image"""
        result = self._adb("exec-out", "screencap", "-p", text=False)
        if result.returncode != 0:
            raise RuntimeError(f"截图失败: {result.stderr}")
        return Image.open(BytesIO(result.stdout))

    @staticmethod
    def images_similar(img1: Image.Image, img2: Image.Image, threshold: float = 0.98) -> bool:
        """
        判断两张图是否几乎相同（用于检测是否已滚到底）
        比较缩小后的像素差异比例
        """
        # 缩小到 200px 宽比较，速度快
        w, h = 200, int(200 / img1.width * img1.height)
        a = np.array(img1.resize((w, h)).convert("L"), dtype=np.float32)
        b = np.array(img2.resize((w, h)).convert("L"), dtype=np.float32)
        diff = np.abs(a - b).mean() / 255
        return diff < (1 - threshold)

    def scroll_down(self, distance: int | None = None, duration_ms: int = 500) -> int:
        """
        向下滚动，确保起止坐标均在屏幕内，避免因越界截断导致滚动量不准。

        Returns:
            实际滚动距离 (px)
        """
        w, h = self.get_screen_size()
        start_x = w // 2
        start_y = int(h * 0.9)
        end_y = max(int(h * 0.1), start_y - (distance or int(h * 0.75)))
        actual_distance = start_y - end_y

        self._adb(
            "shell", "input", "swipe",
            str(start_x), str(start_y),
            str(start_x), str(end_y),
            str(duration_ms)
        )
        return actual_distance

    def capture_scroll(
        self,
        max_screens: int = 30,
        overlap_ratio: float = 0.4,
        scroll_distance: int | None = None,
        pause_ms: int = 800,
        save_dir: str | Path | None = "tmp",
        debug: bool = False,
    ) -> tuple[list[Image.Image], list[int]]:
        """
        自动滚动并逐屏截图，直到到达底部或达到最大张数。

        Args:
            save_dir: 每截一张图保存到此目录，None 则不保存
            debug: 写入详细日志到 save_dir/debug.log

        Returns:
            (截图列表, 每次滚动的实际距离列表, 长度 = len(screenshots)-1)
        """
        from datetime import datetime

        screen_w, screen_h = self.get_screen_size()

        if save_dir is not None:
            save_path = Path(save_dir)
            save_path.mkdir(parents=True, exist_ok=True)

        screenshots: list[Image.Image] = []
        scroll_distances: list[int] = []
        cumulative_scroll = 0  # 累计滚动高度

        # 先截第一张图，用于估算内容区域高度
        first_img = self.screenshot()
        screenshots.append(first_img)
        if save_dir is not None:
            first_img.save(save_path / "screen_000.png")

        # 从首张图估算内容高度，校准滚动距离
        content_h = _estimate_content_height(first_img)
        if scroll_distance is None:
            scroll_distance = int(content_h * (1 - overlap_ratio))
            # 限制范围，避免基于错误 header 估算的极端值
            scroll_distance = max(int(screen_h * 0.15), min(scroll_distance, int(screen_h * 0.85)))

        print(f"[capture] 开始截图... 屏幕={screen_w}×{screen_h}, "
              f"内容高度≈{content_h}px, 目标滚动={scroll_distance}px")

        if debug and save_dir is not None:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _capture_log(save_path,
                f"开始时间: {ts}",
                f"屏幕尺寸: {screen_w}×{screen_h}",
                f"内容高度≈{content_h}px",
                f"最大截图数: {max_screens}  目标滚动: {scroll_distance}px  暂停: {pause_ms}ms",
                "",
                mode="w",
            )
            _capture_log(save_path,
                f"[截屏 01] {save_dir}/screen_000.png  位置: y=0",
            )

        print(f"  [1] 已截 1 张")

        # 从第 2 张开始循环
        for i in range(1, max_screens):
            # 先滚动再截图（滚动发生在上一张截图之后）
            dist = self.scroll_down(distance=scroll_distance)
            scroll_distances.append(dist)
            cumulative_scroll += dist

            if debug and save_dir is not None:
                _capture_log(save_path,
                    f"[滚动 {i:02d}] 距离={dist}px  累计滚动={cumulative_scroll}px",
                )

            time.sleep(pause_ms / 1000.0)

            img = self.screenshot()
            screenshots.append(img)

            # ── 动态调整下次滚动距离 ──
            # 检查截图底部内容区是否切到文字，是则缩短、否则恢复
            target_scroll = int(content_h * (1 - overlap_ratio))
            if not _bottom_has_text(img):
                scroll_distance = min(
                    scroll_distance + int(scroll_distance * 0.10),
                    target_scroll,
                )
            else:
                scroll_distance = max(
                    scroll_distance - int(scroll_distance * 0.03),
                    int(screen_h * 0.50),
                )
                if debug and save_dir is not None:
                    _capture_log(save_path,
                        f"[动态] 切到文字, 下次滚动→{scroll_distance}px",
                    )

            if save_dir is not None:
                img.save(save_path / f"screen_{i:03d}.png")

            if debug and save_dir is not None:
                _capture_log(save_path,
                    f"[截屏 {i+1:02d}] {save_dir}/screen_{i:03d}.png  "
                    f"位置: y={cumulative_scroll}",
                )

            print(f"  [{i+1}] 已截 {len(screenshots)} 张")

            # 检查是否到达底部
            if self.images_similar(screenshots[-1], screenshots[-2], threshold=0.985):
                if debug and save_dir is not None:
                    _capture_log(save_path,
                        f"[截屏 {i+1:02d}] 与上一张相同 → 到达底部",
                    )
                # 丢弃重复截图，尝试用小滚动距离补截剩余内容
                screenshots.pop()
                scroll_distances.pop()
                cumulative_scroll -= dist
                if save_dir is not None:
                    (save_path / f"screen_{i:03d}.png").unlink(missing_ok=True)

                # 用缩小到 1/3 的滚动距离再试一次
                retry_scroll = max(scroll_distance // 3, int(screen_h * 0.1))
                retry_dist = self.scroll_down(distance=retry_scroll)
                time.sleep(pause_ms / 1000.0)
                retry_img = self.screenshot()

                if self.images_similar(retry_img, screenshots[-1], threshold=0.985):
                    print(f"[capture] 检测到已到达底部，停止截图")
                    if debug and save_dir is not None:
                        _capture_log(save_path,
                            f"[截屏 {i+1:02d}] 小滚动后仍相同 → 确认到达底部",
                        )
                    break

                # 小滚动截到新内容 → 追加为最后一张
                scroll_distances.append(retry_dist)
                cumulative_scroll += retry_dist
                screenshots.append(retry_img)
                if save_dir is not None:
                    retry_img.save(save_path / f"screen_{i:03d}.png")

                print(f"  [{i+1}] 小滚动({retry_scroll}px)补截剩余内容")
                if debug and save_dir is not None:
                    _capture_log(save_path,
                        f"[截屏 {i+1:02d}] 小滚动={retry_scroll}px 补截  位置: y={cumulative_scroll}",
                    )
                break

        print(f"[capture] 完成，共 {len(screenshots)} 张, 滚动序列={scroll_distances}")
        if save_dir is not None:
            print(f"[capture] 截图已保存到 {save_path.absolute()}")
        if debug and save_dir is not None:
            _capture_log(save_path,
                "",
                f"截图完成: {len(screenshots)} 张  总滚动: {cumulative_scroll}px",
            )
        return screenshots, scroll_distances


def _capture_log(save_dir: Path, *lines: str, mode: str = "a"):
    """向 debug.log 追加带时间戳的日志行（capture 模块独立版本）"""
    from datetime import datetime
    ts = datetime.now().strftime("%H:%M:%S")
    with open(save_dir / "debug.log", mode, encoding="utf-8") as f:
        for line in lines:
            f.write(f"[{ts}] {line}\n")


def _estimate_content_height(first_img: Image.Image) -> int:
    """
    从首张截图估算内容区域高度。

    用单图方差法检测顶部 header 边界，减去固定 footer 估算值。
    """
    gray = np.array(first_img.convert("L"), dtype=np.float32)
    H = first_img.height

    # 检测 header：从上往下找到第一个高方差行（非纯色 UI 区域结束处）
    max_scan = int(H * 0.15)
    header_h = 0
    for row in range(10, max_scan):
        if float(np.std(gray[row, :])) > 15:
            header_h = row
            break

    # footer 粗略估算（导航栏等）
    footer_est = min(80, int(H * 0.04))
    return H - header_h - footer_est


def _bottom_has_text(img: Image.Image) -> bool:
    """
    检查截图底部内容区是否切到了文字。
    查看底部 ~8% 区域（排除 footer），动态计算阈值。
    """
    gray = np.array(img.convert("L"), dtype=np.float32)
    H = gray.shape[0]

    check_h = max(int(H * 0.08), 60)
    row_stds = np.std(gray[-check_h:, :], axis=1)

    blank_baseline = float(np.percentile(row_stds, 15))
    text_threshold = max(blank_baseline * 2.5, 8.0)

    # 底部区域前半部分有高 std → 文字被切
    check_n = len(row_stds) // 2
    for i in range(check_n):
        if row_stds[i] > text_threshold:
            return True
    return False


def save_screenshots(images: list[Image.Image], output_dir: Path):
    """保存截图到目录（调试用）"""
    output_dir.mkdir(parents=True, exist_ok=True)
    for i, img in enumerate(images):
        path = output_dir / f"screen_{i:03d}.png"
        img.save(path)
    print(f"[capture] 已保存 {len(images)} 张截图到 {output_dir}")
