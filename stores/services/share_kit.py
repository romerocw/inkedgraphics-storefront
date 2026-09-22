"""Everything an organizer needs to point people at their store.

Built when a store opens and rebuilt whenever something printed on an asset changes, so the
flyer in someone's hand never shows last month's close date. The console calls `generate()`,
and the client portal will call the same function — neither owns the logic.

Assets land in storage under the store's own prefix. `fingerprint()` is what makes rebuilding
cheap: it hashes only the things that appear on an asset, so an unrelated edit to a store
rebuilds nothing.
"""

import hashlib
import logging
import os
from io import BytesIO
from textwrap import wrap

import qrcode
import qrcode.image.svg
import reportlab
from django.conf import settings
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

# Bitstream Vera, shipped inside reportlab. Using it keeps a real typeface out of the repo and
# off the server's font path, which macOS and Amazon Linux do not agree about.
_FONT_DIR = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
FONT_BOLD = os.path.join(_FONT_DIR, "VeraBd.ttf")
FONT_REGULAR = os.path.join(_FONT_DIR, "Vera.ttf")

logger = logging.getLogger(__name__)

SOCIAL_SIZE = 1080
ASSET_FIELDS = ("share_qr_png", "share_qr_svg", "share_flyer_pdf", "share_social_png")


def store_url(store):
    """The link that goes on every asset. There's no request under cron, so SITE_URL it is."""
    return f"{settings.SITE_URL}{reverse('store_detail', args=[store.slug])}"


def brand_color(store):
    return store.primary_color or store.client.primary_color or "#111827"


def close_date_text(store):
    """The close date in the store's own zone, as it's shown everywhere else."""
    if not store.closes_local:
        return ""
    return store.closes_local.strftime("%B %-d")


def inputs(store):
    """Only what actually appears on an asset — an unrelated edit must not force a rebuild."""
    return {
        "name": store.name,
        "client": store.client.name,
        "logo": store.client.logo.name or "",
        "closes_at": store.closes_at.isoformat() if store.closes_at else "",
        "time_zone": store.time_zone,
        "url": store_url(store),
        "color": brand_color(store),
        "arrival": store.arrival.label if store.arrival else "",
    }


def fingerprint(store):
    parts = "\x1f".join(f"{k}={v}" for k, v in sorted(inputs(store).items()))
    return hashlib.sha256(parts.encode()).hexdigest()


def share_text(store):
    """The blurb an organizer pastes into a group text or an email."""
    lines = [f"{store.name} is open!"]
    closes = close_date_text(store)
    lines.append(f"Order by {closes} at {store_url(store)}" if closes else f"Order at {store_url(store)}")
    if store.arrival:
        lines.append(f"Orders arrive {store.arrival.label}.")
    return "\n".join(lines)


def _logo_image(store):
    """The client's logo as RGBA, or None. A missing file must not sink the whole kit."""
    logo = store.client.logo
    if not logo:
        return None
    try:
        with logo.open("rb") as handle:
            return Image.open(BytesIO(handle.read())).convert("RGBA")
    except (OSError, ValueError):
        return None


def _qr(store):
    code = qrcode.QRCode(box_size=10, border=2)
    code.add_data(store_url(store))
    code.make(fit=True)
    return code


def qr_png(store):
    buffer = BytesIO()
    _qr(store).make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    return buffer.getvalue()


def qr_svg(store):
    buffer = BytesIO()
    _qr(store).make_image(image_factory=qrcode.image.svg.SvgPathImage).save(buffer)
    return buffer.getvalue()


LINE_SPACING = 1.18


def _fitted(text, font_path, size, max_width, max_height, draw):
    """Wrap text and shrink the font until the block fits the box in both directions.

    Height matters as much as width: a long store name that wraps to five lines will fit the
    column and still run off the bottom of the image.
    """
    while size > 28:
        font = ImageFont.truetype(font_path, size)
        # Rough characters-per-line from the average glyph width at this size.
        per_line = max(8, int(max_width / (draw.textlength("n", font=font) or 1)))
        lines = wrap(text, per_line) or [text]
        fits_width = all(draw.textlength(line, font=font) <= max_width for line in lines)
        if fits_width and len(lines) * size * LINE_SPACING <= max_height:
            return font, lines
        size -= 6

    font = ImageFont.truetype(font_path, 28)
    per_line = max(8, int(max_width / (draw.textlength("n", font=font) or 1)))
    lines = wrap(text, per_line) or [text]
    keep = max(1, int(max_height / (28 * LINE_SPACING)))
    return font, lines[:keep]


def social_png(store):
    """1080x1080 for Instagram and Facebook: logo, store name, close date."""
    canvas_image = Image.new("RGB", (SOCIAL_SIZE, SOCIAL_SIZE), brand_color(store))
    draw = ImageDraw.Draw(canvas_image)
    margin = 90
    width = SOCIAL_SIZE - margin * 2

    link = ImageFont.truetype(FONT_REGULAR, 40)
    sub = ImageFont.truetype(FONT_REGULAR, 54)
    closes = close_date_text(store)

    # Lay the fixed pieces out first; whatever is left over is the name's to fill.
    url_top = SOCIAL_SIZE - margin - 48
    closes_top = url_top - 90 if closes else url_top
    top = margin

    logo = _logo_image(store)
    if logo:
        logo.thumbnail((420, 260))
        canvas_image.paste(logo, ((SOCIAL_SIZE - logo.width) // 2, top), logo)
        top += logo.height + 50

    available = max(120, closes_top - 40 - top)
    font, lines = _fitted(store.name, FONT_BOLD, 108, width, available, draw)

    line_height = font.size * LINE_SPACING
    y = top + (available - len(lines) * line_height) / 2  # centred in the space it was given
    for line in lines:
        draw.text(((SOCIAL_SIZE - draw.textlength(line, font=font)) / 2, y), line, font=font, fill="white")
        y += line_height

    if closes:
        text = f"Orders close {closes}"
        draw.text(((SOCIAL_SIZE - draw.textlength(text, font=sub)) / 2, closes_top), text, font=sub, fill="white")

    url = store_url(store).replace("https://", "").replace("http://", "")
    draw.text(((SOCIAL_SIZE - draw.textlength(url, font=link)) / 2, url_top), url, font=link, fill="white")

    buffer = BytesIO()
    canvas_image.save(buffer, format="PNG")
    return buffer.getvalue()


def flyer_pdf(store):
    """US Letter, for the noticeboard: name, close date, arrival, and a QR code to scan."""
    page_width, page_height = letter
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setTitle(f"{store.name} — how to order")

    band = 150
    pdf.setFillColor(HexColor(brand_color(store)))
    pdf.rect(0, page_height - band, page_width, band, stroke=0, fill=1)

    pdf.setFillColor(white)
    # Shrink the name to fit rather than cutting it off — a clipped school name looks broken.
    size = 30
    while size > 14 and pdf.stringWidth(store.name, "Helvetica-Bold", size) > page_width - 100:
        size -= 2
    pdf.setFont("Helvetica-Bold", size)
    pdf.drawCentredString(page_width / 2, page_height - band / 2 - 10, store.name)
    pdf.setFont("Helvetica", 15)
    pdf.drawCentredString(page_width / 2, page_height - band / 2 - 38, store.client.name)

    pdf.setFillColor(HexColor("#111827"))
    y = page_height - band - 60
    logo = _logo_image(store)
    if logo:
        logo.thumbnail((200, 100))
        pdf.drawImage(
            ImageReader(logo), (page_width - logo.width) / 2, y - logo.height,
            width=logo.width, height=logo.height, mask="auto",
        )
        y -= logo.height + 40

    closes = close_date_text(store)
    if closes:
        pdf.setFont("Helvetica-Bold", 21)
        pdf.drawCentredString(page_width / 2, y, f"Orders close {closes}")
        y -= 32
    if store.arrival:
        pdf.setFont("Helvetica", 16)
        pdf.drawCentredString(page_width / 2, y, f"Arrives {store.arrival.label}")
        y -= 40

    qr_size = 250
    qr_image = Image.open(BytesIO(qr_png(store)))
    pdf.drawImage(
        ImageReader(qr_image), (page_width - qr_size) / 2, y - qr_size,
        width=qr_size, height=qr_size,
    )
    y -= qr_size + 40

    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawCentredString(page_width / 2, y, "Scan the code to order, or go to")
    pdf.setFont("Helvetica", 14)
    pdf.drawCentredString(page_width / 2, y - 22, store_url(store))

    pdf.setFont("Helvetica", 9)
    pdf.setFillColor(HexColor("#6b7280"))
    pdf.drawCentredString(page_width / 2, 40, "Powered by Inked Graphics")

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def refresh_open_stores():
    """Rebuild the kit of any open store whose printed details changed. Returns (built, failed).

    Open stores only. A closed store's promised arrival is recomputed from today (the promise
    is never allowed to fall into the past), so its fingerprint would drift every single day
    and rebuild a kit nobody is sharing any more.

    One store's bad logo or full disk must not stop the others, and above all must not take
    down the status job this runs beside — the CloudWatch alarm watches that.
    """
    from stores.models import Store

    built = failed = 0
    for store in Store.objects.filter(status=Store.Status.OPEN).select_related("client"):
        try:
            built += bool(generate(store))
        except Exception:
            logger.exception("share kit failed for store %s (%s)", store.pk, store.slug)
            failed += 1
    return built, failed


def generate(store, force=False):
    """Build the kit if anything on it changed. Returns True if files were written.

    Idempotent: the fingerprint covers exactly what's printed, so calling this on every
    lifecycle tick costs one hash unless something really moved.
    """
    current = fingerprint(store)
    if not force and store.share_kit_fingerprint == current and store.share_kit_generated_at:
        return False

    assets = {
        "share_qr_png": ("qr.png", qr_png(store)),
        "share_qr_svg": ("qr.svg", qr_svg(store)),
        "share_social_png": ("social.png", social_png(store)),
        "share_flyer_pdf": ("flyer.pdf", flyer_pdf(store)),
    }
    for field_name, (filename, data) in assets.items():
        field = getattr(store, field_name)
        # Storage never overwrites, so the old file is dropped rather than left orphaned.
        if field:
            field.delete(save=False)
        field.save(filename, ContentFile(data), save=False)

    store.share_kit_fingerprint = current
    store.share_kit_generated_at = timezone.now()
    store.save(update_fields=[*ASSET_FIELDS, "share_kit_fingerprint", "share_kit_generated_at", "updated_at"])
    return True
