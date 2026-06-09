# V2 拼接引擎实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 `match_overlap`（约束搜索 + FR-7滑动窗口兜底 + FR-8钳制防护），更新 `stitch()` 历史窗口管理，添加新CLI参数。

**Architecture:** 修改 `stitch_v2.py`（match_overlap 重写 + stitch 历史管理）和 `main.py`（新CLI参数）。`_verify_ncc`、`detect_header`、`detect_footer`、`blend_seam` 保持不变。

**Tech Stack:** Python 3.10+, opencv-python-headless, numpy, Pillow

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `wxnote2html/stitch_v2.py` | 修改 | 重写 match_overlap，添加 _global_search、_apply_fallback、_clamp_overlap，更新 stitch |
| `wxnote2html/main.py` | 修改 | 添加 --confidence-threshold、--max-overlap-ratio 参数 |

---

### Task 1: 添加 `_clamp_overlap` — FR-8 重叠量合理性防护

**Files:**
- Modify: `wxnote2html/stitch_v2.py`

- [ ] **Step 1: 在 `_row_ncc` 函数之后（约第35行后）添加 `_clamp_overlap`**

```python
def _clamp_overlap(overlap: int, bottom_h: int, max_ratio: float = 0.85) -> int:
    """
    FR-8 重叠量合理性防护。
    无论校验是否通过，每次匹配后均执行以下检查。
    返回钳制后的 overlap 值。
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
```

- [ ] **Step 2: 验证语法**

```bash
python -c "from wxnote2html.stitch_v2 import _clamp_overlap; print('OK')"
```

- [ ] **Step 3: 验证逻辑**

```bash
python -c "
from wxnote2html.stitch_v2 import _clamp_overlap
# overlap >= bottom_h - 10 → 钳制
assert _clamp_overlap(95, 100) == 70
# overlap < 0 → 钳制为0
assert _clamp_overlap(-5, 100) == 0
# overlap > bottom_h * 0.85 → 不修正，仅警告
assert _clamp_overlap(90, 100, max_ratio=0.85) == 90
# 正常 overlap → 原样返回
assert _clamp_overlap(50, 100) == 50
print('PASS: all clamp checks')
"
```

- [ ] **Step 4: Commit**

```bash
git add wxnote2html/stitch_v2.py
git commit -m "feat: add _clamp_overlap for FR-8 overlap reasonableness guard"
```

---

### Task 2: 添加 `_global_search` — 首对匹配失败全局搜索

**Files:**
- Modify: `wxnote2html/stitch_v2.py`

- [ ] **Step 1: 在 `_clamp_overlap` 之后添加 `_global_search`**

```python
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
```

- [ ] **Step 2: 验证语法**

```bash
python -c "from wxnote2html.stitch_v2 import _global_search; print('OK')"
```

- [ ] **Step 3: 验证逻辑**

```bash
python -c "
import numpy as np
from wxnote2html.stitch_v2 import _global_search

# 创建模拟：top_gray 底部 100 行 = bottom_gray 顶部 60 行
H = 500
overlap_true = 100
top = np.random.randint(0, 255, (H, 200), dtype=np.uint8)
bot = np.zeros((H, 200), dtype=np.uint8)
bot[:60, :] = top[-overlap_true:-overlap_true+60, :]  # 重叠区在 bottom 顶部60行
bot[60:, :] = np.random.randint(0, 255, (H-60, 200), dtype=np.uint8)

ov, val = _global_search(top, bot)
print(f'global_search: overlap={ov}, max_val={val:.2f}')
assert ov is not None, 'Global search should succeed'
# overlap 应该在真实值附近
assert abs(ov - overlap_true) < 30, f'Expected ~{overlap_true}, got {ov}'
print('PASS')
"
```

- [ ] **Step 4: Commit**

```bash
git add wxnote2html/stitch_v2.py
git commit -m "feat: add _global_search for first-pair fallback"
```

---

### Task 3: 添加 `_apply_fallback` — FR-7 兜底策略决策

**Files:**
- Modify: `wxnote2html/stitch_v2.py`

- [ ] **Step 1: 在 `_global_search` 之后添加 `_apply_fallback`**

```python
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
    
    Returns:
        (fallback_overlap, confidence)
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
```

- [ ] **Step 2: 验证语法**

```bash
python -c "from wxnote2html.stitch_v2 import _apply_fallback; print('OK')"
```

- [ ] **Step 3: 验证逻辑 — 历史中位数路径**

```bash
python -c "
import numpy as np
from wxnote2html.stitch_v2 import _apply_fallback

# 创建最小可用的灰度图
top = np.ones((200, 50), dtype=np.uint8) * 128
bot = np.ones((200, 50), dtype=np.uint8) * 128

# 有历史 → 使用历史中位数
ov, conf = _apply_fallback(100, [80, 90, 85], False, top, bot)
assert ov == 85, f'Expected median 85, got {ov}'
assert conf == 0.5
print(f'PASS (history): overlap={ov}, conf={conf}')

# 无历史 + 非首对 → 使用 expected
ov, conf = _apply_fallback(100, None, False, top, bot)
assert ov == 100, f'Expected 100, got {ov}'
assert conf == 0.3
print(f'PASS (no-history, non-first): overlap={ov}, conf={conf}')

# 无历史 + expected 过大需钳制
ov, conf = _apply_fallback(195, None, False, top, bot)
assert ov <= 170, f'Expected clamped <=170, got {ov}'
print(f'PASS (no-history, clamped): overlap={ov}, conf={conf}')
"
```

- [ ] **Step 4: Commit**

```bash
git add wxnote2html/stitch_v2.py
git commit -m "feat: add _apply_fallback for FR-7 fallback strategy"
```

---

### Task 4: 重写 `match_overlap` — 约束搜索 + FR-7/FR-8 集成

**Files:**
- Modify: `wxnote2html/stitch_v2.py` (替换现有 `match_overlap` 函数，约第170-219行)

- [ ] **Step 1: 替换 `match_overlap` 实现**

将现有的 `match_overlap` 函数（第170-219行）替换为：

```python
def match_overlap(
    top_gray: np.ndarray,
    bottom_gray: np.ndarray,
    expected: int,
    history: list[int] | None = None,
    is_first_pair: bool = False,
    max_ratio: float = 0.85,
) -> tuple[int, float]:
    """
    约束搜索 + 60行NCC校验 + 滑动窗口兜底 + 全局搜索 + 钳制。

    Args:
        top_gray: 前一张裁剪后的灰度图 (H×W, uint8)
        bottom_gray: 当前裁剪后的灰度图 (H×W, uint8)
        expected: 预期重叠量 (px)
        history: 最近 3 次成功匹配的 overlap 值
        is_first_pair: 是否为首对匹配（决定是否触发全局搜索）
        max_ratio: overlap/bottom_h 最大比例

    Returns:
        (overlap_px, confidence)
    """
    if not HAS_CV2:
        return expected, 0.5

    H = top_gray.shape[0]
    bottom_h = bottom_gray.shape[0]
    template_h = min(60, bottom_h)

    # ── 1. 约束搜索区 [expected × 0.2, expected × 2.0] ──
    half_win = max(int(expected * 0.8), 50)  # expected ± 80%
    if half_win < 100:
        half_win = 100  # 过窄自动扩展
    search_top = max(0, H - expected - half_win)
    search_bot = min(H - template_h, H - expected + half_win)

    if search_bot <= search_top:
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

    if max_val < 0.3:
        print(f"  [stitch_v2] 匹配相关系数低={max_val:.2f}, 进入兜底")
        return _apply_fallback(
            expected, history, is_first_pair, top_gray, bottom_gray, max_ratio
        )

    overlap = H - (search_top + max_loc[1])

    # ── 3. 60 行连续非空白 NCC 校验 ──
    conf = _verify_ncc(top_gray, bottom_gray, overlap, required=60)

    if conf < 1.0:
        print(f"  [stitch_v2] NCC校验不通过 (confidence={conf:.0%}), 进入兜底")
        overlap, conf = _apply_fallback(
            expected, history, is_first_pair, top_gray, bottom_gray, max_ratio
        )
    else:
        conf = 1.0

    # ── 4. FR-8 重叠量合理性防护 ──
    overlap = _clamp_overlap(overlap, bottom_h, max_ratio)

    return overlap, conf
```

- [ ] **Step 2: 验证语法**

```bash
python -c "from wxnote2html.stitch_v2 import match_overlap; print('OK')"
```

- [ ] **Step 3: 验证 — 正常匹配路径**

```bash
python -c "
import numpy as np
from wxnote2html.stitch_v2 import match_overlap

# 两张有明确 100px 重叠且内容一致的灰度图
H = 400
top = np.random.randint(50, 200, (H, 100), dtype=np.uint8)
bot = np.zeros((H, 100), dtype=np.uint8)
bot[:100, :] = top[-100:, :]  # 精确复制重叠区
bot[100:, :] = np.random.randint(50, 200, (H-100, 100), dtype=np.uint8)

ov, conf = match_overlap(top, bot, expected=100)
print(f'overlap={ov}, confidence={conf:.2f}')
# 重叠在 70-130 之间且置信度 1.0 视为通过
assert 70 < ov < 130, f'Expected ~100, got {ov}'
assert conf == 1.0, f'Expected 1.0, got {conf}'
print('PASS: normal match')
"
```

- [ ] **Step 4: 验证 — 低相似度触发兜底（有历史）**

```bash
python -c "
import numpy as np
from wxnote2html.stitch_v2 import match_overlap

H = 400
top = np.random.randint(50, 200, (H, 100), dtype=np.uint8)
bot = np.random.randint(50, 200, (H, 100), dtype=np.uint8)  # 完全不相关

# 有历史 → 使用历史中位数
ov, conf = match_overlap(top, bot, expected=100, history=[80, 90, 85])
print(f'fallback overlap={ov}, confidence={conf:.2f}')
assert ov == 85, f'Expected median 85, got {ov}'
assert conf == 0.5
print('PASS: fallback with history')
"
```

- [ ] **Step 5: 验证 — 低相似度触发兜底（无历史、非首对）**

```bash
python -c "
import numpy as np
from wxnote2html.stitch_v2 import match_overlap

H = 400
top = np.random.randint(50, 200, (H, 100), dtype=np.uint8)
bot = np.random.randint(50, 200, (H, 100), dtype=np.uint8)

# 无历史 + 非首对 → 使用 expected
ov, conf = match_overlap(top, bot, expected=100, history=None, is_first_pair=False)
print(f'fallback overlap={ov}, confidence={conf:.2f}')
assert ov == 100, f'Expected 100, got {ov}'
assert conf == 0.3
print('PASS: fallback with expected')
"
```

- [ ] **Step 6: Commit**

```bash
git add wxnote2html/stitch_v2.py
git commit -m "feat: rewrite match_overlap with constrained search + FR-7/FR-8"
```

---

### Task 5: 更新 `stitch()` — 滑动窗口历史管理

**Files:**
- Modify: `wxnote2html/stitch_v2.py` (`stitch()` 函数，约第259-352行)

- [ ] **Step 1: 在 `stitch()` 阶段2 的循环前添加历史窗口变量**

找到 `stitch()` 函数中阶段2 部分。在 `for i, img in enumerate(images):` 之前添加：

```python
    # 滑动窗口：记录最近 3 次成功匹配的 overlap
    history: list[int] = []
```

然后找到循环内 `ov, conf = match_overlap(prev_gray, gray, expected)` 这一行（约第331行），替换为：

```python
            is_first = (i == 1)
            ov, conf = match_overlap(
                prev_gray, gray, expected,
                history=history if history else None,
                is_first_pair=is_first,
            )
```

然后在 `print(f"  [{i}] overlap={ov}px conf={conf:.2f} {tag}")` 之后，添加历史记录逻辑：

```python
            # 成功匹配 → 记录到滑动窗口
            if conf >= 0.8:
                history.append(ov)
                if len(history) > 3:
                    history.pop(0)
```

完整的循环体变更后如下（仅展示修改部分的位置关系）：

```python
    # 滑动窗口：记录最近 3 次成功匹配的 overlap
    history: list[int] = []

    for i, img in enumerate(images):
        cropped = crop_image(img, header_h, footer_h)
        gray = np.array(cropped.convert("L"))
        color = np.array(cropped.convert("RGB"))

        print(f"  [cropped {i}] size={cropped.size} top={header_h} bottom={footer_h}")

        if debug_dir:
            from pathlib import Path
            d = Path(debug_dir)
            d.mkdir(parents=True, exist_ok=True)
            cropped.save(str(d / f"cropped_{i:03d}.png"))
            if i == 0:
                print(f"  [debug] saved to {debug_dir}/")

        if i == 0:
            canvas = color
        else:
            is_first = (i == 1)
            ov, conf = match_overlap(
                prev_gray, gray, expected,
                history=history if history else None,
                is_first_pair=is_first,
            )
            tag = "OK" if conf >= 0.8 else ("WARN" if conf >= 0.5 else "LOW")
            print(f"  [{i}] overlap={ov}px conf={conf:.2f} {tag}")

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

            # 首对匹配成功后，用实际值校准
            if i == 1 and conf >= 0.8:
                expected = ov

        prev_gray = gray
        prev_color = color
```

- [ ] **Step 2: 验证语法**

```bash
python -c "import py_compile; py_compile.compile('wxnote2html/stitch_v2.py', doraise=True); print('OK')"
```

- [ ] **Step 3: 集成验证 — 多张图流式拼接**

```bash
python -c "
import numpy as np
from PIL import Image
from wxnote2html.stitch_v2 import stitch

# 模拟 5 张图，每张 500×200，滚动 350px (overlap=150)
H, W = 500, 200
scroll = 350
images = []
for i in range(5):
    arr = np.ones((H, W, 3), dtype=np.uint8) * (i * 40 + 30)
    # 相邻图重叠区域保持一致
    if i > 0:
        overlap_h = H - scroll
        arr[:overlap_h, :, :] = images[-1][-overlap_h:, :, :]
    images.append(arr)

result = stitch([Image.fromarray(a) for a in images], scroll_distance=scroll)
print(f'Result: {result.size}')
assert result.width == W
# 预期高度: H + (N-1) * (H - overlap) = 500 + 4 * 350 = 1900
expected_h = H + 4 * scroll
assert abs(result.height - expected_h) < 50, f'Expected ~{expected_h}, got {result.height}'
print('PASS: streaming stitch')
"
```

- [ ] **Step 4: Commit**

```bash
git add wxnote2html/stitch_v2.py
git commit -m "feat: add sliding window history management to stitch()"
```

---

### Task 6: 更新 `main.py` — 添加 CLI 参数

**Files:**
- Modify: `wxnote2html/main.py`

- [ ] **Step 1: 添加 `--confidence-threshold` 参数**

找到 `main.py` 的 argparse 段中 `--no-blend` 参数附近（约第67行），在其后添加：

```python
    parser.add_argument(
        "--confidence-threshold", type=float, default=0.6,
        help="匹配置信度低于此值输出警告 (默认 0.6)"
    )
    parser.add_argument(
        "--max-overlap-ratio", type=float, default=0.85,
        help="overlap 占下图高度的最大比例 (默认 0.85)"
    )
```

- [ ] **Step 2: 更新 `stitch_v2.stitch()` 调用，传入新参数**

当前 `stitch()` 签名不接受 `max_ratio` 参数，先更新 `stitch()` 的签名。找到 `stitch_v2.py` 中 `stitch()` 函数定义（约第259行），在参数列表中添加 `max_ratio`：

```python
def stitch(
    images: list[Image.Image],
    scroll_distance: int | None = None,
    blend: bool = True,
    debug_dir: str | None = None,
    max_ratio: float = 0.85,
) -> Image.Image:
```

然后在 `stitch()` 内部调用 `match_overlap` 时传入 `max_ratio`：

```python
            ov, conf = match_overlap(
                prev_gray, gray, expected,
                history=history if history else None,
                is_first_pair=is_first,
                max_ratio=max_ratio,
            )
```

回到 `main.py`，更新 stitch 调用行（约第136-140行），传入 `max_ratio`：

```python
        if args.images:
            stitched = stitch_v2.stitch(
                images, blend=use_blend, debug_dir=debug_dir,
                max_ratio=args.max_overlap_ratio,
            )
        else:
            stitched = stitch_v2.stitch(
                images, scroll_distance=scroll_dist, blend=use_blend,
                debug_dir=debug_dir, max_ratio=args.max_overlap_ratio,
            )
```

- [ ] **Step 3: 验证语法**

```bash
python -c "import py_compile; py_compile.compile('wxnote2html/main.py', doraise=True); print('OK')"
python -c "import py_compile; py_compile.compile('wxnote2html/stitch_v2.py', doraise=True); print('OK')"
```

- [ ] **Step 4: 验证 CLI 参数生效**

```bash
python -m wxnote2html.main --help 2>&1 | grep -E "confidence-threshold|max-overlap"
```

预期输出包含两行帮助文本。

- [ ] **Step 5: Commit**

```bash
git add wxnote2html/stitch_v2.py wxnote2html/main.py
git commit -m "feat: add --confidence-threshold and --max-overlap-ratio CLI params"
```

---

### Task 7: 端到端回归测试

**Files:**
- 无新建文件

- [ ] **Step 1: 测试完整管线 — ADB 模式模拟**

```bash
python -c "
import numpy as np
from PIL import Image
from wxnote2html.stitch_v2 import stitch

# 模拟 20 张 1080×2400 截图，含顶部 150px header + 底部 80px footer
H, W = 2400, 1080
header_h = 150
footer_h = 80
cropped_h = H - header_h - footer_h  # 2170
scroll = 1800
overlap = cropped_h - scroll  # 370

images = []
rng = np.random.RandomState(42)
for i in range(20):
    arr = rng.randint(200, 256, (H, W, 3), dtype=np.uint8)
    # 顶部 header: 所有图相同
    arr[:header_h, :, :] = 60
    # 底部 footer: 所有图相同
    arr[-footer_h:, :, :] = 30
    # 重叠区: 精确复制前一张
    if i > 0:
        arr[header_h:header_h+overlap, :, :] = \
            images[-1][header_h+cropped_h-overlap:header_h+cropped_h, :, :]
    images.append(arr)

result = stitch(
    [Image.fromarray(a) for a in images],
    scroll_distance=scroll,
)

expected_h = cropped_h + (19) * (cropped_h - overlap)
actual_h = result.height
ratio = actual_h / expected_h
print(f'Expected height: ~{expected_h}, actual: {actual_h}, ratio: {ratio:.2f}')
assert 0.85 < ratio < 1.15, f'Height mismatch: {ratio}'
print('PASS: 20-image ADB mode')
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
rng = np.random.RandomState(42)
for i in range(5):
    arr = rng.randint(200, 256, (H, 200, 3), dtype=np.uint8)
    arr[:120, :, :] = 50  # header
    if i > 0:
        # 模拟 300px 重叠
        arr[:300, :, :] = images[-1][-300:, :, :]
    images.append(arr)

result = stitch([Image.fromarray(a) for a in images])
print(f'File mode result: {result.size}')
assert result.height > H
assert result.width == 200
print('PASS: file mode')
"
```

- [ ] **Step 3: 测试单张截图**

```bash
python -c "
import numpy as np
from PIL import Image
from wxnote2html.stitch_v2 import stitch

arr = np.ones((500, 200, 3), dtype=np.uint8) * 128
result = stitch([Image.fromarray(arr)])
print(f'Single image: {result.size}')
assert result.size == (200, 500)
print('PASS: single image')
"
```

- [ ] **Step 4: 测试 overlap 越界钳制**

```bash
python -c "
import numpy as np
from wxnote2html.stitch_v2 import match_overlap

# 两张完全不相关的图，expected 过大
H = 400
top = np.ones((H, 100), dtype=np.uint8) * 200
bot = np.ones((H, 100), dtype=np.uint8) * 50

# expected=395 → 搜索区会触发 search_bot <= search_top → 自动进入兜底
ov, conf = match_overlap(top, bot, expected=395, is_first_pair=False)
# 兜底使用 expected，但会被 FR-8 钳制（expected >= bottom_h-10）
assert ov <= 370, f'Expected clamped, got {ov}'
assert conf < 1.0, f'Expected low confidence, got {conf}'
print(f'PASS: clamp test — overlap={ov}, conf={conf}')
"
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test: add end-to-end regression tests for stitch_v2"
```

---

### Task 8: 最终验证 — 全管线运行

**Files:**
- 无新建文件

- [ ] **Step 1: 确认所有模块可导入**

```bash
python -c "
from wxnote2html.stitch_v2 import (
    detect_header, detect_footer, crop_image,
    match_overlap, blend_seam, stitch,
    _clamp_overlap, _global_search, _apply_fallback,
    _verify_ncc, _row_ncc, _row_std,
)
print('All imports OK')
"
```

- [ ] **Step 2: 运行全部上述测试**

确认 Task 1-7 的所有验证步骤通过。

- [ ] **Step 3: Commit (如有剩余修改)**

```bash
git add -A
git commit -m "chore: final verification of stitch_v2"
```
