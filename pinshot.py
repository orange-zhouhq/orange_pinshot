#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PinShot —— 轻量截图 / 标注 / 贴图工具（GNOME Wayland 环境，贴图窗口走 XWayland 以支持置顶）

用法:
    pinshot.py            启动常驻服务（供开机自启使用）
    pinshot.py area       鼠标框选截图 -> 打开编辑器
    pinshot.py window     自动框选当前活动窗口 -> 打开编辑器
    pinshot.py full       全屏截图 -> 打开编辑器
    pinshot.py pin        贴图：优先贴当前活动编辑器；否则贴剪贴板/最近一次截图
    pinshot.py quit       退出服务并关闭所有窗口

快捷键约定:
    Ctrl+F1  鼠标框选
    Ctrl+F2  自动框选当前窗口
    Ctrl+F4  全屏
    Ctrl+F3  贴图（编辑器激活时贴编辑器内容，否则贴剪贴板/最近截图）

编辑器内:
    箭头 / 文字 / 马赛克，颜色、线宽、字号可调；Ctrl+Z 撤销，Ctrl+Shift+Z 重做，
    Ctrl+C 复制，Ctrl+S 保存，Enter 贴图，Esc 关闭。
"""
import os
import sys
import math
import time
import shutil
import subprocess
import tempfile
import warnings

# 必须在导入 Gtk 之前设置：窗口走 XWayland，Mutter 才会认可置顶属性
os.environ.setdefault("GDK_BACKEND", "x11")

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, Gio, GLib, Pango, PangoCairo

warnings.filterwarnings("ignore", category=DeprecationWarning)

APP_ID = "com.zhq.PinShot"
FIT_FRACTION = 0.8      # 贴图最大占显示器的比例
EDITOR_MAX_W = 0.8      # 编辑器图片区域最大占显示器比例
EDITOR_MAX_H = 0.72
MIN_SCALE = 0.2
MAX_SCALE = 4.0
ZOOM_STEP = 1.1
MOSAIC_BLOCK = 12

RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
LAST_CAPTURE = os.path.join(RUNTIME_DIR, "pinshot-last.png")

SUGAR_CSS = """
.sugar-toolbar {
    background-color: alpha(@theme_bg_color, 0.94);
    border-radius: 14px;
    border: 1px solid alpha(@theme_fg_color, 0.10);
    padding: 4px;
    box-shadow: 0 2px 6px alpha(black, 0.25);
}
.sugar {
    min-width: 34px;
    min-height: 34px;
    padding: 0;
    border-radius: 9px;
    background-image: none;
    border: none;
    box-shadow: none;
}
.sugar:hover {
    background-image: none;
    background-color: alpha(#808080, 0.18);
}
.sugar:active {
    background-image: none;
    background-color: alpha(#808080, 0.28);
}
.sugar:checked {
    background-image: none;
    background-color: @theme_selected_bg_color;
    color: @theme_selected_fg_color;
}
.sugar-label {
    font-size: 9pt;
}
.sugar-glyph {
    font-size: 16pt;
    font-weight: bold;
}
.sugar-value {
    font-size: 12pt;
    font-weight: bold;
}
.sugar:checked .sugar-label,
.sugar:checked .sugar-glyph,
.sugar:checked .sugar-value {
    color: @theme_selected_fg_color;
}
"""

_css_loaded = False


def load_css():
    global _css_loaded
    if _css_loaded:
        return
    provider = Gtk.CssProvider()
    try:
        provider.load_from_data(SUGAR_CSS.encode("utf-8"))
    except TypeError:
        provider.load_from_data(SUGAR_CSS)
    screen = Gdk.Screen.get_default()
    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    _css_loaded = True


def notify(message):
    if shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "-a", "PinShot", "-i", "image-x-generic", "PinShot", message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def load_pixbuf(path):
    try:
        return GdkPixbuf.Pixbuf.new_from_file(path)
    except GLib.Error:
        return None


def clipboard_image_types():
    try:
        out = subprocess.run(
            ["wl-paste", "--list-types"], capture_output=True, text=True, timeout=3
        )
    except Exception:
        return []
    if out.returncode != 0:
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip().startswith("image/")]


def read_clipboard_image():
    """把剪贴板中的图片落盘，返回临时文件路径；没有图片则返回 None。"""
    types = clipboard_image_types()
    types.sort(key=lambda t: 0 if t == "image/png" else 1)
    for mime in types:
        fd, path = tempfile.mkstemp(prefix="pinshot-", suffix=".img")
        os.close(fd)
        try:
            with open(path, "wb") as fh:
                result = subprocess.run(
                    ["wl-paste", "--type", mime, "--no-newline"],
                    stdout=fh,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            if result.returncode == 0 and os.path.getsize(path) > 0 and load_pixbuf(path):
                return path
        except Exception:
            pass
        try:
            os.unlink(path)
        except OSError:
            pass
    return None


def remove_quietly(path):
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def copy_pixbuf_to_clipboard(pixbuf):
    fd, path = tempfile.mkstemp(prefix="pinshot-copy-", suffix=".png")
    os.close(fd)
    try:
        pixbuf.savev(path, "png", [], [])
        handle = open(path, "rb")
        subprocess.Popen(
            ["wl-copy", "--type", "image/png"],
            stdin=handle,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        handle.close()
        GLib.timeout_add_seconds(5, lambda: (remove_quietly(path), False)[1])
        return True
    except Exception:
        remove_quietly(path)
        return False


def rgba_tuple(rgba):
    return (rgba.red, rgba.green, rgba.blue, rgba.alpha)


class PinWindow(Gtk.Window):
    """无边框置顶贴图窗口：拖动、滚轮缩放、右键菜单、双击关闭。"""

    def __init__(self, app, pixbuf, path=None, delete_on_close=True):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.app = app
        self.orig = pixbuf
        self.path = path
        self.delete_on_close = delete_on_close
        self.scale = 1.0
        self._w = self._h = 1

        self.set_title("PinShot")
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_resizable(False)
        self.set_accept_focus(False)  # 不抢焦点，贴图不打断当前工作

        self.image = Gtk.Image()
        self.add(self.image)

        display = Gdk.Display.get_default()
        if display is None:
            raise RuntimeError("无法连接 X11 显示（DISPLAY 未设置？）")
        pointer = display.get_pointer()
        px, py = int(pointer.x), int(pointer.y)
        monitor = display.get_monitor_at_point(px, py)
        geom = monitor.get_geometry()

        max_w = max(1, int(geom.width * FIT_FRACTION))
        max_h = max(1, int(geom.height * FIT_FRACTION))
        iw, ih = pixbuf.get_width(), pixbuf.get_height()
        self.scale = min(1.0, max_w / iw, max_h / ih)
        self._apply()

        x = min(max(px - self._w // 2, geom.x), geom.x + geom.width - self._w)
        y = min(max(py - self._h // 2, geom.y), geom.y + geom.height - self._h)
        self.move(x, y)

        self.connect("button-press-event", self._on_button)
        self.connect("scroll-event", self._on_scroll)
        self.connect("destroy", self._on_destroy)
        self.show_all()

    def _apply(self):
        self._w = max(1, int(self.orig.get_width() * self.scale))
        self._h = max(1, int(self.orig.get_height() * self.scale))
        scaled = self.orig.scale_simple(self._w, self._h, GdkPixbuf.InterpType.BILINEAR)
        self.image.set_from_pixbuf(scaled)
        self.resize(self._w, self._h)

    def _on_scroll(self, _widget, event):
        if event.direction == Gdk.ScrollDirection.UP:
            factor = ZOOM_STEP
        elif event.direction == Gdk.ScrollDirection.DOWN:
            factor = 1 / ZOOM_STEP
        else:
            return False
        new_scale = max(MIN_SCALE, min(MAX_SCALE, self.scale * factor))
        if abs(new_scale - self.scale) > 1e-6:
            self.scale = new_scale
            self._apply()
        return True

    def _on_button(self, _widget, event):
        if event.button == 1 and event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            self.destroy()
            return True
        if event.button == 1:
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
            return True
        if event.button == 3:
            menu = Gtk.Menu()
            copy_item = Gtk.MenuItem(label="复制到剪贴板")
            copy_item.connect("activate", self._copy)
            close_item = Gtk.MenuItem(label="关闭")
            close_item.connect("activate", lambda *_: self.destroy())
            menu.append(copy_item)
            menu.append(close_item)
            menu.show_all()
            menu.popup_at_pointer(event)
            return True
        return False

    def _copy(self, *_args):
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, "rb") as fh:
                    subprocess.Popen(
                        ["wl-copy", "--type", "image/png"],
                        stdin=fh,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                return
            except Exception:
                pass
        copy_pixbuf_to_clipboard(self.orig)
        notify("已复制到剪贴板")

    def _on_destroy(self, *_args):
        if self.app:
            self.app.remove_window(self)
        if self.delete_on_close:
            remove_quietly(self.path)


class EditorWindow(Gtk.Window):
    """截图后的标注编辑器：箭头 / 文字 / 马赛克。"""

    def __init__(self, app, pixbuf):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.app = app
        self.orig_pixbuf = pixbuf
        self.img_w = pixbuf.get_width()
        self.img_h = pixbuf.get_height()
        self.ops = []
        self.undo_index = 0
        self.tool = "arrow"
        self.drag_start = None
        self.preview_op = None
        self.polygon_points = []
        self.polygon_cursor = None
        self._updating_tools = False

        self.set_title("PinShot - 编辑")
        self.set_resizable(False)
        # 新建编辑器时先置顶，确保用户能看到；点到别的窗口后自动取消置顶
        self.set_keep_above(True)
        self.connect("focus-out-event", self._on_focus_out)

        display = Gdk.Display.get_default()
        pointer = display.get_pointer()
        monitor = display.get_monitor_at_point(pointer.x, pointer.y)
        geom = monitor.get_geometry()
        max_w = int(geom.width * EDITOR_MAX_W)
        max_h = int(geom.height * EDITOR_MAX_H)
        self.view_scale = min(1.0, max_w / self.img_w, max_h / self.img_h)
        self.area_w = max(1, int(self.img_w * self.view_scale))
        self.area_h = max(1, int(self.img_h * self.view_scale))

        toolbar = self._build_toolbar()

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(self.area_w, self.area_h)
        self.area.set_can_focus(True)
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.area.connect("draw", self.on_draw)
        self.area.connect("button-press-event", self.on_button_press)
        self.area.connect("button-release-event", self.on_button_release)
        self.area.connect("motion-notify-event", self.on_motion)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.pack_start(toolbar, False, False, 0)
        vbox.pack_start(self.area, False, False, 0)
        self.add(vbox)

        self.set_default_size(self.area_w, self.area_h + 72)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect("key-press-event", self.on_key)
        self.connect("destroy", self.on_destroy)

        self.base_surface = Gdk.cairo_surface_create_from_pixbuf(self.orig_pixbuf, 0, None)
        self.surface = None
        self.rebuild()
        self.show_all()
        self.present()
        self.area.grab_focus()
        self.tool_buttons["arrow"].set_active(True)
        self._set_cursor("crosshair")

    # ---------- 工具栏 ----------

    def _icon_button(self, icon, tooltip, text, toggle=False, glyph=None):
        button = Gtk.ToggleButton() if toggle else Gtk.Button()
        button.set_tooltip_text(tooltip)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_size_request(46, 48)
        button.set_focus_on_click(False)
        button.get_style_context().add_class("sugar")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        content.set_margin_top(3)
        content.set_margin_bottom(2)
        if glyph:
            top = Gtk.Label(label=glyph)
            top.get_style_context().add_class("sugar-glyph")
        else:
            top = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
        top.set_halign(Gtk.Align.CENTER)
        label = Gtk.Label(label=text)
        label.get_style_context().add_class("sugar-label")
        label.set_halign(Gtk.Align.CENTER)
        content.add(top)
        content.add(label)
        button.add(content)
        return button

    def _value_button(self, title, minimum, maximum, value, setter):
        button = Gtk.Button()
        button.set_tooltip_text(title)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_size_request(46, 48)
        button.set_focus_on_click(False)
        button.get_style_context().add_class("sugar")

        value_label = Gtk.Label(label=str(value))
        value_label.get_style_context().add_class("sugar-value")
        value_label.set_halign(Gtk.Align.CENTER)
        text_label = Gtk.Label(label=title)
        text_label.get_style_context().add_class("sugar-label")
        text_label.set_halign(Gtk.Align.CENTER)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        content.set_margin_top(3)
        content.set_margin_bottom(2)
        content.add(value_label)
        content.add(text_label)
        button.add(content)

        popover = Gtk.Popover.new(button)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.add(Gtk.Label(label=title))
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, minimum, maximum, 1)
        scale.set_value(value)
        scale.set_size_request(180, -1)
        scale.set_draw_value(True)
        box.add(scale)
        popover.add(box)
        box.show_all()
        popover.connect("show", self._popover_on_show)
        button.popover = popover
        button.connect("clicked", lambda *_: popover.popup())

        def on_change(widget):
            current = int(widget.get_value())
            value_label.set_text(str(current))
            setter(current)

        scale.connect("value-changed", on_change)
        return button

    def _separator(self):
        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.set_margin_top(6)
        sep.set_margin_bottom(6)
        sep.set_margin_start(3)
        sep.set_margin_end(3)
        return sep

    def _set_width(self, value):
        self.width_value = value

    def _set_font(self, value):
        self.font_value = value

    def _draw_color(self, widget, cr):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        cr.set_source_rgba(*self.color)
        self._rounded_rect(cr, 1, 1, width - 2, height - 2, 6)
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.25)
        cr.set_line_width(1)
        self._rounded_rect(cr, 1, 1, width - 2, height - 2, 6)
        cr.stroke()
        return False

    @staticmethod
    def _rounded_rect(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _popover_on_show(self, popover):
        window = popover.get_window()
        if window is None:
            toplevel = popover.get_toplevel()
            window = toplevel.get_window() if toplevel is not None else None
        if window is not None:
            window.set_keep_above(True)

    def _pick_color(self, *_args):
        dialog = Gtk.ColorChooserDialog(title="选择颜色", parent=self)
        dialog.set_rgba(Gdk.RGBA(*self.color))
        if dialog.run() == Gtk.ResponseType.OK:
            rgba = dialog.get_rgba()
            self.color = (rgba.red, rgba.green, rgba.blue, rgba.alpha)
            self._color_area.queue_draw()
        dialog.destroy()

    def _build_toolbar(self):
        load_css()
        self.width_value = 4
        self.font_value = 24

        bar = Gtk.Box(spacing=2)
        bar.get_style_context().add_class("sugar-toolbar")
        bar.set_halign(Gtk.Align.CENTER)
        bar.set_margin_top(6)
        bar.set_margin_bottom(6)
        bar.set_margin_start(6)
        bar.set_margin_end(6)

        self.tool_buttons = {}
        for name, icon, tip, text, glyph in (
            ("arrow", None, "箭头", "箭头", "↗"),
            ("text", None, "文字", "文字", "T"),
            ("rect", "edit-select-all-symbolic", "矩形框", "矩形", None),
            ("ellipse", None, "椭圆框", "椭圆", "○"),
            ("polygon", None, "多点多边形（双击或回车完成，Esc 取消）", "多边形", "◇"),
            ("mosaic", "view-grid-symbolic", "马赛克", "马赛克", None),
        ):
            btn = self._icon_button(icon, tip, text, toggle=True, glyph=glyph)
            btn.connect("toggled", self.on_tool_toggled, name)
            bar.pack_start(btn, False, False, 0)
            self.tool_buttons[name] = btn

        bar.pack_start(self._separator(), False, False, 0)

        self.color = (1.0, 0.0, 0.0, 1.0)
        color_button = Gtk.Button()
        color_button.set_tooltip_text("颜色")
        color_button.set_relief(Gtk.ReliefStyle.NONE)
        color_button.set_size_request(46, 48)
        color_button.set_focus_on_click(False)
        color_button.get_style_context().add_class("sugar")
        self._color_area = Gtk.DrawingArea()
        self._color_area.set_size_request(24, 24)
        self._color_area.set_halign(Gtk.Align.CENTER)
        self._color_area.set_valign(Gtk.Align.CENTER)
        self._color_area.connect("draw", self._draw_color)
        color_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        color_content.set_margin_top(3)
        color_content.set_margin_bottom(2)
        color_label = Gtk.Label(label="颜色")
        color_label.get_style_context().add_class("sugar-label")
        color_label.set_halign(Gtk.Align.CENTER)
        color_content.add(self._color_area)
        color_content.add(color_label)
        color_button.add(color_content)
        color_button.connect("clicked", self._pick_color)
        self.color_button = color_button
        bar.pack_start(color_button, False, False, 0)

        self.width_button = self._value_button("线宽", 1, 20, 4, self._set_width)
        self.font_button = self._value_button("字号", 10, 96, 24, self._set_font)
        bar.pack_start(self.width_button, False, False, 0)
        bar.pack_start(self.font_button, False, False, 0)

        bar.pack_start(self._separator(), False, False, 0)

        self.undo_button = self._icon_button("edit-undo-symbolic", "撤销 (Ctrl+Z)", "撤销")
        self.undo_button.connect("clicked", lambda *_: self.undo())
        self.redo_button = self._icon_button("edit-redo-symbolic", "重做 (Ctrl+Shift+Z)", "重做")
        self.redo_button.connect("clicked", lambda *_: self.redo())
        bar.pack_start(self.undo_button, False, False, 0)
        bar.pack_start(self.redo_button, False, False, 0)

        bar.pack_start(self._separator(), False, False, 0)

        for icon, tip, text, callback in (
            ("edit-copy-symbolic", "复制 (Ctrl+C)", "复制", self.copy),
            ("document-save-symbolic", "保存 (Ctrl+S)", "保存", self.save),
            ("view-pin-symbolic", "贴图 (Enter)", "贴图", self.pin),
            ("window-close-symbolic", "关闭 (Esc)", "关闭", self.destroy),
        ):
            button = self._icon_button(icon, tip, text)
            button.connect("clicked", lambda _b, cb=callback: cb())
            bar.pack_start(button, False, False, 0)

        return bar

    def on_tool_toggled(self, button, name):
        if self._updating_tools:
            return
        if button.get_active():
            self._updating_tools = True
            for other_name, other in self.tool_buttons.items():
                if other_name != name:
                    other.set_active(False)
            self._updating_tools = False
            self.tool = name
            if name != "polygon":
                self._cancel_polygon()
            self._set_cursor("crosshair")
        elif self.tool == name:
            self._cancel_polygon()
            self.tool = None
            self._set_cursor(None)

    def _set_cursor(self, name):
        window = self.area.get_window() if hasattr(self, "area") else None
        if not window:
            return
        if name:
            cursor = Gdk.Cursor.new_from_name(Gdk.Display.get_default(), name)
            window.set_cursor(cursor)
        else:
            window.set_cursor(None)

    def _on_focus_out(self, *_args):
        self.set_keep_above(False)
        return False

    # ---------- 绘制 / 撤销 ----------

    def rebuild(self):
        self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.img_w, self.img_h)
        cr = cairo.Context(self.surface)
        cr.set_source_surface(self.base_surface, 0, 0)
        cr.paint()
        for op in self.ops[: self.undo_index]:
            self.draw_op(cr, op, self.surface)
        self.area.queue_draw()
        if hasattr(self, "undo_button"):
            self.undo_button.set_sensitive(self.undo_index > 0)
            self.redo_button.set_sensitive(self.undo_index < len(self.ops))

    def on_draw(self, _widget, cr):
        cr.set_source_rgba(0.12, 0.12, 0.12, 1.0)
        cr.paint()
        cr.save()
        cr.scale(self.view_scale, self.view_scale)
        cr.set_source_surface(self.surface, 0, 0)
        cr.paint()
        cr.restore()
        if self.preview_op:
            cr.save()
            cr.scale(self.view_scale, self.view_scale)
            self.draw_op(cr, self.preview_op, self.surface, preview=True)
            cr.restore()
        if self.tool == "polygon" and self.polygon_points:
            cr.save()
            cr.scale(self.view_scale, self.view_scale)
            self._draw_polygon_preview(cr)
            cr.restore()
        return False

    def draw_op(self, cr, op, surface=None, preview=False):
        kind = op["type"]
        if kind == "arrow":
            x0, y0, x1, y1 = op["x0"], op["y0"], op["x1"], op["y1"]
            cr.set_source_rgba(*op["color"])
            cr.set_line_width(op["width"])
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.move_to(x0, y0)
            cr.line_to(x1, y1)
            cr.stroke()
            angle = math.atan2(y1 - y0, x1 - x0)
            size = max(10.0, op["width"] * 3.0)
            p1 = (x1 - size * math.cos(angle - math.pi / 6),
                  y1 - size * math.sin(angle - math.pi / 6))
            p2 = (x1 - size * math.cos(angle + math.pi / 6),
                  y1 - size * math.sin(angle + math.pi / 6))
            cr.move_to(x1, y1)
            cr.line_to(*p1)
            cr.line_to(*p2)
            cr.close_path()
            cr.fill()

        elif kind in ("rect", "ellipse"):
            x0, y0, x1, y1 = op["x0"], op["y0"], op["x1"], op["y1"]
            x, y = min(x0, x1), min(y0, y1)
            w, h = abs(x1 - x0), abs(y1 - y0)
            if w < 1 or h < 1:
                return
            cr.set_source_rgba(*op["color"])
            cr.set_line_width(op["width"])
            if preview:
                cr.set_dash([6, 4])
            if kind == "rect":
                cr.rectangle(x, y, w, h)
            else:
                cx, cy = x + w / 2.0, y + h / 2.0
                rx, ry = w / 2.0, h / 2.0
                cr.move_to(cx + rx, cy)
                steps = 72
                for step in range(1, steps + 1):
                    angle = 2 * math.pi * step / steps
                    cr.line_to(cx + rx * math.cos(angle), cy + ry * math.sin(angle))
                cr.close_path()
            cr.stroke()
            if preview:
                cr.set_dash([])

        elif kind == "polygon":
            points = op.get("points", [])
            if len(points) < 2:
                return
            cr.set_source_rgba(*op["color"])
            cr.set_line_width(op["width"])
            if preview:
                cr.set_dash([6, 4])
            cr.move_to(*points[0])
            for point in points[1:]:
                cr.line_to(*point)
            if op.get("closed", True):
                cr.close_path()
            cr.stroke()
            if preview:
                cr.set_dash([])

        elif kind == "text":
            layout = PangoCairo.create_layout(cr)
            layout.set_text(op["text"], -1)
            desc = Pango.FontDescription("Sans")
            desc.set_size(int(op["size"] * Pango.SCALE))
            layout.set_font_description(desc)
            cr.set_source_rgba(*op["color"])
            cr.move_to(op["x"], op["y"])
            PangoCairo.show_layout(cr, layout)

        elif kind == "mosaic":
            x, y, w, h = op["x"], op["y"], op["w"], op["h"]
            if preview:
                cr.set_source_rgba(0.5, 0.5, 0.5, 0.35)
                cr.rectangle(x, y, w, h)
                cr.fill()
                cr.set_source_rgba(0.15, 0.15, 0.15, 0.9)
                cr.set_line_width(1.5)
                cr.rectangle(x, y, w, h)
                cr.stroke()
                return
            if surface is None or w < 2 or h < 2:
                return
            x0 = max(0, int(x))
            y0 = max(0, int(y))
            x1 = min(self.img_w, int(x + w))
            y1 = min(self.img_h, int(y + h))
            w = x1 - x0
            h = y1 - y0
            if w < 2 or h < 2:
                return
            block = max(2, int(op.get("block", MOSAIC_BLOCK)))
            sw = max(1, w // block)
            sh = max(1, h // block)

            sub = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
            sub_cr = cairo.Context(sub)
            sub_cr.set_source_surface(surface, -x0, -y0)
            sub_cr.paint()

            small = cairo.ImageSurface(cairo.FORMAT_ARGB32, sw, sh)
            small_cr = cairo.Context(small)
            small_cr.scale(sw / w, sh / h)
            small_cr.set_source_surface(sub, 0, 0)
            small_cr.paint()

            cr.save()
            cr.rectangle(x0, y0, w, h)
            cr.clip()
            pattern = cairo.SurfacePattern(small)
            pattern.set_filter(cairo.FILTER_NEAREST)
            cr.translate(x0, y0)
            cr.scale(w / sw, h / sh)
            cr.set_source(pattern)
            cr.paint()
            cr.restore()

    # ---------- 鼠标交互 ----------

    def _img_coords(self, event):
        x = min(max(event.x / self.view_scale, 0), self.img_w)
        y = min(max(event.y / self.view_scale, 0), self.img_h)
        return x, y

    def on_button_press(self, _widget, event):
        if event.button == 3:
            if self.tool == "polygon" and self.polygon_points:
                self._finish_polygon()
                return True
            return False
        if event.button != 1 or not self.tool:
            return False
        self.area.grab_focus()
        ix, iy = self._img_coords(event)

        if self.tool == "text":
            self._add_text(ix, iy)
            return True

        if self.tool == "polygon":
            if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
                self._finish_polygon()
            else:
                self.polygon_points.append((ix, iy))
                self.polygon_cursor = (ix, iy)
                self.area.queue_draw()
            return True

        self.drag_start = (ix, iy)
        color = self.color
        if self.tool == "arrow":
            self.preview_op = {
                "type": "arrow", "x0": ix, "y0": iy, "x1": ix, "y1": iy,
                "color": color, "width": self.width_value,
            }
        elif self.tool in ("rect", "ellipse"):
            self.preview_op = {
                "type": self.tool, "x0": ix, "y0": iy, "x1": ix, "y1": iy,
                "color": color, "width": self.width_value,
            }
        else:
            self.preview_op = {
                "type": "mosaic", "x": ix, "y": iy, "w": 0, "h": 0,
                "block": MOSAIC_BLOCK,
            }
        self.area.queue_draw()
        return True

    def on_motion(self, _widget, event):
        if self.tool == "polygon" and self.polygon_points:
            self.polygon_cursor = self._img_coords(event)
            self.area.queue_draw()
            return True
        if not self.preview_op or not self.drag_start:
            return False
        ix, iy = self._img_coords(event)
        if self.preview_op["type"] == "arrow":
            self.preview_op["x1"] = ix
            self.preview_op["y1"] = iy
        elif self.preview_op["type"] in ("rect", "ellipse"):
            self.preview_op["x1"] = ix
            self.preview_op["y1"] = iy
        else:
            x0, y0 = self.drag_start
            self.preview_op["x"] = min(x0, ix)
            self.preview_op["y"] = min(y0, iy)
            self.preview_op["w"] = abs(ix - x0)
            self.preview_op["h"] = abs(iy - y0)
        self.area.queue_draw()
        return True

    def on_button_release(self, _widget, _event):
        if not self.preview_op:
            return False
        op = self.preview_op
        self.preview_op = None
        self.drag_start = None
        if op["type"] == "arrow":
            if math.hypot(op["x1"] - op["x0"], op["y1"] - op["y0"]) < 3:
                self.area.queue_draw()
                return True
        elif op["type"] == "mosaic":
            if op["w"] < 4 or op["h"] < 4:
                self.area.queue_draw()
                return True
        else:
            if abs(op["x1"] - op["x0"]) < 3 or abs(op["y1"] - op["y0"]) < 3:
                self.area.queue_draw()
                return True
            op["x0"], op["x1"] = min(op["x0"], op["x1"]), max(op["x0"], op["x1"])
            op["y0"], op["y1"] = min(op["y0"], op["y1"]), max(op["y0"], op["y1"])
        self._add_op(op)
        return True

    def _add_text(self, x, y):
        dialog = Gtk.Dialog(title="输入文字", transient_for=self, modal=True)
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        entry = Gtk.Entry()
        entry.set_width_chars(32)
        dialog.get_content_area().add(entry)
        dialog.show_all()
        entry.grab_focus()
        response = dialog.run()
        text = entry.get_text()
        dialog.destroy()
        self.present()
        if response == Gtk.ResponseType.OK and text.strip():
            self._add_op({
                "type": "text", "x": x, "y": y, "text": text,
                "color": self.color,
                "size": self.font_value,
            })

    def _add_op(self, op):
        self.ops = self.ops[: self.undo_index]
        self.ops.append(op)
        self.undo_index += 1
        self.rebuild()

    def _cancel_polygon(self):
        if self.polygon_points or self.polygon_cursor is not None:
            self.polygon_points = []
            self.polygon_cursor = None
            self.area.queue_draw()

    def _finish_polygon(self):
        if len(self.polygon_points) >= 3:
            self._add_op({
                "type": "polygon",
                "points": list(self.polygon_points),
                "color": self.color,
                "width": self.width_value,
                "closed": True,
            })
        self.polygon_points = []
        self.polygon_cursor = None
        self.area.queue_draw()

    def _draw_polygon_preview(self, cr):
        points = self.polygon_points
        if not points:
            return
        cr.set_source_rgba(*self.color)
        cr.set_line_width(self.width_value)
        cr.set_dash([6, 4])
        cr.move_to(*points[0])
        for point in points[1:]:
            cr.line_to(*point)
        if self.polygon_cursor:
            cr.line_to(*self.polygon_cursor)
        cr.stroke()
        cr.set_dash([])
        radius = max(2.0, self.width_value)
        for point in points:
            cr.arc(point[0], point[1], radius, 0, 2 * math.pi)
            cr.fill()

    def undo(self):
        if self.undo_index > 0:
            self.undo_index -= 1
            self.rebuild()

    def redo(self):
        if self.undo_index < len(self.ops):
            self.undo_index += 1
            self.rebuild()

    # ---------- 导出 ----------

    def render_pixbuf(self):
        return Gdk.pixbuf_get_from_surface(self.surface, 0, 0, self.img_w, self.img_h)

    def copy(self):
        if self.polygon_points:
            self._finish_polygon()
        if copy_pixbuf_to_clipboard(self.render_pixbuf()):
            notify("已复制到剪贴板")
        else:
            notify("复制失败")

    def save(self):
        if self.polygon_points:
            self._finish_polygon()
        dialog = Gtk.FileChooserDialog(
            title="保存图片",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK,
        )
        dialog.set_do_overwrite_confirmation(True)
        pictures = os.path.expanduser("~/图片")
        if os.path.isdir(pictures):
            dialog.set_current_folder(pictures)
        dialog.set_current_name("PinShot-%s.png" % time.strftime("%Y%m%d-%H%M%S"))
        if dialog.run() == Gtk.ResponseType.OK:
            path = dialog.get_filename()
            try:
                self.render_pixbuf().savev(path, "png", [], [])
                notify("已保存: %s" % path)
            except Exception as exc:
                notify("保存失败: %s" % exc)
        dialog.destroy()

    def pin(self):
        if self.polygon_points:
            self._finish_polygon()
        self.app.show_pin(self.render_pixbuf(), None, False)
        self.destroy()

    # ---------- 键盘 / 生命周期 ----------

    def on_key(self, _widget, event):
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        key = event.keyval
        if key == Gdk.KEY_Escape:
            if self.polygon_points:
                self._cancel_polygon()
            else:
                self.destroy()
            return True
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if self.polygon_points:
                self._finish_polygon()
            else:
                self.pin()
            return True
        if ctrl and key == Gdk.KEY_z:
            self.redo() if shift else self.undo()
            return True
        if ctrl and key == Gdk.KEY_y:
            self.redo()
            return True
        if ctrl and key == Gdk.KEY_c:
            self.copy()
            return True
        if ctrl and key == Gdk.KEY_s:
            self.save()
            return True
        return False

    def on_destroy(self, *_args):
        self.app.remove_editor(self)


class PinShotApp(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.pins = []
        self.editors = []

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()  # 常驻：没有窗口时也不退出

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        command = args[0] if args else ""
        if command in ("area", "snip"):
            GLib.idle_add(lambda: (self.capture("area"), False)[1])
        elif command == "window":
            GLib.idle_add(lambda: (self.capture("window"), False)[1])
        elif command == "full":
            GLib.idle_add(lambda: (self.capture("full"), False)[1])
        elif command == "pin":
            GLib.idle_add(lambda: (self.pin(), False)[1])
        elif command == "quit":
            for editor in list(self.editors):
                editor.destroy()
            for win in list(self.pins):
                win.destroy()
            self.release()
            self.quit()
        elif command in ("", "daemon"):
            pass
        else:
            print("用法: pinshot.py [area|window|full|pin|quit]", file=sys.stderr)
        return 0

    def add_window(self, window):
        self.pins.append(window)

    def remove_window(self, window):
        if window in self.pins:
            self.pins.remove(window)

    def add_editor(self, editor):
        self.editors.append(editor)

    def remove_editor(self, editor):
        if editor in self.editors:
            self.editors.remove(editor)

    def capture(self, mode):
        """三种截图模式；成功后复制到剪贴板 + 保存最近一次截图 + 打开编辑器。"""
        fd, path = tempfile.mkstemp(prefix="pinshot-", suffix=".png")
        os.close(fd)
        if mode == "area":
            args = ["gnome-screenshot", "-a", "-f", path]
        elif mode == "window":
            args = ["gnome-screenshot", "-w", "-f", path]
        else:
            args = ["gnome-screenshot", "-f", path]

        # gnome-screenshot 需要走 Wayland/portal；不要让它继承 GDK_BACKEND=x11
        child_env = os.environ.copy()
        child_env.pop("GDK_BACKEND", None)
        result = subprocess.run(args, env=child_env)
        if result.returncode != 0 or not os.path.exists(path) or os.path.getsize(path) == 0:
            remove_quietly(path)
            return

        try:
            handle = open(path, "rb")
            subprocess.Popen(
                ["wl-copy", "--type", "image/png"],
                stdin=handle,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            handle.close()
        except Exception:
            pass

        try:
            shutil.copyfile(path, LAST_CAPTURE)
        except Exception:
            pass

        pixbuf = load_pixbuf(path)
        remove_quietly(path)
        if pixbuf is None:
            notify("截图读取失败")
            return
        self._show_editor(pixbuf)

    def _show_editor(self, pixbuf):
        try:
            editor = EditorWindow(self, pixbuf)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            notify("无法打开编辑器: %s" % exc)
            return
        self.add_editor(editor)

    def pin(self):
        # 编辑器激活时，Ctrl+F3 直接贴编辑器当前内容；只开了一个编辑器时也用它的内容
        editor = next((e for e in self.editors if e.is_active()), None)
        if editor is None and len(self.editors) == 1:
            editor = self.editors[0]
        if editor is not None:
            editor.pin()
            return

        path = read_clipboard_image()
        delete_on_close = True
        if not path and os.path.exists(LAST_CAPTURE):
            path = LAST_CAPTURE
            delete_on_close = False
        if not path:
            notify("剪贴板里没有图片")
            return

        pixbuf = load_pixbuf(path)
        if pixbuf is None:
            if delete_on_close:
                remove_quietly(path)
            notify("图片读取失败")
            return
        self.show_pin(pixbuf, path, delete_on_close)

    def show_pin(self, pixbuf, path, delete_on_close):
        try:
            window = PinWindow(self, pixbuf, path, delete_on_close)
        except Exception as exc:
            if delete_on_close:
                remove_quietly(path)
            notify("无法创建贴图窗口: %s" % exc)
            return
        self.add_window(window)


def main():
    app = PinShotApp()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
