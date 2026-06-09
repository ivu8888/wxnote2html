#!/usr/bin/env python3
"""
wxnote2html — 微信笔记自动截图 → 拼接 → HTML

用法:
    python run.py --run                     # 全自动模式，输出 note.html
    python run.py --run -o my_note.html     # 指定输出文件
    python run.py --run --images ./screens/ # 已有截图拼接
    python run.py --run --debug             # 调试模式
    python run.py                           # 显示本帮助
"""

import argparse
import base64
import importlib
import io
import re
import sys
from pathlib import Path

# 强制 UTF-8 输出，解决 Windows bash 中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from PIL import Image


def _natural_key(path: Path) -> tuple:
    """自然排序：将文件名中的数字按数值排序，避免 10 < 2"""
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r'(\d+)', path.name)
    )


def _import_module(name: str):
    """兼容 package 和直接运行的 import"""
    try:
        if __package__:
            return importlib.import_module(f".{name}", package=__package__)
        else:
            raise ImportError
    except ImportError:
        return importlib.import_module(name)


def _encode_image(img: Image.Image, fmt: str = "PNG") -> str:
    """将 PIL Image 编码为 base64 字符串"""
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _cleanup_tmp():
    """清理 tmp/ 目录中的缓存图片"""
    import shutil
    tmp = Path("tmp")
    if tmp.is_dir():
        shutil.rmtree(tmp)
        print("[main] 已清理 tmp/ 缓存")


def main():
    parser = argparse.ArgumentParser(
        description="微信笔记截图转 HTML",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --run                              # 默认输出 note.html
  %(prog)s --run -o my_note.html              # 指定输出文件
  %(prog)s --run --images ./screens/          # 已有截图拼接
  %(prog)s --run --debug                      # 调试模式
  %(prog)s --run --max-screens 30             # 最多30张截图
        """,
    )
    parser.add_argument("--run", action="store_true", help="执行截图拼接（必选）")
    parser.add_argument("-o", "--output", default="note.html", help="输出文件路径 (默认 note.html)")
    parser.add_argument("--images", help="已有截图目录（按文件名排序）")
    parser.add_argument("--device", help="ADB 设备序列号")
    parser.add_argument("--max-screens", type=int, default=20, help="最大截图张数 (默认 20)")
    parser.add_argument("--scroll-distance", type=int, help="每次滚动距离(px)")
    parser.add_argument("--no-blend", action="store_true", help="禁用拼接缝融合")
    parser.add_argument("--save-screenshots", help="保存原始截图到自定义目录")
    parser.add_argument("--debug", action="store_true", help="调试模式：详细日志 + 匹配调试图保存到 tmp/")
    parser.add_argument(
        "--mode", choices=["adb", "file"], default=None,
        help="输入模式: adb (自动截图) / file (文件导入)"
    )
    parser.add_argument(
        "--confidence-threshold", type=float, default=0.6,
        help="匹配置信度低于此值输出警告 (默认 0.6)"
    )
    parser.add_argument(
        "--max-overlap-ratio", type=float, default=0.85,
        help="overlap 占下图高度的最大比例 (默认 0.85)"
    )
    args = parser.parse_args()

    # 无参数时显示帮助
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    # ── 1. 获取截图 ──
    images: list[Image.Image] = []

    if args.images:
        img_dir = Path(args.images)
        if not img_dir.is_dir():
            print(f"错误: 目录不存在: {img_dir}")
            sys.exit(1)
        files = sorted(img_dir.glob("*.png"), key=_natural_key) + sorted(img_dir.glob("*.jpg"), key=_natural_key)
        if not files:
            print(f"错误: 目录中没有 png/jpg 文件: {img_dir}")
            sys.exit(1)
        print(f"[main] 加载 {len(files)} 张截图...")
        for f in files:
            images.append(Image.open(f))
    else:
        capture = _import_module("capture")
        ADBCapture = capture.ADBCapture
        save_screenshots = capture.save_screenshots

        devices = ADBCapture.list_devices()
        if not devices:
            print("错误: 未检测到 ADB 设备。请确保:")
            print("  1. 手机已开启 USB 调试")
            print("  2. USB 线已连接并授权")
            print("  3. adb devices 能看到设备")
            sys.exit(1)

        device = args.device or devices[0]
        print(f"[main] 使用设备: {device} (共 {len(devices)} 台)")

        cap = ADBCapture(device)
        print("[main] 请在手机上打开微信笔记到顶部，然后回车继续...")
        input()

        images, scroll_distances = cap.capture_scroll(
            max_screens=args.max_screens,
            scroll_distance=args.scroll_distance,
            debug=args.debug,
        )

        if args.save_screenshots:
            save_screenshots(images, Path(args.save_screenshots))

    if not images:
        print("错误: 没有截图")
        sys.exit(1)

    # ── 2. 拼接 ──
    stitch_v2 = _import_module("stitch_v2")
    use_blend = not args.no_blend
    if args.images:
        stitched = stitch_v2.stitch(
            images, blend=use_blend,
            max_ratio=args.max_overlap_ratio,
            debug=args.debug,
        )
    else:
        stitched = stitch_v2.stitch(
            images, scroll_distances=scroll_distances, blend=use_blend,
            max_ratio=args.max_overlap_ratio,
            debug=args.debug,
        )

    # ── 3. 输出 ──
    output = Path(args.output)
    if output.suffix.lower() in (".png", ".jpg", ".jpeg"):
        stitched.save(output)
        print(f"[main] 图片已保存: {output}")
    else:
        b64 = _encode_image(stitched)
        html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>微信笔记</title>
<style>body{{margin:0;background:#f5f5f5;text-align:center}}
img{{max-width:100%;height:auto;box-shadow:0 2px 10px rgba(0,0,0,0.1)}}</style>
</head><body><img src="data:image/png;base64,{b64}" alt="微信笔记"></body></html>"""
        output.write_text(html, encoding="utf-8")
        print(f"[main] HTML 已保存: {output}")

    # 非 debug 模式清理 tmp/ 缓存
    if not args.debug:
        _cleanup_tmp()


if __name__ == "__main__":
    main()
