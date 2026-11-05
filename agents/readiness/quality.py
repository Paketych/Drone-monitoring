from PIL import Image, ImageFilter, ImageOps, ImageStat

from agents.readiness.config import stages_config

LAPLACIAN = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)


def quality_issues(image: Image.Image) -> list[str]:
    """Technical defects that make a photograph useless: blur and bad exposure.

    Whether the house is in the frame is not checked: that needs a model.
    """
    limits = stages_config().quality
    # A fixed working size makes the sharpness measure comparable between cameras.
    gray = ImageOps.contain(image, (limits.working_size, limits.working_size)).convert("L")
    issues = []
    if ImageStat.Stat(gray.filter(LAPLACIAN)).var[0] < limits.blur_min_laplacian_var:
        issues.append("BLURRY")
    histogram = gray.histogram()
    total = sum(histogram)
    if sum(histogram[250:]) / total > limits.max_clipped_share:
        issues.append("OVEREXPOSED")
    if sum(histogram[:6]) / total > limits.max_clipped_share:
        issues.append("UNDEREXPOSED")
    return issues
