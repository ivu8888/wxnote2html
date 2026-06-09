"""
图片拼接模块
通过模板匹配找到相邻截图的重叠区域，拼接成一张长图。
所有阈值均按图像高度比例计算，兼容不同分辨率的设备。
"""

import numpy as np
from PIL import Image

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


# ── 比例常量（基于 2340px 参考屏验证，适配任意分辨率） ──

def _scale(h: int, ratio: float, floor: int = 10) -> int:
    """h * ratio, 保证不小于 floor"""
    return max(int(h * ratio), floor)


def _detect_top_header(
    images: list[Image.Image],
) -> int:
    """
    检测顶部固定UI区域（Android状态栏 + 微信标题栏）。

    信号1: 单图行内像素标准差（UI均匀=低，文字=高）
    信号2: 两图行间差异（UI相同=低，内容不同=高）
    综合评分后找拐点。
    """
    if len(images) < 2:
        return 0

    a = np.array(images[0].convert("L"), dtype=np.float32)
    b = np.array(images[1].convert("L"), dtype=np.float32)
    max_h = _scale(a.shape[0], 0.15, floor=50)

    row_std = np.std(a[:max_h], axis=1)
    row_diff = np.abs(a[:max_h] - b[:max_h]).mean(axis=1)

    std_norm = row_std / max(np.percentile(row_std, 95), 1)
    diff_norm = row_diff / max(np.percentile(row_diff, 95), 1)
    score = std_norm * 0.6 + diff_norm * 0.4

    peak_row = int(np.argmax(score))
    peak_val = score[peak_row]
    if peak_val < 0.15:
        return 0

    for row in range(peak_row - 1, max(0, peak_row - 30), -1):
        if score[row] < peak_val * 0.25:
            print(f"  [stitch] 顶部UI区域: {row + 1}px (峰值行={peak_row}, 评分={peak_val:.2f})")
            return row + 1

    print(f"  [stitch] 顶部UI区域: {peak_row}px (峰值行={peak_row}, 评分={peak_val:.2f})")
    return peak_row


def _detect_bottom_footer(
    last_image: Image.Image,
    max_ratio: float = 0.25,
) -> int:
    """
    检测底部空白+导航栏区域：从最后一张图的底部向上扫描，
    找到文章内容结束的位置。

    文章到底后，下方是纯白/纯色空白 + 导航栏，行内标准差极低。
    内容区域有文字/图片，标准差高。从底部向上找标准差跃升处。
    """
    gray = np.array(last_image.convert("L"), dtype=np.float32)
    H = gray.shape[0]
    max_h = _scale(H, max_ratio, floor=80)

    # 底部向上取 max_h 行，翻转使索引 0 = 最底部
    strip = gray[-max_h:][::-1]
    row_std = np.std(strip, axis=1)

    # 找从底部向上第一个 std 显著升高的位置
    # 空白区 std < 5, 内容区 std > 20
    for row in range(len(row_std)):
        if row_std[row] > 15:  # 碰到内容
            # 继续向前确认不是噪声
            if row + 2 < len(row_std) and row_std[row + 1] > 10:
                footer_h = row
                print(f"  [stitch] 底部空白区: {footer_h}px (行{H - max_h + row} std={row_std[row]:.1f})")
                return max(0, footer_h - 5)  # 稍微多保留一点

    # 未检测到明显边界，检查是否整个底部都是空白
    if np.mean(row_std) < 8:
        print(f"  [stitch] 底部空白区: 整个底部均匀 (avg std={np.mean(row_std):.1f})")
        return max_h

    return 0


def _template_match_overlap(
    img_top: np.ndarray,
    img_bottom: np.ndarray,
    min_overlap: int,
    expected_overlap: int,
    relaxed: bool = False,
) -> int | None:
    """
    模板匹配找重叠偏移量，搜索窗口围绕预期重叠值。
    """
    h_top = img_top.shape[0]
    h_bottom = img_bottom.shape[0]

    # 模板高度：取底部图顶部的 1/8~1/6 屏高
    strip_height = _scale(h_top, 0.08, floor=40)
    strip_height = min(strip_height, h_bottom, h_top)
    strip_height = max(strip_height, min_overlap)

    if h_bottom < strip_height or h_top < strip_height:
        return None

    template = img_bottom[:strip_height, :]

    # 搜索窗口
    if relaxed:
        margin = max(expected_overlap, _scale(h_top, 0.10, floor=80))
    else:
        margin = max(expected_overlap // 3, _scale(h_top, 0.04, floor=40))

    search_top = max(0, h_top - expected_overlap - margin)
    search_bot = min(h_top - strip_height, h_top - expected_overlap + margin)
    if search_bot <= search_top:
        search_top = max(0, h_top - expected_overlap - _scale(h_top, 0.15))
        search_bot = min(h_top - strip_height, h_top - expected_overlap + _scale(h_top, 0.15))

    if search_bot <= search_top:
        return None

    search_region = img_top[search_top:search_bot + strip_height, :]

    if search_region.shape[0] < template.shape[0] or search_region.shape[1] < template.shape[1]:
        return None

    if len(template.shape) == 3:
        template_gray = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY)
        search_gray = cv2.cvtColor(search_region, cv2.COLOR_RGB2GRAY)
    else:
        template_gray = template
        search_gray = search_region

    result = cv2.matchTemplate(search_gray, template_gray, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val < 0.6:
        return None

    match_y = max_loc[1]
    actual_overlap = h_top - (search_top + match_y)

    max_ratio = 0.85 if relaxed else 0.6
    if actual_overlap > h_top * max_ratio or actual_overlap < min_overlap:
        return None

    return actual_overlap


def stitch_images(
    images: list[Image.Image],
    method: str = "template",
    scroll_distance: int | None = None,
) -> Image.Image:
    """
    将多张截图拼接成一张长图。

    Args:
        images: 按顺序排列的截图（从上到下）
        scroll_distance: 每次滚动距离(px)，用于精确计算裁剪后的预期重叠

    Returns:
        拼接后的长图 PIL Image
    """
    if not images:
        raise ValueError("截图列表为空")
    if len(images) == 1:
        return images[0]

    H = images[0].height

    # 动态检测顶部/底部固定区域
    header_h = _detect_top_header(images)
    footer_h = _detect_bottom_footer(images[-1])
    print(f"  [stitch] 裁剪: 顶部={header_h}px, 底部={footer_h}px")

    # 裁剪固定区域
    cropped: list[Image.Image] = []
    for img in images:
        top = header_h
        bottom = img.height - footer_h if footer_h > 0 else img.height
        if bottom > top:
            cropped.append(img.crop((0, top, img.width, bottom)))
        else:
            cropped.append(img)

    # 统一宽度
    max_width = max(img.width for img in cropped)
    aligned: list[Image.Image] = []
    for img in cropped:
        if img.width != max_width:
            new_img = Image.new("RGB", (max_width, img.height), (255, 255, 255))
            new_img.paste(img, ((max_width - img.width) // 2, 0))
            aligned.append(new_img)
        else:
            aligned.append(img)

    cropped_h = aligned[0].height
    min_overlap = _scale(H, 0.02, floor=30)

    # 预期重叠 = 裁剪后内容高度 - 滚动距离
    if scroll_distance is not None:
        expected_overlap = max(cropped_h - scroll_distance, min_overlap)
        print(f"  [stitch] 预期重叠={expected_overlap}px (内容={cropped_h} - 滚动={scroll_distance})")
    else:
        expected_overlap = max(int(cropped_h * 0.2), min_overlap)
        print(f"  [stitch] 预期重叠={expected_overlap}px (估算)")

    if method == "template" and HAS_CV2:
        return _stitch_template(aligned, min_overlap, expected_overlap)
    else:
        return _stitch_simple(aligned)


def _stitch_template(
    images: list[Image.Image],
    min_overlap: int,
    expected_overlap: int,
) -> Image.Image:
    """基于模板匹配的精确拼接"""
    total_height = images[0].height
    offsets = [0]

    n = len(images)
    for i in range(1, n):
        prev_arr = np.array(images[i - 1].convert("RGB"))
        curr_arr = np.array(images[i].convert("RGB"))
        is_last = (i == n - 1)
        overlap = _template_match_overlap(
            prev_arr, curr_arr, min_overlap, expected_overlap,
            relaxed=is_last,
        )
        if overlap is not None:
            offset = offsets[-1] + images[i - 1].height - overlap
            offsets.append(offset)
            total_height = offset + images[i].height
            print(f"  [stitch] 第{i}张: 重叠={overlap}px, 偏移={offset}px")
        else:
            fallback = max(expected_overlap, min_overlap)
            offset = offsets[-1] + images[i - 1].height - fallback
            offsets.append(offset)
            total_height = offset + images[i].height
            print(f"  [stitch] 第{i}张: 匹配失败，用预期重叠={fallback}px")

    canvas = Image.new("RGB", (images[0].width, total_height), (255, 255, 255))
    for i, img in enumerate(images):
        canvas.paste(img, (0, offsets[i]))

    print(f"[stitch] 拼接完成: {len(images)} 张 → {canvas.size}")
    return canvas


def _stitch_simple(images: list[Image.Image]) -> Image.Image:
    """简单拼接（无去重，用于无 OpenCV 时）"""
    print("[stitch] OpenCV 未安装，使用简单拼接")
    overlap = images[0].height // 5
    total_height = images[0].height + sum(img.height - overlap for img in images[1:])

    canvas = Image.new("RGB", (images[0].width, total_height), (255, 255, 255))
    y = 0
    for img in images:
        canvas.paste(img, (0, y))
        y += img.height - overlap

    print(f"[stitch] 拼接完成: {len(images)} 张 → {canvas.size}")
    return canvas
