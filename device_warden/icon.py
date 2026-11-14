"""Draw the Device Warden icon.

OpenCode and Windows otherwise show Python's icon for the tray and window, so the
icon is drawn here instead of shipped as a generated bitmap. assets/app-icon.svg
holds the same artwork as the canonical vector source.
"""

import math

from PIL import Image, ImageDraw

# State colours let the tray show availability without opening the window.
COLORS = {
    'free': (53, 214, 164),      # available
    'human': (76, 154, 255),     # held by a person
    'ai': (255, 183, 77),        # held by an AI agent
    'recovery': (255, 92, 92),   # connection lost / needs attention
    'idle': (122, 139, 153),     # no devices known
    'starting': (122, 139, 153), # Broker not reachable yet
}
BACKGROUND = (20, 33, 46)
DRAW_SIZE = 512


def _arc(cx, cy, radius, start, end, steps=16):
    return [
        (cx + radius * math.cos(math.radians(start + (end - start) * i / steps)),
         cy + radius * math.sin(math.radians(start + (end - start) * i / steps)))
        for i in range(steps + 1)
    ]


def _curve(start, control, end, steps=32):
    points = []
    for index in range(steps + 1):
        t = index / steps
        u = 1 - t
        points.append((
            u * u * start[0] + 2 * u * t * control[0] + t * t * end[0],
            u * u * start[1] + 2 * u * t * control[1] + t * t * end[1],
        ))
    return points


def _shield(cx, top, bottom, half):
    """Return a shield outline: rounded top, straight sides, tapered bottom tip."""
    height = bottom - top
    radius = half * 0.28
    left, right = cx - half, cx + half
    shoulder = top + height * 0.52
    points = _arc(left + radius, top + radius, radius, 180, 270)
    points += _arc(right - radius, top + radius, radius, 270, 360)
    points.append((right, shoulder))
    points += _curve((right, shoulder), (right, top + height * 0.86), (cx, bottom))
    points += _curve((cx, bottom), (left, top + height * 0.86), (left, shoulder))
    return points


def image(state='free', size=64):
    """Render the icon at any size; state selects the shield colour."""
    color = COLORS.get(state, COLORS['free'])
    canvas = Image.new('RGBA', (DRAW_SIZE, DRAW_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((16, 16, DRAW_SIZE - 16, DRAW_SIZE - 16),
                           radius=112, fill=BACKGROUND + (255,))
    draw.polygon(_shield(DRAW_SIZE / 2, 104, 420, 148), fill=color + (255,))
    # A bold checkmark stays readable at 16px, unlike a phone silhouette.
    width = 44
    stroke = [(196, 262), (240, 306), (322, 212)]
    draw.line(stroke, fill=BACKGROUND + (255,), width=width, joint='curve')
    for x, y in (stroke[0], stroke[2]):
        draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2),
                     fill=BACKGROUND + (255,))
    return canvas.resize((size, size), Image.LANCZOS)


def icon_path():
    """Path to a generated .ico for Windows shortcuts and windows."""
    from .server import data_dir
    path = data_dir() / 'device-warden.ico'
    if not path.exists():
        assets = path.parent
        assets.mkdir(parents=True, exist_ok=True)
        image('free', DRAW_SIZE).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return path


def state_for(devices):
    """Pick the tray colour that matters most: attention first, then active owners."""
    if not devices:
        return 'idle'
    if any(device.get('lease_state') == 'recovery_required' for device in devices):
        return 'recovery'
    if any(device.get('kind') == 'human' for device in devices):
        return 'human'
    if any(device.get('kind') == 'ai' for device in devices):
        return 'ai'
    connections = [device.get('connection') for device in devices]
    if 'connected' in connections:
        return 'free'
    if 'unknown' in connections:
        return 'recovery'
    return 'idle'
