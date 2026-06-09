# V2 拼接引擎设计文档

**日期**: 2026-06-09
**状态**: 已实施

---

## 1. 背景与目标

### 1.1 背景

V1 拼接引擎存在以下问题：
- 顶部状态栏/底部导航栏残留
- 亮度变化导致模板匹配失败
- 长列表（>50 张图）内存溢出
- 缺少置信度评估，错误静默传递
- `input swipe` 手势距离 ≠ 微信实际内容滚动距离，导致 overlap 远小于预期

### 1.2 目标

- 自动去除截图中的状态栏、导航栏、微信笔记固定 UI
- 高鲁棒性图像匹配（支持亮度变化、动态滚动）
- **杜绝因重叠量估算错误导致的图像中间内容截断**
- 支持 100+ 张截图流式处理，不占用超额内存
- 提供置信度输出，支持降级策略
- **自适应滚动校准**：根据首张截图估算内容高度，滚动距离基于内容高度而非屏幕高度

---

## 2. 架构

```
run.py  →  main.py  ─┬─ capture.py   (ADB 截图 + 自适应滚动)
                      └─ stitch_v2.py (拼接引擎: 多图投票 + 约束搜索 + NCC校验)
```

输出为 HTML（内嵌 base64 长图）或 PNG，无需 OCR。

---

## 3. 微信笔记 UI 结构

微信笔记页面的固定 UI 结构如下：

```
┌─────────────────┐
│    状态栏        │  ← 系统状态栏
├─────────────────┤
│  微信    ···     │  ← 微信标题栏
├─────────────────┤
│ ─── 细线 ───    │  ← 分割线 1
│      来自       │  ← 来源标注
│ ─── 细线 ───    │  ← 分割线 2
├─────────────────┤
│                 │
│   笔记内容区域   │  ← header_h 应裁剪到这里
│                 │
│      ...        │
│                 │
├─────────────────┤
│   底部导航栏     │  ← footer 区域
└─────────────────┘
```

**header_h** = 状态栏 + 微信标题栏 + 双细线 + "来自" 区域的高度，裁剪后笔记内容从第二条细线下方开始。

---

## 4. 算法设计

### 4.1 顶部 UI 区域检测：多图投票

```
输入：前 K 张连续截图 (K = min(N, 10))

对每对相邻图 (i, i+1) for i in 0..K-2:
  1. 取顶部 15% 区域，转灰度
  2. 逐行计算两图 MSE
  3. 找到 MSE 从接近 0 跃升到显著值的拐点 → 该对的 header 候选

对 K-1 个候选取中位数 → 统一 header_h

兜底：若中位数为 0，用首图的单图行内方差法
```

**为什么多图比双图好**：单对图可能在笔记顶部有大图/空白导致误判，多对投票可剔除异常值。取前 K 张而非全部 N 张，因为 header 在所有图中一致，无需加载全部。

### 4.2 底部区域检测

```
信号A：末图从底部向上逐行扫描 std，找到空白→内容的边界 → 文章结束线
信号B：所有图底部共同区域（导航栏），同 header 投票法

footer_h = max(信号A, 信号B)  // 取较大值，宁可少裁不可多裁
```

### 4.3 统一裁剪

每张截图裁剪 `[header_h, H - footer_h]` 区域作为有效内容。所有图使用统一的 header_h 和 footer_h。

### 4.4 核心拼接：约束搜索 + 60行NCC校验

```
对相邻两张裁剪后的灰度图 (img_top, img_bottom)：

1. 计算候选搜索区
   有滚动距离列表时：
     expected = 内容高度 - scroll_distances[pair_idx]  // 每对独立计算
   无滚动距离（文件模式）：
     expected = 内容高度 × 0.2  // 默认 20% 重叠
   search_range = [expected × 0.1, expected × 2.0]  // ±90% 覆盖滚动波动
   最小 100 行宽度

2. cv2.matchTemplate 粗匹配（C 加速）
   template = img_bottom 顶部 60 行
   在搜索区内用 TM_CCOEFF_NORMED 匹配
   → candidate_offset

3. 60行连续非空白 NCC 校验（核心）
   从 candidate_offset 位置开始，逐行比对。
   空白行（行内 std < 8）跳过，不中断连续计数。
   非空白行 NCC > 0.75 → 通过，连续计数 +1。
   非空白行 NCC ≤ 0.75 → 重置连续计数为 0。
   必须凑齐连续 60 个非空白行，每行 NCC > 0.75 → 通过。
   不通过 → 进入兜底策略（见 4.5）。

   confidence = 通过的连续非空白行数 / 60  （0.0 ~ 1.0）
```

**自适应预期更新**：每次成功匹配（conf ≥ 0.8）后，用实际 overlap 校准下一对的 expected；匹配失败时用对应 scroll_distances 重算。

### 4.5 校验失败的兜底策略（FR-7）

```
维护滑动窗口：记录最近 3 次成功匹配（confidence ≥ 0.8）的 overlap 值

若 NCC 校验失败（confidence < 1.0）：
  ├─ 有历史数据（≥1 个）
  │     fallback_overlap = 历史中位数
  │     输出: [stitch_v2] 使用历史中位数兜底 overlap={fallback}
  │
  └─ 无历史数据
        ├─ 首对匹配 → 触发全局搜索：
        │     在上图上 10%~90% 区域用 cv2.matchTemplate 重新匹配
        │     ├─ 全局匹配成功（max_val ≥ 0.3）
        │     │     overlap = 全局匹配结果
        │     │     输出: [stitch_v2] 全局搜索成功
        │     └─ 全局匹配失败
        │           overlap = expected（钳制后）
        │           输出: [stitch_v2] 全局搜索失败, 使用估算值
        │
        └─ 非首对 → 使用 expected 几何估算值（钳制后）
```

### 4.6 重叠量合理性防护（FR-8）

无论校验是否通过，**每次匹配后**均执行以下检查：

```
bottom_h = img_bottom 裁剪后的高度

若 overlap >= bottom_h - 10
  → 钳制为 max(0, bottom_h - 30)
  → 红色警告: [stitch_v2] OVERLAP: overlap 过大, 已钳制

若 overlap < 0
  → 钳制为 0
  → 红色警告: [stitch_v2] OVERLAP: overlap 负值, 已钳制为0

若 overlap > bottom_h × max_ratio（默认 0.85）
  → 黄色警告: [stitch_v2] HIGH OVERLAP
  → 不自动修正（保留现场供调试）
```

### 4.7 拼接缝融合

```python
# 5px 线性渐变混合
blend_width = 5
alpha = np.linspace(0, 1, blend_width).reshape(-1, 1, 1)
blended = (prev_bottom * (1 - alpha) + new_top * alpha).astype(np.uint8)
```

可通过 `--no-blend` 关闭。

### 4.8 自适应滚动校准

`capture_scroll()` 在截图前先估算内容高度，基于内容高度而非屏幕高度计算滚动距离：

```python
# 1. 先截首张图
first_img = self.screenshot()

# 2. 方差法估算 header，减去固定 footer 估算
content_h = _estimate_content_height(first_img)

# 3. 基于内容高度计算滚动距离
scroll_distance = int(content_h * (1 - overlap_ratio))
```

这解决了 `input swipe` 手势距离 ≠ 微信实际滚动距离的问题——虽然无法精确校准滑动比例，但使用内容高度作为基准比使用屏幕高度更接近目标 overlap。

### 4.9 流式内存管理

两阶段处理：

```
阶段1：采样检测
  加载前 K(≤10) 张连续图 + 末 2 张 → 投票检测 header/footer → 释放
  峰值内存 ≈ 12 张 ≈ 94 MB

阶段2：流式拼接（匹配用灰度图，画布用彩色）
  canvas = None  (彩色 np.array)
  prev_gray = None  (前一张的灰度 np.array)
  for i in 1..N:
      img = Image.open(file_i).crop(top=header, bottom=H-footer)
      gray = np.array(img.convert("L"))
      if i == 1:
          canvas = color
      else:
          overlap, conf = match_overlap(prev_gray, gray, expected, ...)
          canvas = 垂直拼接(canvas, color[overlap:, :, :])
          expected = overlap  # 用实际值校准下一对
      prev_gray = gray
  峰值内存 ≈ 1 彩色+1 灰度图 + 画布 ≈ 18 MB
```

总峰值内存与截图张数无关，100 张和 1000 张一样。

---

## 5. 模块接口

### `stitch_v2.py`

```python
def _scale(h: int, ratio: float, floor: int = 10) -> int:
    """h * ratio, 保证不小于 floor"""

def _row_std(gray: np.ndarray) -> np.ndarray:
    """逐行计算像素标准差"""

def _row_ncc(row1: np.ndarray, row2: np.ndarray) -> float:
    """两行之间的归一化互相关系数"""

def _clamp_overlap(overlap: int, bottom_h: int, max_ratio: float = 0.85) -> int:
    """FR-8 重叠量合理性防护"""

def _verify_ncc(top_gray, bottom_gray, overlap, required=60) -> float:
    """N 行连续非空白 NCC 校验，返回 confidence"""

def _global_search(top_gray, bottom_gray, template_h=60) -> tuple[int | None, float]:
    """全局搜索兜底（首对匹配失败时）"""

def _apply_fallback(expected, history, is_first_pair, top_gray, bottom_gray, max_ratio) -> tuple[int, float]:
    """FR-7 兜底策略"""

def _write_log(log_dir: str, *lines: str, mode: str = "a"):
    """向 debug.log 追加带时间戳的日志行"""

def detect_header(images: list[Image.Image]) -> int:
    """多图投票检测顶部UI高度，返回 px"""

def detect_footer(images: list[Image.Image], last_image: Image.Image) -> int:
    """末图空白检测 + 多图底部投票，返回 px"""

def crop_image(img: Image.Image, header: int, footer: int) -> Image.Image:
    """统一裁剪单张图"""

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
    """约束搜索 + 60行NCC校验 + 滑动窗口兜底 + 全局搜索 + 钳制
    返回 (overlap_px, confidence)"""

def blend_seam(
    canvas: np.ndarray, new_strip: np.ndarray,
    prev_bottom: np.ndarray, blend_width: int = 5,
) -> np.ndarray:
    """拼接缝线性渐变融合"""

def stitch(
    images: list[Image.Image],
    scroll_distances: list[int] | None = None,
    blend: bool = True,
    max_ratio: float = 0.85,
    debug: bool = False,
) -> Image.Image:
    """主入口，流式拼接，返回完整长图"""
```

### `capture.py`

```python
class ADBCapture:
    def list_devices() -> list[str]: ...
    def get_screen_size(self) -> tuple[int, int]: ...
    def screenshot(self) -> Image.Image: ...
    def scroll_down(self, distance=None, duration_ms=500) -> int: ...
    def images_similar(img1, img2, threshold=0.98) -> bool: ...
    def capture_scroll(
        self, max_screens=30, overlap_ratio=0.4, scroll_distance=None,
        pause_ms=800, save_dir="tmp", debug=False,
    ) -> tuple[list[Image.Image], list[int]]:  # 返回 (截图, 每次滚动距离列表)

def _estimate_content_height(first_img: Image.Image) -> int: ...
def _capture_log(save_dir: Path, *lines: str, mode: str = "a"): ...
def save_screenshots(images: list[Image.Image], output_dir: Path): ...
```

### `main.py` CLI

```python
parser.add_argument("-o", "--output", default="note.html")
parser.add_argument("--images")           # 已有截图目录
parser.add_argument("--device")           # ADB 设备序列号
parser.add_argument("--max-screens", type=int, default=20)
parser.add_argument("--scroll-distance", type=int)
parser.add_argument("--no-blend", action="store_true")
parser.add_argument("--save-screenshots")  # 保存原始截图到自定义目录
parser.add_argument("--debug", action="store_true")  # 调试模式
parser.add_argument("--confidence-threshold", type=float, default=0.6)
parser.add_argument("--max-overlap-ratio", type=float, default=0.85)
```

---

## 6. 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-o, --output` | `note.html` | 输出路径 (.html 或 .png) |
| `--images` | - | 文件模式下截图目录 |
| `--device` | 自动 | ADB 设备序列号 |
| `--max-screens` | 20 | 最大截图张数 |
| `--scroll-distance` | 自动 | 手动指定滚动距离 (px)，不指定则基于内容高度×60% |
| `--no-blend` | false | 禁用拼接缝融合 |
| `--save-screenshots` | - | 保存原始截图到自定义目录 |
| `--debug` | false | 调试模式：详细控制台日志 + 匹配调试图 + `tmp/debug.log` |
| `--confidence-threshold` | 0.6 | 置信度低于此值输出警告 |
| `--max-overlap-ratio` | 0.85 | overlap/bottom_h 最大比例 |

---

## 7. 错误/降级处理

| 场景 | 处理 |
|------|------|
| NCC 校验失败 + 有历史 | 用最近 3 次成功中位数兜底，日志提示 |
| NCC 校验失败 + 无历史（首对） | 全局搜索（上 10%~90%），再失败用 expected |
| NCC 校验失败 + 无历史（非首对） | 用 per-pair scroll_distance 重算 expected |
| overlap ≥ bottom_h - 10 | 钳制为 max(0, bottom_h - 30)，红色警告 |
| overlap < 0 | 钳制为 0，红色警告 |
| overlap > bottom_h × 0.85 | 黄色警告，不自动修正 |
| header 投票全部为 0 | 回退到单图行内方差法 |
| template match 相关系数 < 0.3 | 跳过 NCC 校验，直接进入兜底 |
| 截图宽度不一致 | 统一缩放到最大宽度（白底填充），日志提示 |
| 仅 1 张截图 | 跳过拼接，直接输出裁剪后的单图 |
| OpenCV 不可用 | 回退到几何估算拼接 |
| 100+ 张截图 | 流式处理，进度条显示 |
| `input swipe` ≠ 实际滚动 | 基于内容高度计算滚动距离；stitch 模板匹配自适应校准 |

---

## 8. 非功能需求

### 8.1 性能
- 100 张 1080×2400 截图处理时间 ≤ 30 秒（普通 PC）
- 内存占用峰值 ≤ 200 MB

### 8.2 鲁棒性
- 支持 720p / 1080p / 1440p 三种常见分辨率
- 亮度变化（日间/夜间模式）不影响匹配成功率 > 95%
- 单个截图尺寸不一致时，统一缩放、白边填充，不中断整体流程
- 微信滑动距离 ≠ swipe 手势距离时，模板匹配自适应校准

### 8.3 可维护性
- 核心拼接逻辑集中在 `stitch_v2.py`
- 关键算法步骤（校验、兜底、钳制）有详细注释和日志输出

### 8.4 日志与调试
- 控制台输出每对匹配的 `overlap`、置信度、是否使用兜底
- `--debug` 模式下：
  - 详细控制台日志（搜索区、corr、NCC 通过数、per-cropped 尺寸）
  - 每对匹配保存调试图：`tmp/match_{idx:03d}_search.png`、`tmp/match_{idx:03d}_template.png`
  - 写入结构化日志文件 `tmp/debug.log`，包含：时间戳、屏幕尺寸、每张截图位置、每次滚动距离/累计高度、每对匹配详情、最终拼接尺寸
- 默认始终保存原始截图和裁剪图到 `tmp/` 目录

### 8.5 中文编码
- 所有入口文件（run.py, main.py, capture.py, stitch_v2.py）启动时调用 `sys.stdout.reconfigure(encoding="utf-8")`
- 日志文件使用 UTF-8 编码

---

## 9. 验收标准

| 编号 | 测试场景 | 预期结果 |
|------|----------|----------|
| AC-01 | 20 张微信笔记截图（adb 模式） | 生成的长图无状态栏/导航栏/微信UI，拼接缝不可见，内容完整无缺失 |
| AC-02 | 文件模式（未知滚动距离） | 生成完整长图，无中间图像被截断 |
| AC-03 | 夜间模式截图（亮度偏低） | 匹配成功，置信度 > 0.8，拼接正常 |
| AC-04 | 100 张截图 | 处理时间 ≤ 30 秒，内存 ≤ 200 MB，最终长图完整 |
| AC-05 | 仅 1 张截图 | 直接输出裁剪后的单图，不报错 |
| AC-06 | 某对截图匹配失败（极低相似度） | 使用历史中位数或钳制值兜底，输出警告，最终图仍可看 |
| AC-07 | 手动指定 `--scroll-distance` | 按给定值计算 expected，搜索区据此生成 |
| AC-08 | overlap 越界（过大/负值） | 触发钳制防护，输出红色警告，拼接继续 |
| AC-09 | `--debug` 模式 | 生成 `tmp/debug.log`、匹配调试图、详细控制台日志 |

---

## 10. 风险与应对

| 风险 | 影响 | 应对措施 |
|------|------|----------|
| 真实滚动距离极小（<10% 屏高） | 默认重叠偏大，overlap 可能 > bottom_h | FR-8 钳制防护；历史中位数调整 |
| 截图包含大段空白（文章结尾） | 难以凑齐 60 个非空白行 | 空白行跳过不中断计数；仍失败则用兜底策略 |
| 不同分辨率混用 | 缩放后像素对齐偏差 | 统一缩放到最大宽度，白边填充，告警提示 |
| 微信 UI 结构变化 | header 检测可能失效 | 多图投票 + 方差兜底；日志输出候选值便于调试 |
| `input swipe` ≠ 实际滚动距离 | overlap 远小于预期，内容可能缺失 | 基于内容高度计算初始滚动距离；stitch 模板匹配自适应每对 expected |
| 轻量级 overlap 估算不可靠 | 无法在 capture 阶段精确校准滚动距离 | 依赖 stitch 阶段的模板匹配正确找到实际 overlap；滑动窗口历史中位数兜底 |
