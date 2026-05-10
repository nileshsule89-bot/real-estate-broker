from datetime import UTC, datetime
from pathlib import Path

from app.models import Property, User


AGREEMENTS_DIR = Path("generated_agreements")


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_simple_pdf(lines: list[str]) -> bytes:
    content_lines = ["BT", "/F1 12 Tf", "72 770 Td", "14 TL"]
    for line in lines:
        content_lines.append(f"({_pdf_escape(line)}) Tj")
        content_lines.append("T*")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream")

    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{idx} 0 obj\n".encode("latin-1"))
        output.extend(obj)
        output.extend(b"\nendobj\n")

    xref_start = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_start}\n%%EOF"
        ).encode("latin-1")
    )
    return bytes(output)


def generate_sample_agreement_pdf(
    user: User,
    agreement_type: str,
    shortlisted_properties: list[Property],
) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    slug = agreement_type.replace("/", "_").replace(" ", "_")
    output_dir = AGREEMENTS_DIR / user.user_id
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"{slug}_{timestamp}.pdf"

    properties = shortlisted_properties[:3]
    property_lines = [
        f"- {prop.title}, {prop.location}, INR {int(prop.price)}"
        for prop in properties
    ] or ["- Property details to be finalized with the client"]

    lines = [
        f"Sample {agreement_type.title()} Agreement",
        "",
        f"Prepared for: {user.name}",
        f"Phone: {user.phone_number}",
        f"Prepared on: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "1. Parties",
        "This sample agreement is between the property owner and the client.",
        "",
        "2. Property",
        *property_lines,
        "",
        "3. Commercial Terms",
        "Price, deposit, taxes, and payment schedule to be finalized after negotiation.",
        "",
        "4. Legal Drafting",
        "KYC, title checks, registration charges, and stamp duty are to be verified before execution.",
        "",
        "5. Next Step",
        "Contact Nilesh Sule to review, customize, and register the final agreement.",
    ]

    file_path.write_bytes(_build_simple_pdf(lines))
    return str(file_path.resolve())
