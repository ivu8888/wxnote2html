# V2 拼接引擎设计文档

**日期**: 2026-06-04
**状态**: 已确认

---

## 1. 概述

V2 重写核心拼接算法。保留 V1 的 ADB 截图 + 文件输入双模式，重写 `stitch.py` 和 UI 检测模块，用逐行滑动匹配替代模板匹配，用多图投票替代双图比对。

## 2. 架构

```
run.py  →  main.py  ─┬─ capture.py   (ADB 截图, 不变)
                      ├─ stitch_v2.py (拼接引擎, 重写)
                      ├─ ocr_module.py (OCR, 不变)
                      └─ render.py    (HTML 渲染, 小改)
```

`stitch_v2.py` 是本次唯一的重点修改文件，接口与 V1 `stitch.py` 兼容。

## 3. 算法设计

### 3.1 顶部 UI 区域检测：多图投票

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

### 3.2 底部区域检测

```
信号A：末图从底部向上逐行扫描 std，找到空白→内容的边界 → 文章结束线
信号B：所有图底部共同区域（导航栏），同 header 投票法

footer_h = max(信号A, 信号B)  // 取较大值，宁可少裁不可多裁
```

### 3.3 核心拼接：约束搜索 + 10行校验

```
对相邻两张裁剪后的灰度图 (img_top, img_bottom)：

1. 计算候选搜索区
   已知滚动距离时：
     expected = 内容高度 - 滚动距离
   未知滚动距离（文件模式）：
     expected = 内容高度 * 0.2  // 默认 20% 重叠
     首对匹配成功后用实际值校准后续 expected
   search_region = img_top 底部 [expected * 0.5 : expected * 1.5] 区域
   // 仅在窄窗口内搜索，杜绝远处误匹配

2. cv2.matchTemplate 粗匹配（C 加速）
   template = img_bottom 顶部 1/8 屏高条
   在 search_region 中用 TM_CCOEFF_NORMED 匹配
   → best_offset
   // 归一化互相关对亮度变化不敏感，比 MSE 更鲁棒

3. 10行连续 NCC 校验（核心）
   从 best_offset 位置开始，逐行比对。
   空白行（行内 std < 8）直接跳过，不计入连续计数。
   必须找到连续 10 个非空白行，每行 NCC > 0.85 → 通过。
   不通过 → 用 expected 几何值兜底，日志标红 [LOW CONFIDENCE]
   
   confidence = 校验通过的非空白行数 / 10  （0.0 ~ 1.0）
   
   // 跳过空白行的原因：段落间距、文章末尾等空白区域，
   // NCC 天然接近 1.0，无法区分"正确对齐"与"错位但恰好是空白"

4. 裁切拼接
   img_bottom 从 best_offset 行开始取新增内容
   拼接到画布底部
   拼接缝处做 5px 线性渐变融合
```

**与 V1 模板匹配的关键区别**：

| | V1 | V2 |
|--|-----|-----|
| 搜索范围 | 全图 | 仅候选窗口 [0.5x ~ 1.5x expected] |
| 匹配算法 | TM_CCOEFF_NORMED | 同，但限定搜索区 |
| 校验 | 单一相关系数 > 0.6 | 10 行连续 NCC > 0.85 |
| 亮度变化 | 敏感 | NCC 归一化，自动适应 |
| 置信度 | 无 | confidence = 校验通过率 |
| 文件模式 | expected 固定 20% | 首对匹配后自动校准 |

### 3.4 拼接缝融合

```python
# 5px 线性渐变混合
blend_width = 5
alpha = np.linspace(0, 1, blend_width).reshape(-1, 1, 1)
blended = (img1_边缘 * (1 - alpha) + img2_边缘 * alpha).astype(np.uint8)
```

### 3.5 流式内存管理

100 张截图（每张 1080×2400 ≈ 7.8MB 解压后）不能一次性全部加载（780MB 会 OOM）。

**两阶段处理**：

```
阶段1：采样检测
  加载前 K(≤10) 张连续图 + 末 2 张 → 投票检测 header/footer → 释放
  峰值内存 ≈ 12 张 ≈ 94 MB

阶段2：流式拼接（匹配用灰度图，画布用彩色）
  canvas = None  (彩色 PIL Image)
  prev_gray = None  (前一张的灰度 np.array)
  for i in 1..N:
      img = Image.open(file_i).crop(top=header, bottom=H-footer)
      gray = np.array(img.convert("L"))
      if i == 1:
          canvas = img
      else:
          overlap, conf = match_overlap(prev_gray, gray, expected)
          canvas = 垂直拼接(canvas, img.crop((0, overlap, w, h)))
          expected = overlap  # 用实际值校准下一对
      prev_gray = gray
  峰值内存 ≈ 1 彩色+1 灰度图 + 画布 ≈ 18 MB
```

总峰值内存与截图张数无关，100 张和 1000 张一样。

## 4. 模块接口

### `stitch_v2.py`

```python
def detect_header(images: list[Image.Image]) -> int:
    """多图投票检测顶部UI高度，返回 px"""

def detect_footer(images: list[Image.Image]) -> int:
    """末图空白检测 + 多图底部投票，返回 px"""

def crop_ui(images: list[Image.Image], header: int, footer: int) -> list[Image.Image]:
    """统一裁剪所有图，返回裁剪后的列表"""

def match_overlap(top_gray: np.ndarray, bottom_gray: np.ndarray,
                  expected: int) -> tuple[int, float]:
    """约束搜索 + NCC 校验，返回 (overlap_px, confidence)
       top_gray, bottom_gray: 裁剪后的灰度图 (H×W, uint8)"""

def stitch(images: list[Image.Image],
           scroll_distance: int | None = None) -> Image.Image:
    """主入口，流式拼接，返回完整长图"""
```

### `main.py` 改动

```python
# 两种模式保持不变，统一调用 stitch_v2
stitch_v2 = _import_module("stitch_v2")
stitched = stitch_v2.stitch(images, scroll_distance=scroll_dist)
```

## 5. 参数控制

| 参数 | 默认 | 说明 |
|------|------|------|
| `--mode` | `adb` | `adb` 自动截图 / `file` 文件输入 |
| `--images` | - | 文件模式下截图目录 |
| `-o, --output` | `note.html` | 输出路径 |
| `--scroll-distance` | 自动 | 覆盖滚动距离 |
| `--no-blend` | false | 禁用拼接缝融合 |
| `--confidence` | 0.6 | 匹配置信度告警阈值 |
| `--no-ocr` | false | 跳过 OCR |

## 6. 错误/降级处理

| 场景 | 处理 |
|------|------|
| 10 行 NCC 校验失败 (confidence < 0.5) | 用几何估算值兜底，日志标红 `[LOW CONFIDENCE]` |
| header 投票全部为 0 | 回退到单图行内方差法 |
| 文件模式下首对匹配失败 | 用默认 20% 重叠估算，后续对用此校准 |
| 截图宽度不一致 | 统一缩放到最大宽度（白底填充），日志提示 |
| 仅 1 张截图 | 跳过拼接，直接输出裁剪后的单图 |
| 100+ 张截图 | 流式处理，进度条显示 |

## 7. 验收标准

- 20 张微信笔记截图自动生成肉眼无缝长图
- 无状态栏/导航栏残留
- 拼接缝处无重影、无像素错位
- 100 张截图处理不 OOM，耗时 < 30 秒
- 720p / 1080p / 1440p 三种分辨率手机均通过
