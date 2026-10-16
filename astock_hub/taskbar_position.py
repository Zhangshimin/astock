"""Position a horizontal quote strip using Windows screen rectangles."""

Rect = tuple[int, int, int, int]


def calculate_taskbar_position(
    taskbar: Rect, notification: Rect | None, monitor: Rect,
    width: int, height: int, mode: str = "taskbar", gap: int = 4,
) -> tuple[int, int]:
    left, top, right, bottom = taskbar
    ml, mt, mr, mb = monitor
    horizontal = right - left >= bottom - top
    if horizontal:
        # Explorer may recreate the notification window; ignore stale rectangles.
        valid_tray = notification is not None and left < notification[0] < right and notification[1] < bottom and notification[3] > top
        anchor = notification[0] if valid_tray else right
        x = max(left, anchor - width - gap)
        y = top + (bottom - top - height) // 2
        if mode == "above":
            y = bottom + gap if abs(top - mt) < abs(mb - bottom) else top - height - gap
    else:
        valid_tray = notification is not None and top < notification[1] < bottom and notification[0] < right and notification[2] > left
        anchor = notification[1] if valid_tray else bottom
        y = max(top, anchor - height - gap)
        on_left = abs(left - ml) < abs(mr - right)
        # A horizontal strip cannot fit inside a narrow vertical taskbar.
        x = left if on_left else right - width
        if mode == "above":
            x = right + gap if on_left else left - width - gap
    return (
        min(max(x, ml), max(ml, mr - width)),
        min(max(y, mt), max(mt, mb - height)),
    )
