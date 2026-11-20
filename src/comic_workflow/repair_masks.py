"""User-drawn repair boundaries, with blending confined to the selected pixels."""
import math
from PIL import Image, ImageDraw, ImageFilter, ImageChops
from . import engine as e


def validate_outline(outline, rect):
    if outline is None:
        return None
    e.need(isinstance(outline, list) and 3 <= len(outline) <= 128, '沿修复区域边缘点出三至一百二十八个点')
    for point in outline:
        e.need(isinstance(point, list) and len(point) == 2
               and all(type(v) in (int, float) and math.isfinite(v) for v in point)
               and rect[0] <= point[0] <= rect[2] and rect[1] <= point[1] <= rect[3],
               '修复描边需要位于所选红框内')
    return [list(point) for point in outline]


def edit_mask(size, rect, outline=None):
    w, h = size
    mask = Image.new('RGB', size, 'black')
    draw = ImageDraw.Draw(mask)
    if outline is not None:
        draw.polygon([(min(w-1,int(x*w)),min(h-1,int(y*h))) for x,y in outline], fill='white')
    else:
        draw.rectangle((int(rect[0]*w),int(rect[1]*h),min(w-1,int(rect[2]*w)),min(h-1,int(rect[3]*h))), fill='white')
    e.need(mask.getbbox() is not None, '修复区域不能为空')
    return mask


def blend_mask(mask):
    # Shrink and feather the compositing boundary, never extending edits outside
    # the exact outline. The denoising mask remains opaque and separate.
    hard = mask.convert('L')
    feathered = hard.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.GaussianBlur(2))
    return ImageChops.multiply(feathered, hard).convert('RGB')
