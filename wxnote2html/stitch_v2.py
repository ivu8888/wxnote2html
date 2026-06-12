"""
V2 拼接引擎 — 多图投票 + 约束搜索 + NCC校验 + 流式内存
"""

import sys

import numpy as np
from PIL import Image

# 强制 UTF-8 输出，解决 Windows bash 中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


def _scale(h: int, ratio: float, floor: int = 10) -> int:
    """h * ratio, 保证不小于 floor"""
    return max(int(h * ratio), floor)


def _write_log(log_dir: str, *lines: str, mode: str = "a"):
    """向 debug.log 追加带时间戳的日志行"""
    from pathlib import Path
    from datetime import datetime
    d = Path(log_dir)
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%H:%M:%S")
    with open(d / "debug.log", mode, encoding="utf-8") as f:
        for line in lines:
            f.write(f"[{ts}] {line}\n")


def _row_std(gray: np.ndarray) -> np.ndarray:
    """逐行计算像素标准差，返回 shape (H,)"""
    return np.std(gray, axis=1)


def _row_ncc(row1: np.ndarray, row2: np.ndarray) -> float:
    """两行之间的归一化互相关系数，范围 [0, 1]"""
    r1 = row1.astype(np.float64)
    r2 = row2.astype(np.float64)
    r1_mean = r1.mean()
    r2_mean = r2.mean()
    num = ((r1 - r1_mean) * (r2 - r2_mean)).sum()
    den = np.sqrt(((r1 - r1_mean) ** 2).sum() * ((r2 - r2_mean) ** 2).sum())
    if den < 1e-10:
        return 1.0  # 两行都是纯色 → 视为相同
    return max(0.0, float(num / den))


def _clamp_overlap(overlap: int, bottom_h: int, max_ratio: float = 0.85) -> int:
    """
    FR-8 重叠量合理性防护。
    无论校验是否通过，每次匹配后均执行以下检查。
    """
    if overlap >= bottom_h - 10:
        clamped = max(0, bottom_h - 30)
        print(
            f"  \033[91m[stitch_v2] OVERLAP: overlap过大"
            f"({overlap} >= {bottom_h - 10}), 钳制为{clamped}\033[0m"
        )
        return clamped

    if overlap < 0:
        print(
            f"  \033[91m[stitch_v2] OVERLAP: overlap负值({overlap}), "
            f"钳制为0\033[0m"
        )
        return 0

    if overlap > int(bottom_h * max_ratio):
        print(
            f"  \033[93m[stitch_v2] HIGH OVERLAP: {overlap}/{bottom_h} "
            f"(>{max_ratio:.0%}), 不自动修正\033[0m"
        )

    return overlap


def detect_header(images: list[Image.Image]) -> int:
    """
    多图投票检测顶部固定UI高度。
    取前 min(N,10) 张图，对每对相邻图用 MSE 拐点法，取中位数。
    """
    N = min(len(images), 10)
    if N < 2:
        return 0

    candidates: list[int] = []
    max_h = _scale(images[0].height, 0.15, floor=50)

    for i in range(N - 1):
        a = np.array(images[i].convert("L"), dtype=np.float32)
        b = np.array(images[i + 1].convert("L"), dtype=np.float32)
        row_mse = ((a[:max_h] - b[:max_h]) ** 2).mean(axis=1)

        # 找 MSE 突变的拐点：从前 20% 位置开始往后扫
        start = max_h // 5
        if start >= len(row_mse):
            continue
        baseline = np.mean(row_mse[:start])
        for row in range(start, len(row_mse)):
            if row_mse[row] > baseline * 3 and row_mse[row] > 30:
                candidates.append(row)
                break

    if not candidates:
        # 兜底：首图单图方差法
        gray = np.array(images[0].convert("L"), dtype=np.float32)
        row_std = _row_std(gray[:max_h])
        for row in range(10, len(row_std)):
            if row_std[row] > 15:
                print(f"  [stitch_v2] header 兜底(单图方差): {row}px")
                return row
        return 0

    header_h = int(np.median(candidates))
    print(f"  [stitch_v2] header 多图投票: {header_h}px (候选={candidates})")
    return header_h


def detect_footer(images: list[Image.Image], last_image: Image.Image) -> int:
    """
    信号A: 末图从底部向上扫描，找到空白→内容的边界（文章结束线）
    信号B: 前 K 张图底部共同区域投票（导航栏）
    取 max(信号A, 信号B)
    """
    H = last_image.height
    gray_last = np.array(last_image.convert("L"), dtype=np.float32)

    # 信号A：末图底部向上扫
    max_scan = _scale(H, 0.25, floor=80)
    strip = gray_last[-max_scan:][::-1]
    row_std = _row_std(strip)

    footer_a = 0
    for row in range(len(row_std)):
        if row_std[row] > 12 and row + 2 < len(row_std) and row_std[row + 1] > 10:
            footer_a = max(0, row - 5)
            break
    if footer_a == 0 and np.mean(row_std) < 6:
        footer_a = max_scan

    # 信号B：取前 min(N,10) 张图底部共同区域投票
    N = min(len(images), 10)
    footer_b = 0
    if N >= 2:
        candidates_b: list[int] = []
        max_b = _scale(H, 0.10, floor=40)
        for i in range(N - 1):
            a = np.array(images[i].convert("L"), dtype=np.float32)
            b = np.array(images[i + 1].convert("L"), dtype=np.float32)
            a_bot = a[-max_b:][::-1]
            b_bot = b[-max_b:][::-1]
            row_mse = ((a_bot - b_bot) ** 2).mean(axis=1)
            for row in range(len(row_mse)):
                if row_mse[row] > 30:
                    if row > 0:
                        candidates_b.append(row)
                    break
        if candidates_b:
            footer_b = int(np.median(candidates_b))

    footer_h = max(footer_a, footer_b)
    print(f"  [stitch_v2] footer: 末图空白={footer_a}px, 投票={footer_b}px → 取{footer_h}px")
    return footer_h


def detect_scrollbar(images: list[Image.Image]) -> int:
    """
    多图投票检测右侧滚动条宽度。

    从第 2 张图开始（首张无滚动条），从右边缘向左扫描列 std，
    找到 std 骤降处作为滚动条边界。多图中位数投票，适配不同分辨率。

    Returns:
        滚动条宽度 (px)，未检测到则返回 0
    """
    N = min(len(images), 8)
    if N < 2:
        return 0

    candidates: list[int] = []
    max_scan = max(int(images[0].width * 0.03), 15)  # 搜索右边缘 3% 宽度

    for i in range(1, N):  # 从第 2 张开始（首张无滚动条）
        gray = np.array(images[i].convert("L"), dtype=np.float32)
        W = gray.shape[1]

        # 从右向左扫描
        for offset in range(max_scan):
            col = gray[:, W - 1 - offset]
            col_std = float(np.std(col))
            if col_std < 6.0 and offset > 0:
                # std 骤降 → 滚动条边界
                candidates.append(offset)
                break

    if not candidates:
        return 0

    scrollbar_w = int(np.median(candidates))
    print(f"  [stitch_v2] scrollbar 多图投票: {scrollbar_w}px (候选={candidates})")
    return scrollbar_w


def crop_image(img: Image.Image, header: int, footer: int, right_crop: int = 0) -> Image.Image:
    """裁剪单张图的顶部、底部和右侧"""
    top = header
    bottom = img.height - footer if footer > 0 else img.height
    right = img.width - right_crop if right_crop > 0 else img.width
    if bottom > top and right > 0:
        return img.crop((0, top, right, bottom))
    return img


def _verify_ncc(
    top_gray: np.ndarray,
    bottom_gray: np.ndarray,
    overlap: int,
    required: int = 60,
) -> float:
    """N 行连续非空白 NCC 校验，返回 confidence"""
    H = top_gray.shape[0]
    top_start = H - overlap
    content_rows_checked = 0
    content_rows_passed = 0

    for offset in range(overlap):
        top_row = top_gray[top_start + offset, :]
        bot_row = bottom_gray[offset, :]

        if float(np.std(top_row)) < 8.0:
            continue

        content_rows_checked += 1
        ncc = _row_ncc(top_row, bot_row)
        if ncc > 0.75:
            content_rows_passed += 1
            if content_rows_passed >= required:
                break
        else:
            content_rows_passed = 0

        if content_rows_checked >= required * 5:
            break

    return content_rows_passed / required


def _global_search(
    top_gray: np.ndarray,
    bottom_gray: np.ndarray,
    template_h: int = 60,
) -> tuple[int | None, float]:
    """
    在上图 10%~90% 区域全局搜索，用于首对匹配失败兜底。

    Returns:
        (overlap, max_val) — overlap 为 None 表示搜索失败
    """
    if not HAS_CV2:
        return None, 0.0

    H = top_gray.shape[0]
    th = min(template_h, bottom_gray.shape[0])
    template = bottom_gray[:th, :]

    search_top = int(H * 0.1)
    search_bot = int(H * 0.9)
    if search_bot - search_top < th:
        return None, 0.0

    search_region = top_gray[search_top:search_bot, :]
    result = cv2.matchTemplate(search_region, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val < 0.3:
        print(f"  [stitch_v2] 全局搜索: 相关系数低={max_val:.2f}")
        return None, max_val

    match_y = search_top + max_loc[1]
    overlap = H - match_y
    print(f"  [stitch_v2] 全局搜索成功 overlap={overlap} (max_val={max_val:.2f})")
    return overlap, max_val


def _apply_fallback(
    expected: int,
    history: list[int] | None,
    is_first_pair: bool,
    top_gray: np.ndarray,
    bottom_gray: np.ndarray,
    max_ratio: float = 0.85,
) -> tuple[int, float]:
    """
    FR-7 兜底策略：校验失败时选择合适的 fallback overlap。

    优先级: 历史中位数 > 全局搜索(首对) > 几何估算
    """
    bottom_h = bottom_gray.shape[0]

    # 有历史数据 → 中位数兜底
    if history and len(history) > 0:
        fallback = int(np.median(history))
        print(f"  [stitch_v2] 使用历史中位数兜底 overlap={fallback} (历史={history})")
        clamped = _clamp_overlap(fallback, bottom_h, max_ratio)
        return clamped, 0.5

    # 无历史 + 首对 → 全局搜索
    if is_first_pair:
        gs_overlap, gs_val = _global_search(top_gray, bottom_gray)
        if gs_overlap is not None:
            clamped = _clamp_overlap(gs_overlap, bottom_h, max_ratio)
            return clamped, 0.5
        print(f"  [stitch_v2] 全局搜索失败, 使用估算值 expected={expected}")

    # 无历史 + 非首对 / 全局搜索失败 → 几何估算
    clamped = _clamp_overlap(expected, bottom_h, max_ratio)
    print(f"  [stitch_v2] 使用估算值 expected={expected} → clamped={clamped}")
    return clamped, 0.3


def match_overlap(
    top_gray: np.ndarray,
    bottom_gray: np.ndarray,
    expected: int,
    history: list[int] | None = None,
    is_first_pair: bool = False,
    max_ratio: float = 0.85,
    debug: bool = False,
    debug_dir: str = "tmp",
) -> tuple[int, float]:
    """
    约束搜索 + 60行NCC校验 + 滑动窗口兜底 + 全局搜索 + 钳制。

    1. 搜索区 = [expected × 0.2, expected × 2.0]，min 50行，auto-expand 100
    2. cv2.matchTemplate 粗匹配 (TM_CCOEFF_NORMED)
    3. 60 行连续非空白 NCC > 0.75 校验
    4. 失败 → FR-7 兜底 (历史中位数 / 全局搜索 / 几何估算)
    5. 最终 overlap → FR-8 钳制防护
    """
    if not HAS_CV2:
        return expected, 0.5

    H = top_gray.shape[0]
    bottom_h = bottom_gray.shape[0]
    template_h = min(60, bottom_h)

    # ── 1. 约束搜索区 ──
    half_win = max(int(expected * 0.9), 80)  # ±90% 覆盖滚动距离波动
    if half_win < 100:
        half_win = 100
    search_top = max(0, H - expected - half_win)
    search_bot = min(H - template_h, H - expected + half_win)

    if search_bot <= search_top:
        if debug:
            print(f"  [stitch_v2] 搜索区过窄, 进入兜底")
        return _apply_fallback(
            expected, history, is_first_pair, top_gray, bottom_gray, max_ratio
        )

    # ── 2. cv2.matchTemplate 粗匹配 ──
    template = bottom_gray[:template_h, :]
    search_region = top_gray[search_top:search_bot, :]

    if search_region.shape[0] < template.shape[0] or search_region.shape[1] < template.shape[1]:
        return _apply_fallback(
            expected, history, is_first_pair, top_gray, bottom_gray, max_ratio
        )

    result = cv2.matchTemplate(search_region, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if debug:
        print(f"  [stitch_v2] 搜索区=[{search_top},{search_bot}] template={template_h}行 corr={max_val:.3f}")

    if max_val < 0.3:
        if debug:
            print(f"  [stitch_v2] 匹配相关系数低={max_val:.2f}, 进入兜底")
        return _apply_fallback(
            expected, history, is_first_pair, top_gray, bottom_gray, max_ratio
        )

    overlap = H - (search_top + max_loc[1])

    # ── 3. 60 行连续非空白 NCC 校验 ──
    conf = _verify_ncc(top_gray, bottom_gray, overlap, required=60)

    if debug:
        print(f"  [stitch_v2] NCC: {int(conf*60)}/60 连续通过" if conf >= 1.0 else
              f"  [stitch_v2] NCC校验不通过 (confidence={conf:.0%}), 进入兜底")

    if conf < 1.0:
        overlap, conf = _apply_fallback(
            expected, history, is_first_pair, top_gray, bottom_gray, max_ratio
        )
    else:
        conf = 1.0

    # ── 4. FR-8 重叠量合理性防护 ──
    overlap = _clamp_overlap(overlap, bottom_h, max_ratio)

    # ── debug: 保存匹配调试图 ──
    if debug:
        from pathlib import Path
        d = Path(debug_dir)
        d.mkdir(parents=True, exist_ok=True)
        idx = 1 if history is None else len(history) + 1
        Image.fromarray(search_region).save(str(d / f"match_{idx:03d}_search.png"))
        Image.fromarray(template).save(str(d / f"match_{idx:03d}_template.png"))

    return overlap, conf


def _match_last_pair(
    top_gray: np.ndarray,
    bottom_gray: np.ndarray,
    expected: int,
    history: list[int] | None,
    cropped_h: int,
    scroll_dist: int | None,
    max_ratio: float,
    debug: bool = False,
) -> tuple[int, float]:
    """
    特殊处理最后一张图的 overlap 检测 — 双向模板匹配。

    正向: 底部图的顶部 60 行 → 在顶部图下半部搜索 (适用于正常重叠)
    反向: 顶部图的底部 60 行 → 在底部图上半部搜索 (适用于巨大重叠,
          因为顶部图底部是内容密集区, 模板辨识度远高于底部图顶部的稀疏区)

    综合双向候选 + 放宽 NCC(30 行) + 综合评分。
    """
    if not HAS_CV2:
        return expected, 0.5

    H = top_gray.shape[0]
    # 模板高度按图像比例计算 (3%)，适配不同分辨率手机
    th = max(30, int(H * 0.03))
    th = min(th, bottom_gray.shape[0])
    # NCC 要求也按比例 (1.5%)，保证至少 15 行
    ncc_required = max(15, int(H * 0.015))

    # ── 正向: 底部图顶部模板 → 顶部图搜索 ──
    fwd_scores = []
    template_fwd = bottom_gray[:th, :]
    fwd_search_start = int(H * 0.05)
    fwd_search_end = H - th
    if fwd_search_end > fwd_search_start:
        fwd_region = top_gray[fwd_search_start:fwd_search_end, :]
        fwd_result = cv2.matchTemplate(fwd_region, template_fwd, cv2.TM_CCOEFF_NORMED)
        fwd_raw = fwd_result.ravel()
        top_k = min(3, len(fwd_raw))
        top_idx = np.argpartition(fwd_raw, -top_k)[-top_k:]
        top_idx = top_idx[np.argsort(fwd_raw[top_idx])][::-1]
        for idx in top_idx:
            corr = float(fwd_raw[idx])
            if corr >= 0.3:
                ov = H - (fwd_search_start + idx)
                fwd_scores.append((ov, corr, "fwd"))

    # ── 反向: 顶部图底部模板 → 底部图搜索 ──
    rev_scores = []
    template_rev = top_gray[-th:, :]
    # 搜索底部图上半部分: 0 ~ 95% (overlap ≤ 底部图高度)
    rev_search_end = bottom_gray.shape[0] - th
    if rev_search_end > 0:
        rev_region = bottom_gray[:rev_search_end, :]
        rev_result = cv2.matchTemplate(rev_region, template_rev, cv2.TM_CCOEFF_NORMED)
        rev_raw = rev_result.ravel()
        top_k = min(3, len(rev_raw))
        top_idx = np.argpartition(rev_raw, -top_k)[-top_k:]
        top_idx = top_idx[np.argsort(rev_raw[top_idx])][::-1]
        for idx in top_idx:
            corr = float(rev_raw[idx])
            if corr >= 0.3:
                # overlap = 模板在底部图中的位置 + 模板高度
                ov = int(idx) + th
                rev_scores.append((ov, corr, "rev"))

    # ── 合并所有候选, NCC 验证 ──
    all_candidates = fwd_scores + rev_scores
    if not all_candidates:
        ov = _clamp_overlap(expected, bottom_gray.shape[0], max_ratio)
        return ov, 0.5

    best_ov = expected
    best_score = 0.0

    for ov, corr, direction in all_candidates:
        # 放宽 NCC 要求：30 行连续非空白
        conf = _verify_ncc(top_gray, bottom_gray, ov, required=ncc_required)
        # 反向匹配在巨大重叠时更可靠 → 加权
        dir_weight = 1.1 if direction == "rev" else 1.0
        score = (corr * 0.4 + conf * 0.6) * dir_weight
        if debug:
            print(f"  [stitch_v2] last_pair[{direction}] ov={ov} corr={corr:.3f} ncc={conf:.2f} score={score:.2f}")
        if score > best_score:
            best_score = score
            best_ov = ov

    if best_score > 0:
        ov = _clamp_overlap(best_ov, bottom_gray.shape[0], max_ratio)
        return ov, min(1.0, max(0.5, best_score))

    # 最终兜底
    geo_ov = max(0, cropped_h - scroll_dist) if scroll_dist is not None else expected
    if history:
        fallback = max(int(np.median(history)), geo_ov)
    else:
        fallback = max(expected, geo_ov)
    ov = _clamp_overlap(fallback, bottom_gray.shape[0], max_ratio)
    if debug:
        print(f"  [stitch_v2] last_pair兜底 ov={ov}")
    return ov, 0.5


def blend_seam(
    canvas: np.ndarray,
    new_strip: np.ndarray,
    prev_bottom: np.ndarray,
    blend_width: int = 5,
) -> np.ndarray:
    """
    将 new_strip 拼接到 canvas 底部，重叠 blend_width 行用线性渐变融合。

    Args:
        canvas: 累积画布 (H×W×3, uint8)
        new_strip: 新增条带 (H'×W×3, uint8)
        prev_bottom: 前一张图的底部 blend_width 行 (blend_width×W×3, uint8)
        blend_width: 融合宽度

    Returns:
        拼接后的画布
    """
    if blend_width <= 0 or prev_bottom.shape[0] < blend_width:
        return np.vstack([canvas, new_strip])

    # 取 new_strip 顶部 blend_width 行
    new_top = new_strip[:blend_width, :, :]

    # Alpha 线性渐变
    alpha = np.linspace(0, 1, blend_width, dtype=np.float64).reshape(-1, 1, 1)
    blended = (prev_bottom * (1 - alpha) + new_top * alpha).astype(np.uint8)

    # 组合：canvas[:-blend] + blended + new_strip[blend:]
    result = np.vstack([
        canvas[: canvas.shape[0] - blend_width, :, :],
        blended,
        new_strip[blend_width:, :, :],
    ])
    return result


def stitch(
    images: list[Image.Image],
    scroll_distances: list[int] | None = None,
    blend: bool = True,
    max_ratio: float = 0.85,
    debug: bool = False,
) -> Image.Image:
    """
    主入口：流式拼接 N 张截图。

    Args:
        images: 按顺序排列的截图 (PIL Image)
        scroll_distances: 每次滚动的实际距离列表 (len=len(images)-1)，None 则自动估算
        blend: 是否启用拼接缝融合
        max_ratio: overlap 占下图高度的最大比例
        debug: 调试模式 — 详细日志 + 匹配调试图保存到 tmp/

    Returns:
        拼接后的长图
    """
    if not images:
        raise ValueError("截图列表为空")
    if len(images) == 1:
        return images[0]

    H, W = images[0].height, images[0].width
    N = len(images)
    # 校验所有截图尺寸一致（防止混入已裁剪的旧图）
    for idx, img in enumerate(images):
        if img.size != (W, H):
            raise ValueError(
                f"截图尺寸不一致: 第1张={W}×{H}, 第{idx+1}张={img.size[0]}×{img.size[1]}。"
                f"是否混入了已裁剪的旧图(cropped_*.png)?"
            )
    print(f"[stitch_v2] 开始拼接: {N} 张, {W}×{H}")

    # 确保 tmp 目录存在
    from pathlib import Path
    tmp_dir = Path("tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if debug:
        _write_log(str(tmp_dir),
            "--- Stitch ---",
            f"图片数: {N}  分辨率: {W}×{H}",
        )

    # ── 阶段1: 采样检测 header / footer / scrollbar ──
    sample_n = min(N, 10)
    sample = images[:sample_n]
    header_h = detect_header(sample)
    footer_h = detect_footer(sample, images[-1])
    scrollbar_w = detect_scrollbar(sample)

    cropped_h = H - header_h - footer_h
    if debug:
        print(f"[stitch_v2] header={header_h}px footer={footer_h}px scrollbar={scrollbar_w}px")
        _write_log(str(tmp_dir),
            f"Header: {header_h}px  Footer: {footer_h}px  Scrollbar: {scrollbar_w}px  "
            f"裁剪后: {W - scrollbar_w}×{cropped_h}px",
            "",
        )
    print(f"[stitch_v2] 裁剪后内容: {W - scrollbar_w}×{cropped_h}px")

    # ── 阶段2: 流式拼接 ──
    canvas: np.ndarray | None = None
    prev_gray: np.ndarray | None = None
    prev_color: np.ndarray | None = None

    # 预期重叠量 — 由 per-pair scroll_distance 计算
    # scroll_distances[i-1] = 截图 i-1 到 i 之间的滚动距离
    def _calc_expected(pair_idx: int) -> int:
        """根据第 pair_idx 次滚动距离计算预期 overlap（pair_idx 从 0 开始）"""
        if scroll_distances is not None and pair_idx < len(scroll_distances):
            return max(cropped_h - scroll_distances[pair_idx], _scale(H, 0.02, floor=30))
        return int(cropped_h * 0.2)  # 默认 20% 重叠

    expected = _calc_expected(0)  # 第一对 (i=1) 的预期值

    if debug:
        print(f"[stitch_v2] 初始预期重叠={expected}px (scroll_distances={scroll_distances})")
    blend_w = 5 if blend else 0

    # 滑动窗口：记录最近 3 次成功匹配的 overlap
    history: list[int] = []

    for i, img in enumerate(images):
        cropped = crop_image(img, header_h, footer_h, right_crop=scrollbar_w)
        gray = np.array(cropped.convert("L"))
        color = np.array(cropped.convert("RGB"))

        # 始终保存裁剪图到 tmp/
        cropped.save(str(tmp_dir / f"cropped_{i:03d}.png"))

        if debug:
            print(f"  [cropped {i}] size={cropped.size}")

        if i == 0:
            canvas = color
        else:
            is_first = (i == 1)
            is_last = (i == N - 1)
            ov, conf = match_overlap(
                prev_gray, gray, expected,
                history=history if history else None,
                is_first_pair=is_first,
                max_ratio=max_ratio,
                debug=debug,
                debug_dir=str(tmp_dir),
            )

            # 特殊处理最后一张图：全范围搜索 + 放宽 NCC
            if is_last and conf < 0.8:
                last_sd = scroll_distances[i - 1] if scroll_distances and i - 1 < len(scroll_distances) else None
                if debug:
                    print(f"  [stitch_v2] 最后一张图 conf={conf:.2f}, 启用全范围搜索")
                ov, conf = _match_last_pair(
                    prev_gray, gray, expected,
                    history=history if history else None,
                    cropped_h=cropped_h,
                    scroll_dist=last_sd,
                    max_ratio=max_ratio,
                    debug=debug,
                )

            tag = "OK" if conf >= 0.8 else ("WARN" if conf >= 0.5 else "LOW")
            print(f"  [{i}] overlap={ov}px conf={conf:.2f} {tag}")

            if debug:
                sd = scroll_distances[i - 1] if scroll_distances and i - 1 < len(scroll_distances) else "?"
                _write_log(str(tmp_dir),
                    f"[匹配 {i}→{i+1}] 预期={expected}px  实际={ov}px  "
                    f"置信度={conf:.2f}  scroll_dist={sd}  结果={tag}",
                )

            # 成功匹配 → 记录到滑动窗口
            if conf >= 0.8:
                history.append(ov)
                if len(history) > 3:
                    history.pop(0)

            new_strip = color[ov:, :, :]

            if blend_w > 0 and ov >= blend_w:
                prev_bottom = prev_color[-blend_w:, :, :]
                canvas = blend_seam(canvas, new_strip, prev_bottom, blend_w)
            else:
                canvas = np.vstack([canvas, new_strip])

            # 更新下一对的预期 overlap
            # 成功匹配 → 用实际 overlap 校准；失败 → 用下一对的滚动距离估算
            if conf >= 0.8:
                expected = ov
            else:
                expected = _calc_expected(i)  # i = 下一对的 pair_idx

        prev_gray = gray
        prev_color = color

    result = Image.fromarray(canvas)
    print(f"[stitch_v2] 完成: {result.size}")
    if debug:
        _write_log(str(tmp_dir),
            f"拼接完成: {result.size[0]}×{result.size[1]}px",
        )
    return result
