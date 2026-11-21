#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/pinshot"
BIN_DIR="$HOME/.local/bin"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

missing=()
for cmd in python3 gnome-screenshot wl-copy wl-paste; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "缺少依赖: ${missing[*]}" >&2
    echo "Ubuntu/Debian: sudo apt install python3-gi gir1.2-gtk-3.0 python3-cairo gnome-screenshot wl-clipboard libnotify-bin" >&2
    exit 1
fi

mkdir -p "$DEST_DIR" "$BIN_DIR" "$AUTOSTART_DIR"
install -m 755 "$SRC_DIR/pinshot.py" "$DEST_DIR/pinshot.py"
ln -sf "$DEST_DIR/pinshot.py" "$BIN_DIR/pinshot"

cat > "$AUTOSTART_DIR/pinshot.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=PinShot
GenericName=截图贴图
Comment=PinShot 截图/标注/贴图常驻服务
Exec=/usr/bin/python3 "$DEST_DIR/pinshot.py"
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=3
EOF

echo "PinShot 已安装:"
echo "  程序: $DEST_DIR/pinshot.py"
echo "  命令: $BIN_DIR/pinshot"
echo "  自启: $AUTOSTART_DIR/pinshot.desktop"
echo
echo "请在 GNOME 设置 -> 键盘 -> 自定义快捷键中添加："
echo "  Ctrl+F1  ->  /usr/bin/python3 \"$DEST_DIR/pinshot.py\" area"
echo "  Ctrl+F2  ->  /usr/bin/python3 \"$DEST_DIR/pinshot.py\" window"
echo "  Ctrl+F4  ->  /usr/bin/python3 \"$DEST_DIR/pinshot.py\" full"
echo "  Ctrl+F3  ->  /usr/bin/python3 \"$DEST_DIR/pinshot.py\" pin"
echo
echo "首次使用可以直接运行其中一条命令，PinShot 会自动常驻。"
