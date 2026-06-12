# wxnote2html

微信笔记自动截图 → 拼接 → HTML

```
ADB 截图 → 多图投票去UI → 约束搜索+NCC校验 → PDF/HTML(base64)
```

## 前置条件

- Android 手机 + **USB 调试** + USB/WiFi 连接
- Python 3.10+
- ADB 工具

## 安装

```bash
git clone https://github.com/ivu8888/wxnote2html.git
cd wxnote2html
pip install -r requirements.txt
```

## 使用

### 操作步骤

1. 手机连接电脑，确认：`adb devices`
2. 手机上打开微信 → 收藏 → 目标笔记 → **滑到最顶部**
3. 运行命令，按回车后自动截图拼接

### 完整参数列表

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--run` | flag | — | **必选**，执行截图拼接 |
| `-o`, `--output` | path | `note.pdf` | 输出文件路径 |
| `-f`, `--format` | pdf/html/png | pdf | 输出格式（优先级高于 `-o` 后缀推断） |
| `--mode` | adb/file | adb | 输入模式：adb 自动截图 / file 文件导入 |
| `--images` | path | — | 已有截图目录（`--mode file` 时使用） |
| `--device` | str | — | ADB 设备序列号（多设备时指定） |
| `--max-screens` | int | 200 | 最大截图张数 |
| `--scroll-distance` | int | 自动 | 每次滚动距离(px)，不传则自动计算 |
| `--no-blend` | flag | — | 禁用拼接缝融合 |
| `--save-screenshots` | path | — | 保存原始截图到自定义目录 |
| `--confidence-threshold` | float | 0.6 | 匹配置信度低于此值输出警告 |
| `--max-overlap-ratio` | float | 0.85 | overlap 占下图高度的最大比例 |
| `--debug` | flag | — | 调试模式：详细日志+匹配调试图 |

---

### 示例

```bash
# ========== 基础用法 ==========

# 最简：自动截图 → 输出 note.pdf
python run.py --run

# 指定输出文件名（根据后缀自动推断格式）
python run.py --run -o 我的笔记.pdf
python run.py --run -o 我的笔记.html
python run.py --run -o 拼接图.png

# 指定输出格式（-f 优先级高于 -o 后缀）
python run.py --run -f pdf  -o output.bin      # 输出 PDF
python run.py --run -f html -o output          # 输出 HTML（无后缀文件）
python run.py --run -f png  -o stitched.png    # 输出 PNG


# ========== 调试模式 ==========

# 开启调试：详细日志 + 匹配调试图 → tmp/ 目录
python run.py --run --debug

# 调试 + 指定输出
python run.py --run --debug -o note.pdf

# 调试 + 自定义 debug 输出目录（配合 --save-screenshots）
python run.py --run --debug --save-screenshots ./debug/
# 生成文件:
#   ./debug/screen_000.png  ...  原始截图
#   tmp/debug.log                详细日志
#   tmp/cropped_*.png            裁剪后图片
#   tmp/match_*_search.png       匹配搜索区调试图
#   tmp/match_*_template.png     匹配模板调试图


# ========== 已有截图直接拼接 ==========

# 从目录导入已截图
python run.py --run --images ./screenshots/

# 指定格式和输出
python run.py --run --images ./screenshots/ -o note.pdf

# 调试：分析已有截图的匹配过程
python run.py --run --images ./screenshots/ --debug


# ========== 多设备 ==========

# ADB 连接多台设备时指定序列号
python run.py --run --device R5CY115407T

# 通过 WiFi 连接的设备
python run.py --run --device 192.168.0.100:5555


# ========== 控制截图 ==========

# 长文章用更多截图上限
python run.py --run --max-screens 500

# 手动指定滚动距离（不推荐，默认自动计算更好）
python run.py --run --scroll-distance 1200

# 保存原始截图以便复用
python run.py --run --save-screenshots ./raw_screens/


# ========== 拼接参数调优 ==========

# 放宽匹配置信度阈值（默认 0.6）
python run.py --run --confidence-threshold 0.4

# 提高阈值更严格
python run.py --run --confidence-threshold 0.8

# 调整最大重叠比例（默认 0.85）
python run.py --run --max-overlap-ratio 0.70

# 禁用拼接缝融合（观察原始拼接效果）
python run.py --run --no-blend


# ========== 组合用法 ==========

# 完整调试：指定全部参数
python run.py --run --debug -o 完整笔记.pdf --max-screens 300 \
    --confidence-threshold 0.6 --max-overlap-ratio 0.85 \
    --save-screenshots ./debug_screens/

# 已有截图 + 调试 + 指定参数
python run.py --run --images ./screenshots/ --debug \
    -o stitched.pdf --confidence-threshold 0.5 --no-blend

# WiFi 设备 + 调试
python run.py --run --device 192.168.0.100:5555 --debug -o wifi_note.pdf

## 拼接引擎

V2 拼接引擎特性：

- **多图投票** 检测顶部微信 UI、底部导航栏、右侧滚动条
- **约束搜索** [0.1× ~ 2.0× expected] + NCC 60行连续校验
- **双向末图匹配** 正向+反向模板匹配，解决文章末尾内容稀疏导致的匹配失败
- **FR-7 兜底** 滑动窗口中位数 → 全局搜索 → 几何估算
- **FR-8 钳制** overlap 越界自动防护
- **自适应滚动** 基于内容高度计算滚动距离，动态调整（切字缩短/空白恢复）
- **per-pair 滚动距离** 每次滚动独立追踪，校准预期重叠
- **流式内存** 200张截图峰值不超过 300MB
- **滚动条检测** 多图列 std 投票，统一右侧裁剪，适配所有分辨率
- **触底重试** 检测到底后用小滚动补截剩余内容

## 注意事项

- 屏幕保持常亮，笔记页面在前台
- 滚到底部自动停止
- 默认输出 PDF 文件；也可输出 base64 内嵌图片的 HTML 或 PNG 图片

## License

MIT
