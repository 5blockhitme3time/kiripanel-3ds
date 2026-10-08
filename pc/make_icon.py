"""Draws pc/kiripanel.ico (run once; the .ico is kept in the repo)."""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))


def draw(size):
    s = size * 4                                  # supersample, then shrink
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = s // 5
    d.rounded_rectangle((0, 0, s - 1, s - 1), r, fill=(47, 127, 214, 255))
    # two stacked screens, as on the console
    m = s // 6
    d.rounded_rectangle((m, m, s - m, s // 2 - s // 24), s // 20, fill=(230, 240, 255, 255))
    d.rounded_rectangle((m + s // 12, s // 2 + s // 24, s - m - s // 12, s - m), s // 20,
                        fill=(255, 198, 92, 255))
    # a line of "text" on the lower screen
    y = s // 2 + s // 24 + (s - m - s // 2 - s // 24) // 2
    d.rounded_rectangle((m + s // 6, y - s // 40, s - m - s // 6, y + s // 40), s // 80,
                        fill=(58, 47, 30, 255))
    return im.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    sizes = [16, 24, 32, 48, 64, 128, 256]
    big = draw(256)
    big.save(os.path.join(HERE, "kiripanel.ico"), sizes=[(n, n) for n in sizes])
    big.save(os.path.join(HERE, "kiripanel", "icon.png"))
    print("wrote kiripanel.ico")
