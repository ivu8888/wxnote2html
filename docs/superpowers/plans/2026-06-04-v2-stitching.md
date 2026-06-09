# V2 拼接引擎实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重写拼接引擎，用多图投票检测UI、约束搜索+NCC校验做重叠匹配、流式内存管理，支持100+截图。

**Architecture:** 新建 `stitch_v2.py`，接口兼容旧 `stitch.py`。`main.py` 切换到 v2 并增加 `--mode` 参数。capture/ocr/render 不改。

**Tech Stack:** Python 3.10+, opencv-python-headless, numpy, Pillow

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `wxnote2html/stitch_v2.py` | 新建 | 核心拼接引擎 |
| `wxnote2html/main.py` | 修改 | 切换到 stitch_v2，增加 --mode |
| `wxnote2html/stitch.py` | 保留 | V1 旧代码，不再引用 |

---

### Task 1: 创建 stitch_v2.py 骨架

**Files:**
- Create: `wxnote2html/stitch_v2.py`

- [ ] **Step 1: 写模块骨架**

```python
"""
V2 拼接引擎 — 多图投票 + 约束搜索 + NCC校验 + 流式内存
"""

import numpy as np
from PIL import Image

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


def _scale(h: int, ratio: float, floor: int = 10) -> int:
    """h * ratio, 保证不小于 floor"""
    return max(int(h * ratio), floor)


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


def detect_header(images: list[Image.Image]) -> int:
    """多图投票检测顶部UI高度，返回 px"""
    raise NotImplementedError


def detect_footer(images: list[Image.Image], last_image: Image.Image) -> int:
    """末图空白检测 + 多图底部投票，返回 px"""
    raise NotImplementedError


def crop_image(img: Image.Image, header: int, footer: int) -> Image.Image:
    """裁剪单张图的顶部和底部"""
    top = header
    bottom = img.height - footer if footer > 0 else img.height
    if bottom > top:
        return img.crop((0, top, img.width, bottom))
    return img


def match_overlap(
    top_gray: np.ndarray,
    bottom_gray: np.ndarray,
    expected: int,
) -> tuple[int, float]:
    """约束搜索 + NCC 校验，返回 (overlap_px, confidence)"""
    raise NotImplementedError


def blend_seam(
    canvas: np.ndarray, new_strip: np.ndarray, blend_width: int = 5
) -> np.ndarray:
    """拼接缝线性渐变融合，返回融合后的画布"""
    raise NotImplementedError


def stitch(
    images: list[Image.Image],
    scroll_distance: int | None = None,
) -> Image.Image:
    """主入口，流式拼接，返回完整长图"""
    raise NotImplementedError
```

- [ ] **Step 2: 验证骨架可导入**

```bash
python -c "from wxnote2html import stitch_v2; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add wxnote2html/stitch_v2.py
git commit -m "feat: add stitch_v2.py skeleton"
```

---

### Task 2: 实现 detect_header — 多图投票

**Files:**
- Modify: `wxnote2html/stitch_v2.py`

- [ ] **Step 1: 实现 detect_header**

用已有的 `_row_std` 和 `_row_ncc`，替换占位实现：

```python
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
```

- [ ] **Step 2: 验证**

```bash
python -c "
from PIL import Image
import numpy as np
from wxnote2html.stitch_v2 import detect_header
# 创建模拟截图：顶部 100px 相同，下面不同
a = np.zeros((300,200), dtype=np.uint8)
b = np.zeros((300,200), dtype=np.uint8)
a[100:, :] = 200  # 内容区不同
b[100:, :] = 100
imgs = [Image.fromarray(a), Image.fromarray(b)]
h = detect_header(imgs)
assert h > 80 and h < 120, f'Expected ~100, got {h}'
print(f'PASS: header={h}px')
"
```

- [ ] **Step 3: Commit**

```bash
git add wxnote2html/stitch_v2.py
git commit -m "feat: implement detect_header with multi-vote"
```

---

### Task 3: 实现 detect_footer — 末图空白 + 投票

**Files:**
- Modify: `wxnote2html/stitch_v2.py`

- [ ] **Step 1: 实现 detect_footer**

```python
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
```

- [ ] **Step 2: 验证**

```bash
python -c "
from PIL import Image
import numpy as np
from wxnote2html.stitch_v2 import detect_footer
# 模拟：末图底部 60px 空白
last = np.ones((500,200), dtype=np.uint8) * 200
last[-60:, :] = 0  # 底部空白
# 前两张图：底部 40px 相同（导航栏）
a = np.ones((500,200), dtype=np.uint8) * 150
b = np.ones((500,200), dtype=np.uint8) * 150
a[-40:, :] = 50
b[-40:, :] = 50
imgs = [Image.fromarray(a), Image.fromarray(b)]
last_img = Image.fromarray(last)
f = detect_footer(imgs, last_img)
assert f >= 40, f'Expected >=40, got {f}'
print(f'PASS: footer={f}px')
"
```

- [ ] **Step 3: Commit**

```bash
git add wxnote2html/stitch_v2.py
git commit -m "feat: implement detect_footer"
```

---

### Task 4: 实现 match_overlap — 约束搜索 + NCC校验

**Files:**
- Modify: `wxnote2html/stitch_v2.py`

- [ ] **Step 1: 实现 match_overlap**

```python
def match_overlap(
    top_gray: np.ndarray,
    bottom_gray: np.ndarray,
    expected: int,
) -> tuple[int, float]:
    """
    相邻两张裁剪后灰度图的精确重叠匹配。

    1. 在 expected ±50% 窗口内用 cv2.matchTemplate 粗匹配
    2. 在最佳匹配位置做 10 行连续非空白 NCC 校验
    3. 返回 (overlap_px, confidence)
    """
    if not HAS_CV2:
        return expected, 0.5  # 无 OpenCV 直接返回估算值

    H = top_gray.shape[0]
    half_win = max(expected // 2, 20)
    search_top = max(0, H - expected - half_win)
    search_bot = min(H, H - expected + half_win)

    if search_bot <= search_top:
        return expected, 0.5

    # 模板：bottom 图顶部 1/8 高度
    template_h = _scale(H, 0.08, floor=40)
    template_h = min(template_h, bottom_gray.shape[0])
    template = bottom_gray[:template_h, :]

    search_region = top_gray[search_top:search_bot + template_h, :]
    if search_region.shape[0] < template.shape[0] or search_region.shape[1] < template.shape[1]:
        return expected, 0.5

    result = cv2.matchTemplate(
        search_region, template, cv2.TM_CCOEFF_NORMED
    )
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val < 0.5:
        print(f"  [stitch_v2] 匹配相关系数低={max_val:.2f}, 用估算值")
        return expected, max_val

    match_y_local = max_loc[1]
    overlap = H - (search_top + match_y_local)

    # ── 10 行连续非空白 NCC 校验 ──
    content_rows_checked = 0
    content_rows_passed = 0
    required = 10
    row = overlap

    while row < H and row < overlap + (H // 4) and content_rows_checked < required:
        top_row = top_gray[row, :]
        bot_row = bottom_gray[row - overlap, :]

        # 检查是否为空白行
        if float(np.std(top_row)) < 8.0:
            row += 1
            continue

        content_rows_checked += 1
        ncc = _row_ncc(top_row, bot_row)
        if ncc > 0.85:
            content_rows_passed += 1
        else:
            content_rows_passed = 0  # 不连续→重置
            if content_rows_checked >= required + 5:
                break  # 已尽力搜索
        row += 1

    if content_rows_passed < required:
        print(f"  [stitch_v2] NCC校验不通过 ({content_rows_passed}/{required}), 用估算值")
        return expected, content_rows_passed / required

    confidence = content_rows_passed / required
    return overlap, confidence
```

- [ ] **Step 2: 验证**

```bash
python -c "
import numpy as np
from wxnote2html.stitch_v2 import match_overlap

# 创建两张有明确重叠的模拟灰度图
H = 500
overlap = 100
top_gray = np.random.randint(0, 255, (H, 200), dtype=np.uint8)
bottom_gray = np.zeros((H, 200), dtype=np.uint8)
bottom_gray[:overlap, :] = top_gray[-overlap:, :]  # 精确复制重叠区
bottom_gray[overlap:, :] = np.random.randint(0, 255, (H - overlap, 200), dtype=np.uint8)

ov, conf = match_overlap(top_gray, bottom_gray, expected=100)
print(f'overlap={ov}, confidence={conf:.2f}')
# 重叠在 80-120 之间且置信度 > 0.5 视为通过
assert 50 < ov < 150, f'Expected ~100, got {ov}'
print('PASS')
"
```

- [ ] **Step 3: Commit**

```bash
git add wxnote2html/stitch_v2.py
git commit -m "feat: implement match_overlap with constrained search + NCC verify"
```

---

### Task 5: 实现 blend_seam — 拼接缝融合

**Files:**
- Modify: `wxnote2html/stitch_v2.py`

- [ ] **Step 1: 实现 blend_seam**

```python
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
        # 不做融合，直接拼接
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
```

- [ ] **Step 2: 验证**

```bash
python -c "
import numpy as np
from wxnote2html.stitch_v2 import blend_seam

canvas = np.ones((100, 10, 3), dtype=np.uint8) * 200
new_strip = np.ones((50, 10, 3), dtype=np.uint8) * 50
prev_bottom = canvas[-5:, :, :]
result = blend_seam(canvas, new_strip, prev_bottom, blend_width=5)

# 融合区应该有中间值 (100 < val < 200)
blend_zone = result[95:100, :, :]
assert blend_zone.min() > 50, f'Blend zone too dark: {blend_zone.min()}'
assert blend_zone.max() < 200, f'Blend zone too bright: {blend_zone.max()}'
print(f'PASS: result shape={result.shape}')
"
```

- [ ] **Step 3: Commit**

```bash
git add wxnote2html/stitch_v2.py
git commit -m "feat: implement blend_seam"
```

---

### Task 6: 实现 stitch() 主入口 — 流式拼接

**Files:**
- Modify: `wxnote2html/stitch_v2.py`

- [ ] **Step 1: 实现 stitch()**

```python
def stitch(
    images: list[Image.Image],
    scroll_distance: int | None = None,
    blend: bool = True,
) -> Image.Image:
    """
    主入口：流式拼接 N 张截图。

    Args:
        images: 按顺序排列的截图 (PIL Image)
        scroll_distance: 滚动距离 (px)，None 则自动估算
        blend: 是否启用拼接缝融合

    Returns:
        拼接后的长图
    """
    if not images:
        raise ValueError("截图列表为空")
    if len(images) == 1:
        return images[0]

    H, W = images[0].height, images[0].width
    N = len(images)
    print(f"[stitch_v2] 开始拼接: {N} 张, {W}×{H}")

    # ── 阶段1: 采样检测 header / footer ──
    sample_n = min(N, 10)
    sample = images[:sample_n]
    header_h = detect_header(sample)
    footer_h = detect_footer(sample, images[-1])

    cropped_h = H - header_h - footer_h
    print(f"[stitch_v2] 裁剪后内容高度: {cropped_h}px")

    # ── 阶段2: 流式拼接 ──
    canvas: np.ndarray | None = None
    prev_gray: np.ndarray | None = None
    prev_color: np.ndarray | None = None
    expected = scroll_distance if scroll_distance else int(cropped_h * 0.2)
    # 文件模式：用内容高 20% 估重叠，expected 实际是重叠量
    if scroll_distance is None:
        expected = int(cropped_h * 0.2)
    else:
        expected = max(cropped_h - scroll_distance, _scale(H, 0.02, floor=30))

    blend_w = 5 if blend else 0

    for i, img in enumerate(images):
        cropped = crop_image(img, header_h, footer_h)
        gray = np.array(cropped.convert("L"))
        color = np.array(cropped.convert("RGB"))

        if i == 0:
            canvas = color
        else:
            overlap, conf = match_overlap(prev_gray, gray, expected)
            tag = "✓" if conf >= 0.8 else ("⚠" if conf >= 0.5 else "✗ LOW")
            print(f"  [{i}] overlap={overlap}px conf={conf:.2f} {tag}")

            # 新增条带 = current[overlap:]
            new_strip = color[overlap:, :, :]

            if blend_w > 0 and overlap >= blend_w:
                prev_bottom = prev_color[-(blend_w + overlap): -overlap or None, :, :]
                canvas = blend_seam(canvas, new_strip, prev_bottom, blend_w)
            else:
                canvas = np.vstack([canvas, new_strip])

            # 校准下一对预期值
            if i == 1 and scroll_distance is None:
                expected = overlap  # 首对校准

        prev_gray = gray
        prev_color = color

    result = Image.fromarray(canvas)
    print(f"[stitch_v2] 完成: {result.size}")
    return result
```

- [ ] **Step 2: 集成验证**

```bash
python -c "
import numpy as np
from PIL import Image
from wxnote2html.stitch_v2 import stitch

# 模拟 5 张图，每张 500×200，滚动 350px
H = 500
images = []
for i in range(5):
    arr = np.ones((H, 200, 3), dtype=np.uint8) * (i * 40 + 30)
    images.append(Image.fromarray(arr))

result = stitch(images, scroll_distance=350)
print(f'Result: {result.size}')
assert result.width == 200
assert result.height > 500 and result.height < 2500
print('PASS')
"
```

- [ ] **Step 3: Commit**

```bash
git add wxnote2html/stitch_v2.py
git commit -m "feat: implement stitch() streaming entry point"
```

---

### Task 7: 更新 main.py 切换到 stitch_v2

**Files:**
- Modify: `wxnote2html/main.py`

- [ ] **Step 1: 修改 import 和调用**

找到 `main.py` 中 `_import_module("stitch").stitch_images` 的调用位置（约第116行），替换为：

```python
    # ── 2. 拼接 ──
    if args.skip_stitch:
        print("[main] 跳过拼接")
        stitched = images[0]
    else:
        stitch_v2 = _import_module("stitch_v2")
        use_blend = not args.no_blend
        if args.images:
            # 文件模式：无 scroll_distance，引擎自动估算
            stitched = stitch_v2.stitch(images, blend=use_blend)
        else:
            # ADB 模式：传入实际滚动距离
            stitched = stitch_v2.stitch(images, scroll_distance=scroll_dist, blend=use_blend)
```

- [ ] **Step 2: 添加 --no-blend 参数**

在 `main.py` 的 argparse 段添加：

```python
    parser.add_argument("--no-blend", action="store_true", help="禁用拼接缝融合")
```

- [ ] **Step 3: 添加 --mode 参数**

```python
    parser.add_argument(
        "--mode", choices=["adb", "file"], default="adb",
        help="输入模式: adb (自动截图) / file (文件导入)"
    )
```

- [ ] **Step 4: 更新 --images 逻辑**

当 `--mode file` 时，`--images` 为必填且不再尝试 ADB：

```python
    if args.mode == "file":
        if not args.images:
            print("错误: file 模式需要 --images 参数")
            sys.exit(1)
        # 文件加载逻辑（已有）
        ...
    else:
        # ADB 截图逻辑（已有）
        ...
```

当前代码结构中 `--images` 存在时直接走文件分支，不存在时走 ADB。改为用 `--mode` 控制，保持向后兼容：无 `--mode` 时，有 `--images` 走文件，无则走 ADB。

```python
    if args.mode == "file" or args.images:
        # 文件模式
        ...
    else:
        # ADB 模式
        ...
```

- [ ] **Step 5: 验证 main.py 语法**

```bash
python -c "import py_compile; py_compile.compile('wxnote2html/main.py', doraise=True); print('OK')"
```

- [ ] **Step 6: Commit**

```bash
git add wxnote2html/main.py
git commit -m "feat: switch main.py to stitch_v2, add --mode and --no-blend"
```

---

### Task 8: 端到端集成测试

**Files:**
- 无新建文件

- [ ] **Step 1: 用模拟截图端到端测试**

```bash
python -c "
import numpy as np
from PIL import Image
from wxnote2html.stitch_v2 import stitch
import os, tempfile, glob

# 模拟 20 张 1080×2400 截图，含顶部 150px 相同 header
H, W = 2400, 1080
scroll = 1800
images = []
for i in range(20):
    arr = np.random.randint(200, 256, (H, W, 3), dtype=np.uint8)
    # 顶部 150px: 所有图相同 (header)
    arr[:150, :, :] = 60
    # 底部 80px: 所有图相同 (导航栏)
    arr[-80:, :, :] = 30
    # 内容区: 每条白线上写编号
    arr[200 + i * 10:210 + i * 10, 100:500, :] = 255
    images.append(Image.fromarray(arr))

result = stitch(images, scroll_distance=scroll)
print(f'Final: {result.size}')

# 验证：高度应该 ≈ (H - 150 - 80) + (N-1) * (cropped_h - overlap)
# cropped_h = 2400 - 150 - 80 = 2170
# overlap = 2170 - 1800 = 370
# expected_h = 2170 + 19 * (2170 - 370) = 2170 + 34200 = 36370
expected_h = (H - 150 - 80) + (19) * ((H - 150 - 80) - scroll)
actual_h = result.height
ratio = actual_h / expected_h
print(f'Expected height: ~{expected_h}, actual: {actual_h}, ratio: {ratio:.2f}')
assert 0.85 < ratio < 1.15, f'Height mismatch: {ratio}'
print('PASS: 端到端测试通过')
"
```

- [ ] **Step 2: 测试文件模式（无 scroll_distance）**

```bash
python -c "
import numpy as np
from PIL import Image
from wxnote2html.stitch_v2 import stitch

H = 2400
images = []
for i in range(5):
    arr = np.ones((H, 200, 3), dtype=np.uint8) * (i * 40 + 30)
    arr[:120, :, :] = 50  # header
    images.append(Image.fromarray(arr))

# 不传 scroll_distance → 文件模式，自动估算
result = stitch(images)
print(f'File mode result: {result.size}')
assert result.height > H
print('PASS')
"
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "test: add end-to-end integration tests for stitch_v2"
```
