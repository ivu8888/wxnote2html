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

### 基本用法

```bash
python run.py                    # 显示帮助
python run.py --run              # 默认输出 note.pdf
python run.py --run -o x.pdf     # 指定输出
python run.py --run -f html      # 输出 HTML 格式
python run.py --run --debug      # 调试模式
```

### 输出格式

```bash
python run.py --run               # 默认 PDF
python run.py --run -f html       # 输出 HTML (base64 内嵌图片)
python run.py --run -f png        # 输出 PNG 图片
python run.py --run -o out.pdf    # 根据后缀推断格式
```

### 操作步骤

1. 手机连接电脑，确认：`adb devices`
2. 手机上打开微信 → 收藏 → 目标笔记 → **滑到最顶部**
3. 运行 `python run.py --run`
4. 按回车后自动截图拼接

### 其他选项

```bash
# 已有截图，直接拼接
python run.py --run --images ./screenshots/ -o note.pdf

# 输出 PNG
python run.py --run -f png -o stitched.png

# 多设备时指定
python run.py --run --device 192.168.0.100:5555

# 调试：详细日志 + 匹配调试图 + tmp/debug.log
python run.py --run --debug
```

## 拼接引擎

V2 拼接引擎特性：

- **多图投票** 检测顶部微信 UI 和底部导航栏
- **约束搜索** [0.1× ~ 2.0× expected] + NCC 60行连续校验
- **FR-7 兜底** 滑动窗口中位数 → 全局搜索 → 几何估算
- **FR-8 钳制** overlap 越界自动防护
- **自适应滚动** 基于内容高度计算滚动距离，适配不同屏幕
- **per-pair 滚动距离** 每次滚动独立追踪
- **流式内存** 100张截图峰值不超过 200MB

## 注意事项

- 屏幕保持常亮，笔记页面在前台
- 滚到底部自动停止
- 默认输出 PDF 文件；也可输出 base64 内嵌图片的 HTML 或 PNG 图片

## License

MIT
