# PinShot

面向 **GNOME Wayland** 的轻量截图 / 标注 / 贴图工具，Python + GTK3 单文件实现。

> A lightweight screenshot / annotate / pin-on-screen tool for GNOME Wayland, written in Python + GTK3.

PinShot 主要解决两个在 GNOME Wayland 下很难绕开的问题：

- 截图入口：复用 `gnome-screenshot` 的原生选区 / 窗口 / 全屏 UI；
- 贴图置顶：让贴图窗口跑在 XWayland 下，通过 `_NET_WM_STATE_ABOVE` 实现置顶（GNOME Wayland 不允许普通客户端强制置顶）。

## 功能

- 三种截图入口：**鼠标框选 / 当前活动窗口 / 全屏**
- 标注编辑器：
  - 箭头、文字（Pango，中文正常）、**矩形框、椭圆框、多点多边形**、马赛克
  - 颜色、线宽、字号可调
  - 撤销 / 重做
  - 复制到剪贴板、保存 PNG、贴图
- 贴图窗口：无边框、置顶、拖动、滚轮缩放、双击关闭、右键复制 / 关闭
- 常驻服务 + GNOME 自定义快捷键，支持开机自启
- 单实例（`Gtk.Application` + D-Bus），无 root、无系统级安装

## 环境

测试环境：Ubuntu 22.04 / GNOME 42 / Wayland / Python 3.10。

依赖：

| 依赖 | 用途 |
|---|---|
| `python3`, `python3-gi`, `gir1.2-gtk-3.0`, `python3-cairo` | GUI / 绘图 |
| `gnome-screenshot` | 截图入口 |
| `wl-clipboard` (`wl-copy`, `wl-paste`) | 剪贴板图片 |
| `libnotify-bin` (`notify-send`) | 通知（可选） |

Ubuntu / Debian：

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 python3-cairo gnome-screenshot wl-clipboard libnotify-bin
```

## 安装

```bash
git clone https://github.com/orange-zhouhq/orange_pinshot.git
cd orange_pinshot
./install.sh
```

`install.sh` 会：

1. 复制 `pinshot.py` 到 `~/.local/share/pinshot/`
2. 创建 `~/.local/bin/pinshot` 软链
3. 创建 `~/.config/autostart/pinshot.desktop`
4. 打印 GNOME 自定义快捷键命令

然后在 **设置 → 键盘 → 自定义快捷键** 中添加：

| 快捷键 | 命令 |
|---|---|
| `Ctrl+F1` | `pinshot.py area` |
| `Ctrl+F2` | `pinshot.py window` |
| `Ctrl+F4` | `pinshot.py full` |
| `Ctrl+F3` | `pinshot.py pin` |

## 使用

- `Ctrl+F1/F2/F4` 截图后会打开标注编辑器；画完按 **Enter** 或点“贴图”。
- `Ctrl+F3`：
  - 有编辑器打开时，贴编辑器里标注后的图；
  - 否则贴剪贴板图片，剪贴板没有图片则贴最近一次截图。
- 多边形工具：逐点点击，**双击 / 右键 / 回车** 闭合，`Esc` 取消当前多边形。
- 编辑器内快捷键：`Ctrl+Z` 撤销、`Ctrl+Shift+Z` 重做、`Ctrl+C` 复制、`Ctrl+S` 保存、`Enter` 贴图、`Esc` 关闭。

## 设计说明

- **截图**：`gnome-screenshot -a/-w/-f`，Wayland 下走 GNOME 原生 UI。
- **置顶**：`GDK_BACKEND=x11` 让窗口成为 XWayland 窗口，`set_keep_above(True)` 设置 `_NET_WM_STATE_ABOVE`；编辑器打开时也会临时置顶，失焦后自动取消。
- **单实例**：`Gtk.Application` + D-Bus，快捷键只负责把命令发给常驻进程。
- **剪贴板**：`wl-copy --type image/png` / `wl-paste`，最近截图保存在 `$XDG_RUNTIME_DIR/pinshot-last.png`。

## 已知限制

- 依赖 `gnome-screenshot`；新版 GNOME 正在逐步弃用它，后续可改用 `xdg-desktop-portal` 截图。
- “自动窗口”是**当前活动窗口**，不是鼠标悬停高亮。
- 标注不能选中后拖动 / 删除单条，只能撤销 / 重做。
- 马赛克块大小固定 12px。
- 仅在 Ubuntu 22.04 + GNOME 42 Wayland 下测试；其他桌面环境可能需要调整。

## License

MIT
