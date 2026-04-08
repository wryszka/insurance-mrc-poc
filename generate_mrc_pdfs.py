"""
Generate 5 Lloyd's Market Reform Contract (MRC v3) PDF files.
Pure Python PDF generation - no external libraries required.
"""

import struct
import zlib
import os

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

# ── Minimal PDF Writer ──────────────────────────────────────────────────────

class SimplePDF:
    """Minimal PDF 1.4 writer supporting Helvetica text with headings and body."""

    def __init__(self):
        self.objects = []
        self.pages = []
        self.current_page_streams = []
        self._y = 750  # current y position (top of page)

    def _add_obj(self, content):
        self.objects.append(content)
        return len(self.objects)  # 1-based obj number

    def _escape(self, text):
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    def add_heading(self, text, size=16):
        if self._y < 60:
            self._new_page()
        self._y -= size + 8
        self.current_page_streams.append(
            f"BT /F1 {size} Tf 50 {self._y:.0f} Td ({self._escape(text)}) Tj ET"
        )
        self._y -= 4

    def add_text(self, text, size=10):
        for line in text.split("\n"):
            if self._y < 50:
                self._new_page()
            self._y -= size + 4
            self.current_page_streams.append(
                f"BT /F2 {size} Tf 60 {self._y:.0f} Td ({self._escape(line)}) Tj ET"
            )

    def add_separator(self):
        self._y -= 6
        self.current_page_streams.append(
            f"0.7 G 50 {self._y:.0f} m 550 {self._y:.0f} l S 0 G"
        )
        self._y -= 6

    def _new_page(self):
        stream_body = "\n".join(self.current_page_streams)
        self.pages.append(stream_body)
        self.current_page_streams = []
        self._y = 750

    def save(self, path):
        # Flush last page
        if self.current_page_streams:
            self._new_page()

        objs = []  # list of (objnum, bytes)
        objnum = [0]

        def new_obj(data: str) -> int:
            objnum[0] += 1
            objs.append((objnum[0], data.encode("latin-1")))
            return objnum[0]

        # 1 - Catalog
        catalog_id = new_obj("")  # placeholder
        # 2 - Pages
        pages_id = new_obj("")
        # 3 - Font Helvetica-Bold
        font1_id = new_obj(
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"
        )
        # 4 - Font Helvetica
        font2_id = new_obj(
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
        )

        page_ids = []
        for page_stream in self.pages:
            stream_bytes = page_stream.encode("latin-1")
            stream_id = new_obj(
                f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("latin-1").decode("latin-1")
                + page_stream
                + "\nendstream"
            )
            page_id = new_obj(
                f"<< /Type /Page /Parent {pages_id} 0 R "
                f"/MediaBox [0 0 612 792] "
                f"/Contents {stream_id} 0 R "
                f"/Resources << /Font << /F1 {font1_id} 0 R /F2 {font2_id} 0 R >> >> >>"
            )
            page_ids.append(page_id)

        # Update catalog
        objs[catalog_id - 1] = (
            catalog_id,
            f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1"),
        )
        # Update pages
        kids = " ".join(f"{pid} 0 R" for pid in page_ids)
        objs[pages_id - 1] = (
            pages_id,
            f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode(
                "latin-1"
            ),
        )

        # Write PDF
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
            offsets = {}
            for oid, data in objs:
                offsets[oid] = f.tell()
                f.write(f"{oid} 0 obj\n".encode())
                f.write(data)
                f.write(b"\nendobj\n")
            xref_offset = f.tell()
            f.write(b"xref\n")
            f.write(f"0 {len(objs) + 1}\n".encode())
            f.write(b"0000000000 65535 f \n")
            for oid in range(1, len(objs) + 1):
                f.write(f"{offsets[oid]:010d} 00000 n \n".encode())
            f.write(b"trailer\n")
            f.write(
                f"<< /Size {len(objs) + 1} /Root {catalog_id} 0 R >>\n".encode()
            )
            f.write(b"startxref\n")
            f.write(f"{xref_offset}\n".encode())
            f.write(b"%%EOF\n")


# ── MRC Policy Data ─────────────────────────────────────────────────────────

POLICIES = [
    {
        "filename": "mrc_policy_001.pdf",
        "umr": "B0999ABC123456",
        "policy_number": "MRC-2025-LL-001",
        "class_of_business": "Property Damage & Business Interruption",
        "inception": "01 January 2025",
        "expiry": "31 December 2025",
        "insured": {
            "name": "Meridian Global Industries PLC",
            "address": "45 Bishopsgate, London EC2N 3DA, United Kingdom",
            "industry": "Manufacturing & Distribution",
            "jurisdiction": "England & Wales",
        },
        "broker": {
            "name": "Aon UK Limited",
            "code": "0780",
            "email": "placements.london@aon.com",
        },
        "insurers": [
            {"name": "Syndicate 2001 - Brit Syndicates Ltd", "line": "25.00%", "stamp": "XB0001"},
            {"name": "Syndicate 0623 - Beazley Furlonge Ltd", "line": "15.00%", "stamp": "AF0623"},
            {"name": "Syndicate 1084 - Chaucer Syndicates Ltd", "line": "10.00%", "stamp": "AM1084"},
        ],
        "limits": [
            {"amount": "GBP 50,000,000", "basis": "Any one occurrence", "type": "Property Damage"},
            {"amount": "GBP 25,000,000", "basis": "Any one occurrence", "type": "Business Interruption"},
            {"amount": "GBP 75,000,000", "basis": "In the aggregate", "type": "Combined Maximum"},
        ],
        "deductible": "GBP 250,000 each and every loss",
        "premium": "GBP 1,875,000 annual, payable in quarterly installments",
        "clauses": [
            "LMA5218 - Sanction Limitation and Exclusion Clause",
            "LMA5256 - Cyber Loss Exclusion (Property Treaty Reinsurance)",
            "LSW1001 - Several Liability Notice",
            "NMA2914 - Communicable Disease Exclusion",
        ],
        "exclusions": [
            "War and terrorism (as per NMA2918)",
            "Nuclear, chemical, biological, radiological contamination",
            "Cyber-induced physical damage (except sub-limit GBP 5M)",
            "Gradual pollution and environmental impairment",
            "Government confiscation or nationalisation",
        ],
    },
    {
        "filename": "mrc_policy_002.pdf",
        "umr": "B0888DEF789012",
        "policy_number": "MRC-2025-LL-002",
        "class_of_business": "Professional Indemnity",
        "inception": "01 March 2025",
        "expiry": "28 February 2026",
        "insured": {
            "name": "Thornfield Consulting Group Ltd",
            "address": "12 Fenchurch Street, London EC3M 3BY, United Kingdom",
            "industry": "Management Consultancy",
            "jurisdiction": "England & Wales",
        },
        "broker": {
            "name": "Marsh Ltd",
            "code": "0500",
            "email": "london.pi@marsh.com",
        },
        "insurers": [
            {"name": "Syndicate 0510 - Tokio Marine Kiln", "line": "30.00%", "stamp": "TK0510"},
            {"name": "Syndicate 2121 - Argenta Syndicate Management", "line": "20.00%", "stamp": "AR2121"},
        ],
        "limits": [
            {"amount": "GBP 10,000,000", "basis": "Any one claim", "type": "Professional Indemnity"},
            {"amount": "GBP 20,000,000", "basis": "In the aggregate", "type": "Aggregate Limit"},
        ],
        "deductible": "GBP 100,000 each and every claim, inclusive of defence costs",
        "premium": "GBP 425,000 annual, minimum and deposit",
        "clauses": [
            "LMA5218 - Sanction Limitation and Exclusion Clause",
            "LMA5401 - Claims Made Notification Clause",
            "LSW1001 - Several Liability Notice",
            "IUA09-045 - Dishonesty Exclusion",
        ],
        "exclusions": [
            "Bodily injury and property damage",
            "Directors & Officers liability",
            "Fraud, dishonesty, or criminal acts of the insured",
            "Prior and pending litigation as at inception",
            "US/Canada jurisdiction (unless agreed)",
        ],
    },
    {
        "filename": "mrc_policy_003.pdf",
        "umr": "B0777GHI345678",
        "policy_number": "MRC-2025-LL-003",
        "class_of_business": "Marine Cargo",
        "inception": "15 April 2025",
        "expiry": "14 April 2026",
        "insured": {
            "name": "Atlantic Shipping & Logistics SA",
            "address": "Rue du Rhone 14, 1204 Geneva, Switzerland",
            "industry": "Shipping & Freight Forwarding",
            "jurisdiction": "Switzerland",
        },
        "broker": {
            "name": "Willis Towers Watson",
            "code": "0425",
            "email": "marine.cargo@wtwco.com",
        },
        "insurers": [
            {"name": "Syndicate 0033 - Hiscox Syndicates Ltd", "line": "35.00%", "stamp": "HX0033"},
            {"name": "Syndicate 1880 - Renaissance Re Syndicate Management", "line": "15.00%", "stamp": "RR1880"},
        ],
        "limits": [
            {"amount": "USD 30,000,000", "basis": "Any one vessel/conveyance", "type": "Marine Cargo"},
            {"amount": "USD 60,000,000", "basis": "Any one event/catastrophe", "type": "Catastrophe Limit"},
            {"amount": "USD 5,000,000", "basis": "Any one sending", "type": "Storage Extension"},
        ],
        "deductible": "USD 50,000 each and every loss, except USD 25,000 for containerised cargo",
        "premium": "USD 780,000 adjustable, subject to minimum premium of USD 600,000",
        "clauses": [
            "Institute Cargo Clauses (A) CL382 01/01/2009",
            "Institute War Clauses (Cargo) CL385",
            "Institute Strikes Clauses (Cargo) CL386",
            "LMA5218 - Sanction Limitation and Exclusion Clause",
            "Concealed Damage Clause - 60 days",
        ],
        "exclusions": [
            "Delay, loss of market, or consequential loss",
            "Inherent vice or nature of the subject matter",
            "Insufficiency or unsuitability of packing",
            "Wilful misconduct of the assured",
            "Insolvency of vessel owners/charterers",
        ],
    },
    {
        "filename": "mrc_policy_004.pdf",
        "umr": "B0666JKL901234",
        "policy_number": "MRC-2025-LL-004",
        "class_of_business": "Cyber Liability",
        "inception": "01 June 2025",
        "expiry": "31 May 2026",
        "insured": {
            "name": "NovaTech Digital Solutions Inc",
            "address": "100 California Street, Suite 1200, San Francisco, CA 94111, USA",
            "industry": "Technology & SaaS",
            "jurisdiction": "State of Delaware, USA",
        },
        "broker": {
            "name": "Gallagher Re",
            "code": "1200",
            "email": "cyber.london@ajg.com",
        },
        "insurers": [
            {"name": "Syndicate 4444 - Canopius Managing Agents", "line": "20.00%", "stamp": "CA4444"},
            {"name": "Syndicate 0382 - Hardy Syndicates Ltd", "line": "15.00%", "stamp": "HD0382"},
            {"name": "Syndicate 1969 - Apollo Syndicate Management", "line": "15.00%", "stamp": "AP1969"},
        ],
        "limits": [
            {"amount": "USD 25,000,000", "basis": "Any one claim and in the aggregate", "type": "Cyber Liability Combined"},
            {"amount": "USD 5,000,000", "basis": "Sub-limit, any one event", "type": "Ransomware/Extortion"},
            {"amount": "USD 2,500,000", "basis": "Sub-limit, aggregate", "type": "Regulatory Defence & Penalties"},
            {"amount": "USD 1,000,000", "basis": "Sub-limit, any one event", "type": "Crisis Communication"},
        ],
        "deductible": "USD 500,000 each and every claim; 72-hour waiting period for business interruption",
        "premium": "USD 1,250,000 annual, flat rated",
        "clauses": [
            "LMA5218 - Sanction Limitation and Exclusion Clause",
            "LMA5410 - Cyber Event Definition Clause",
            "CY0001 - Full Cyber Coverage Section A (Data Breach Response)",
            "CY0002 - Full Cyber Coverage Section B (Business Interruption)",
            "LSW1001 - Several Liability Notice",
        ],
        "exclusions": [
            "Unencrypted portable device loss",
            "Infrastructure failure (power grid, telecoms) not caused by cyber event",
            "Acts of cyber war or state-sponsored attacks (as per LMA5567)",
            "Prior known events or circumstances",
            "Bodily injury or tangible property damage",
            "Patent or trade secret infringement",
        ],
    },
    {
        "filename": "mrc_policy_005.pdf",
        "umr": "B0555MNO567890",
        "policy_number": "MRC-2025-LL-005",
        "class_of_business": "Directors & Officers Liability",
        "inception": "01 July 2025",
        "expiry": "30 June 2026",
        "insured": {
            "name": "Halcyon Pharmaceuticals PLC",
            "address": "1 Cabot Square, Canary Wharf, London E14 4QJ, United Kingdom",
            "industry": "Pharmaceuticals & Life Sciences",
            "jurisdiction": "England & Wales",
        },
        "broker": {
            "name": "Lockton Companies LLP",
            "code": "0950",
            "email": "do.london@lockton.com",
        },
        "insurers": [
            {"name": "Syndicate 2003 - Catlin Underwriting Agencies", "line": "25.00%", "stamp": "CU2003"},
            {"name": "Syndicate 0457 - Munich Re Syndicate", "line": "20.00%", "stamp": "MR0457"},
            {"name": "Syndicate 1200 - Argo Managing Agency", "line": "5.00%", "stamp": "AG1200"},
        ],
        "limits": [
            {"amount": "GBP 50,000,000", "basis": "Any one claim and in the aggregate", "type": "D&O Side A (Non-indemnifiable Loss)"},
            {"amount": "GBP 50,000,000", "basis": "Any one claim and in the aggregate", "type": "D&O Side B (Corporate Reimbursement)"},
            {"amount": "GBP 25,000,000", "basis": "Sub-limit, aggregate", "type": "D&O Side C (Entity Securities)"},
            {"amount": "GBP 10,000,000", "basis": "Sub-limit, aggregate", "type": "Employment Practices Liability"},
        ],
        "deductible": "GBP 500,000 each and every claim (Side B & C only); Nil for Side A",
        "premium": "GBP 2,100,000 annual, minimum and deposit",
        "clauses": [
            "LMA5218 - Sanction Limitation and Exclusion Clause",
            "LMA5547 - D&O Claims Made Basis Clause",
            "LSW1001 - Several Liability Notice",
            "DO-001 - Insured vs Insured Exclusion (with carve-backs)",
            "DO-002 - Outside Directorship Extension",
        ],
        "exclusions": [
            "Bodily injury and property damage",
            "Professional services liability (separate PI policy required)",
            "Pension trustee liability",
            "Pollution and environmental liability",
            "Prior and pending litigation as at inception",
            "Deliberate fraud or dishonesty (established by final adjudication)",
        ],
    },
]


def generate_pdf(policy: dict) -> str:
    pdf = SimplePDF()

    # Title
    pdf.add_heading("LLOYD'S MARKET REFORM CONTRACT (MRC v3)", size=14)
    pdf.add_separator()

    # ── Risk Details ──
    pdf.add_heading("1. RISK DETAILS", size=13)
    pdf.add_text(f"Unique Market Reference (UMR): {policy['umr']}")
    pdf.add_text(f"Policy Number: {policy['policy_number']}")
    pdf.add_text(f"Class of Business: {policy['class_of_business']}")
    pdf.add_text(f"Period: {policy['inception']} to {policy['expiry']}")
    pdf.add_text(f"Type: Claims Made" if "Indemnity" in policy["class_of_business"] or "Officers" in policy["class_of_business"] or "Cyber" in policy["class_of_business"] else f"Type: Losses Occurring")
    pdf.add_separator()

    # ── Insured ──
    pdf.add_heading("2. INSURED", size=13)
    ins = policy["insured"]
    pdf.add_text(f"Name: {ins['name']}")
    pdf.add_text(f"Address: {ins['address']}")
    pdf.add_text(f"Industry: {ins['industry']}")
    pdf.add_text(f"Governing Law / Jurisdiction: {ins['jurisdiction']}")
    pdf.add_separator()

    # ── Broker ──
    pdf.add_heading("3. BROKER", size=13)
    brk = policy["broker"]
    pdf.add_text(f"Broker: {brk['name']}")
    pdf.add_text(f"Lloyd's Broker Code: {brk['code']}")
    pdf.add_text(f"Contact: {brk['email']}")
    pdf.add_separator()

    # ── Insurers / Security ──
    pdf.add_heading("4. INSURERS / SECURITY", size=13)
    for i, ins_r in enumerate(policy["insurers"], 1):
        pdf.add_text(f"{i}. {ins_r['name']}  |  Line: {ins_r['line']}  |  Stamp: {ins_r['stamp']}")
    signed_line = sum(float(x["line"].replace("%", "")) for x in policy["insurers"])
    pdf.add_text(f"Total Signed Line: {signed_line:.2f}%")
    pdf.add_separator()

    # ── Limits ──
    pdf.add_heading("5. LIMITS OF LIABILITY", size=13)
    for lim in policy["limits"]:
        pdf.add_text(f"- {lim['type']}: {lim['amount']} ({lim['basis']})")
    pdf.add_text(f"Deductible: {policy['deductible']}")
    pdf.add_separator()

    # ── Premium ──
    pdf.add_heading("6. PREMIUM", size=13)
    pdf.add_text(policy["premium"])
    pdf.add_separator()

    # ── Clauses ──
    pdf.add_heading("7. CLAUSES", size=13)
    for cl in policy["clauses"]:
        pdf.add_text(f"- {cl}")
    pdf.add_separator()

    # ── Exclusions ──
    pdf.add_heading("8. EXCLUSIONS", size=13)
    for ex in policy["exclusions"]:
        pdf.add_text(f"- {ex}")
    pdf.add_separator()

    # ── Signature Block ──
    pdf.add_heading("9. AGREEMENT AND SIGNING", size=13)
    pdf.add_text("This contract has been agreed in accordance with the Lloyd's Market")
    pdf.add_text("Reform Contract procedures. The terms and conditions set out herein")
    pdf.add_text("constitute the agreed basis of the insurance.")
    pdf.add_text("")
    pdf.add_text("Signed for and on behalf of the Leading Underwriter:")
    pdf.add_text(f"  {policy['insurers'][0]['name']}")
    pdf.add_text("")
    pdf.add_text("Date of Signing: _______________")

    path = os.path.join(OUTPUT_DIR, policy["filename"])
    pdf.save(path)
    return path


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for p in POLICIES:
        path = generate_pdf(p)
        size_kb = os.path.getsize(path) / 1024
        print(f"Generated: {path} ({size_kb:.1f} KB)")
    print(f"\nAll {len(POLICIES)} MRC PDFs generated successfully.")
