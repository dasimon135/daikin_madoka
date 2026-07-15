"""Generate the daikin_madoka brand icon (BRC1H-inspired dial).

Renders at high resolution then downscales for clean anti-aliasing.
Outputs the files home-assistant/brands expects for a custom integration:
icon.png (256x256), icon@2x.png (512x512), plus dark_* variants.

Usage: python branding/generate_icon.py
"""

from PIL import Image, ImageDraw, ImageFilter

SS = 4  # supersampling factor
SIZE = 512 * SS  # master canvas (512 -> icon@2x, downscaled again for icon)
CX = CY = SIZE // 2

# Palette sampled from the BRC1H photo (dark face, indigo-violet halo).
FACE = (18, 19, 24, 255)
FACE_EDGE = (48, 50, 60, 255)
BEZEL = (10, 10, 13, 255)
HALO_INNER = (129, 90, 255)
HALO_OUTER = (64, 105, 255)
SCREEN_BG = (52, 38, 120, 255)
SCREEN_GLOW = (120, 96, 235)
TEXT = (196, 186, 255, 255)
BUTTON = (110, 112, 125, 255)


def radial_ring(draw: ImageDraw.ImageDraw, radius: int, width: int, color, alpha: int) -> None:
    draw.ellipse(
        [CX - radius, CY - radius, CX + radius, CY + radius],
        outline=color + (alpha,),
        width=width,
    )


def build(dark_background: bool) -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    r_outer = int(SIZE * 0.47)

    # Outer bezel and face
    draw.ellipse(
        [CX - r_outer, CY - r_outer, CX + r_outer, CY + r_outer],
        fill=BEZEL,
        outline=FACE_EDGE,
        width=6 * SS,
    )
    r_face = int(r_outer * 0.965)
    draw.ellipse(
        [CX - r_face, CY - r_face, CX + r_face, CY + r_face], fill=FACE
    )

    # Halo ring (the glowing light ring of the BRC1H), gradient by layering
    halo = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    hdraw = ImageDraw.Draw(halo)
    r_halo = int(r_outer * 0.86)
    steps = 26
    for i in range(steps):
        t = i / (steps - 1)
        color = tuple(
            int(HALO_INNER[c] * (1 - t) + HALO_OUTER[c] * t) for c in range(3)
        )
        alpha = int(210 * (1 - abs(t - 0.5) * 1.1))
        radial_ring(hdraw, r_halo - i * SS, 3 * SS, color, max(alpha, 40))
    halo = halo.filter(ImageFilter.GaussianBlur(7 * SS))
    img.alpha_composite(halo)

    # Crisp core of the ring on top of the glow
    ring = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    rdraw = ImageDraw.Draw(ring)
    radial_ring(rdraw, r_halo - 13 * SS, 5 * SS, HALO_INNER, 235)
    ring = ring.filter(ImageFilter.GaussianBlur(SS))
    img.alpha_composite(ring)

    # Display: rounded-rect screen, slightly above center (like the device)
    sw, sh = int(SIZE * 0.34), int(SIZE * 0.26)
    sx, sy = CX - sw // 2, CY - int(sh * 0.62)
    screen_glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(screen_glow)
    gdraw.rounded_rectangle(
        [sx - 8 * SS, sy - 8 * SS, sx + sw + 8 * SS, sy + sh + 8 * SS],
        radius=12 * SS,
        fill=SCREEN_GLOW + (110,),
    )
    screen_glow = screen_glow.filter(ImageFilter.GaussianBlur(10 * SS))
    img.alpha_composite(screen_glow)
    draw.rounded_rectangle(
        [sx, sy, sx + sw, sy + sh], radius=10 * SS, fill=SCREEN_BG
    )

    # Minimal "temperature" glyph on screen: a bold degree reading suggestion —
    # abstract: one wide bar + degree dot keeps it readable at 48px.
    bar_w, bar_h = int(sw * 0.42), int(sh * 0.16)
    bx, by = sx + int(sw * 0.16), sy + int(sh * 0.42)
    draw.rounded_rectangle(
        [bx, by, bx + bar_w, by + bar_h], radius=bar_h // 2, fill=TEXT
    )
    dot_r = int(sh * 0.09)
    dcx, dcy = sx + int(sw * 0.76), sy + int(sh * 0.38)
    draw.ellipse(
        [dcx - dot_r, dcy - dot_r, dcx + dot_r, dcy + dot_r],
        outline=TEXT,
        width=4 * SS,
    )

    # The three touch controls: − ○ +
    byc = CY + int(r_face * 0.55)
    spacing = int(SIZE * 0.11)
    lw = 5 * SS
    seg = int(SIZE * 0.028)
    # minus
    draw.line([CX - spacing - seg, byc, CX - spacing + seg, byc], fill=BUTTON, width=lw)
    # circle
    draw.ellipse(
        [CX - seg, byc - seg, CX + seg, byc + seg], outline=BUTTON, width=lw
    )
    # plus
    draw.line([CX + spacing - seg, byc, CX + spacing + seg, byc], fill=BUTTON, width=lw)
    draw.line([CX + spacing, byc - seg, CX + spacing, byc + seg], fill=BUTTON, width=lw)

    if dark_background:
        # dark variant: lift the face edge so it reads on near-black UIs
        edge = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        edraw = ImageDraw.Draw(edge)
        radial_ring(edraw, r_outer, 4 * SS, (95, 98, 112), 255)
        img.alpha_composite(edge)

    return img


def export(img: Image.Image, prefix: str) -> None:
    for name, size in ((f"{prefix}icon@2x.png", 512), (f"{prefix}icon.png", 256)):
        img.resize((size, size), Image.LANCZOS).save(f"branding/{name}")
        print(f"branding/{name}")


if __name__ == "__main__":
    export(build(dark_background=False), "")
    export(build(dark_background=True), "dark_")
