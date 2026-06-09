# wxnote2html

微信笔记自动截图 → 拼接 → HTML

```
┌──────────┐    ┌──────────┐    ┌───────────┐    ┌──────────┐
│ ADB 控制  │───→│ 自动滚动  │───→│ 图片拼接   │───→│  HTML   │
│ 打开笔记  │    │ 逐屏截取  │    │ 去重对齐   │    │ base64  │
└──────────┘    └──────────┘    └───────────┘    └──────────┘
```

## 前置条件

- Android 手机，开启 **USB 调试**
- USB 线连电脑（或 WiFi ADB）
- 电脑安装 Python 3.10+
- 电脑安装 ADB 工具

## 安装

```bash
cd wxnote2html
pip install -r requirements.txt
```

## 使用

### 1. 准备工作

1. 手机 USB 连电脑
2. 确认连接：
   ```bash
   adb devices
   # 应显示: xxxxxxxx  device
   ```
3. 手机上打开微信收藏 → 打开目标笔记 → 滑到**最顶部**

### 2. 一键运行

```bash
python run.py -o note.html
```

脚本会提示你按回车，然后自动：
- 逐屏截图 + 滚动
- 到达底部自动停止
- 拼接成一张长图
- 输出 HTML（图片内嵌为 base64）

### 3. 高级选项

```bash
# 已有截图，直接处理
python run.py --images ./screenshots/ -o note.html

# 指定设备（多台手机时）
python run.py --device 1234567890 -o note.html

# 输出 PNG 图片
python run.py -o stitched.png

# 保存原始截图到目录（调试）
python run.py --save-screenshots ./debug/ -o note.html
```

## 注意事项

- 手机屏幕**不要锁屏**，保持笔记页面在前台
- 滚到底部会自动停止（检测连续两张截图相似度）
- 截图分辨率为手机原始分辨率，拼接图可能很大

## License

MIT
