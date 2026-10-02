"""PDF receipts for paid bookings (ReportLab), with a QR code of the booking reference."""

import io
from datetime import datetime, tzinfo
from decimal import Decimal
from typing import Protocol

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_PAYMENT_METHODS = {"cash": "Cash", "upi": "UPI", "bank_transfer": "Bank transfer", "card": "Card"}
_BRAND = colors.HexColor("#15803d")


class _Turf(Protocol):
    name: str
    address: str
    location: str


class _Customer(Protocol):
    first_name: str
    last_name: str
    email: str
    phone: str


class _Booking(Protocol):
    reference: str
    start_at: datetime
    end_at: datetime
    status: str
    total_amount: Decimal
    paid_amount: Decimal | None
    paid_at: datetime | None
    payment_method: str | None
    payment_reference: str | None
    payment_status: str


def _qr(value: str, size: float = 32 * mm) -> Drawing:
    widget = QrCodeWidget(value)
    x0, y0, x1, y1 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    drawing.add(widget)
    return drawing


def build_receipt_pdf(booking: _Booking, turf: _Turf, customer: _Customer, tz: tzinfo) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Receipt {booking.reference}",
    )
    styles = getSampleStyleSheet()
    start = booking.start_at.astimezone(tz)
    end = booking.end_at.astimezone(tz)
    hours = (booking.end_at - booking.start_at).total_seconds() / 3600
    paid_at = booking.paid_at.astimezone(tz).strftime("%d %b %Y, %H:%M") if booking.paid_at else "-"

    header = Table(
        [
            [
                Paragraph("<font size=20 color='#15803d'><b>TurfSlot</b></font><br/>Booking receipt", styles["Normal"]),
                _qr(booking.reference),
            ]
        ],
        colWidths=[130 * mm, 40 * mm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))

    def section(rows: list[list[str]]) -> Table:
        table = Table(rows, colWidths=[55 * mm, 115 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#374151")),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        return table

    heading = styles["Heading3"].clone("section", textColor=_BRAND)
    story = [
        header,
        Spacer(1, 8 * mm),
        Paragraph("Booking", heading),
        section(
            [
                ["Reference", booking.reference],
                ["Turf", turf.name],
                ["Address", f"{turf.address}, {turf.location}"],
                ["Date", start.strftime("%A, %d %B %Y")],
                ["Time", f"{start:%H:%M} - {end:%H:%M} ({hours:g} h)"],
                ["Status", booking.status.replace("_", " ").title()],
            ]
        ),
        Spacer(1, 6 * mm),
        Paragraph("Customer", heading),
        section(
            [
                ["Name", f"{customer.first_name} {customer.last_name}"],
                ["Email", customer.email],
                ["Phone", customer.phone],
            ]
        ),
        Spacer(1, 6 * mm),
        Paragraph("Payment", heading),
        section(
            [
                ["Amount due", f"Rs {booking.total_amount:,.2f}"],
                ["Amount paid", f"Rs {(booking.paid_amount or Decimal('0')):,.2f}"],
                ["Method", _PAYMENT_METHODS.get(booking.payment_method or "", "-")],
                ["Transaction ref.", booking.payment_reference or "-"],
                ["Paid on", paid_at],
                ["Payment status", booking.payment_status.replace("_", " ").title()],
            ]
        ),
        Spacer(1, 10 * mm),
        Paragraph(
            "Show this receipt or the QR code at the turf. Payment was confirmed manually by the turf owner.",
            styles["Italic"],
        ),
    ]
    doc.build(story)
    return buffer.getvalue()
