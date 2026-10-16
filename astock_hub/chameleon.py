from collections import Counter


def background_color(image, excluded: tuple[int, int, int, int]) -> str | None:
    """Dominant surrounding color, excluding the quote strip itself."""
    image = image.convert("RGB")
    left, top, right, bottom = excluded
    counts = Counter()
    totals = {}
    for y in range(image.height):
        for x in range(image.width):
            if left <= x < right and top <= y < bottom:
                continue
            rgb = image.getpixel((x, y))
            bucket = tuple(channel // 16 for channel in rgb)
            counts[bucket] += 1
            total = totals.setdefault(bucket, [0, 0, 0])
            for index, channel in enumerate(rgb):
                total[index] += channel
    if not counts:
        return None
    bucket, count = counts.most_common(1)[0]
    return '#{:02x}{:02x}{:02x}'.format(*(round(channel / count) for channel in totals[bucket]))


def foreground_color(background: str) -> str:
    rgb = [int(background[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    luminance = sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    return '#17191d' if luminance > 0.179 else '#f3f4f6'
