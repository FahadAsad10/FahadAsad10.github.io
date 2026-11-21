"""Six fictional documents for sample mode, recorded as the fields printed on them.

`samples/make_samples.py` renders the PDFs and photos in `samples/` from exactly these records, so the
documents and the pre-extracted results always agree. Two documents contain deliberate problems
(an arithmetic error and a duplicate) to show what the checks catch. All companies are made up.
"""

from .schema import Invoice, LineItem

SAMPLES: list[tuple[str, Invoice]] = [
    (
        "northwind-INV-20417.pdf",
        Invoice(
            document_type="invoice",
            vendor_name="Northwind IT Supplies Ltd",
            vendor_tax_id="GB 284 6619 03",
            vendor_address="14 Harbour Street, Bristol BS1 4QA, United Kingdom",
            customer_name="Brightside Studio Ltd",
            invoice_number="INV-20417",
            invoice_date="2026-10-06",
            due_date="2026-11-05",
            currency="GBP",
            line_items=[
                LineItem(
                    description='Dell 27" USB-C monitor P2725HE',
                    quantity=3,
                    unit_price=289.00,
                    vat_rate=20,
                    amount=867.00,
                ),
                LineItem(
                    description="Logitech MX Keys S keyboard", quantity=3, unit_price=94.50, vat_rate=20, amount=283.50
                ),
                LineItem(description="Thunderbolt 4 dock", quantity=2, unit_price=219.00, vat_rate=20, amount=438.00),
                LineItem(
                    description="On-site setup (hours)", quantity=2.5, unit_price=65.00, vat_rate=20, amount=162.50
                ),
            ],
            subtotal=1751.00,
            vat_total=350.20,
            total=2101.20,
            payment_terms="Net 30, bank transfer",
        ),
    ),
    (
        "druckerei-weber-RE-2026-0893.pdf",
        Invoice(
            document_type="invoice",
            vendor_name="Druckerei Weber GmbH",
            vendor_tax_id="DE 318 447 215",
            vendor_address="Lindenstraße 22, 50674 Köln, Deutschland",
            customer_name="Brightside Studio Ltd",
            invoice_number="RE-2026-0893",
            invoice_date="2026-10-14",
            due_date="2026-10-28",
            currency="EUR",
            line_items=[
                LineItem(
                    description="Visitenkarten, 400 g, 500 Stk.",
                    quantity=4,
                    unit_price=48.90,
                    vat_rate=19,
                    amount=195.60,
                ),
                LineItem(
                    description="Roll-up Banner 85 × 200 cm", quantity=2, unit_price=129.00, vat_rate=19, amount=258.00
                ),
                LineItem(
                    description="Broschüre A5, 24 Seiten (1.000 Stk.)",
                    quantity=1,
                    unit_price=1185.00,
                    vat_rate=7,
                    amount=1185.00,
                ),
                LineItem(description="Versand DHL Express", quantity=1, unit_price=24.90, vat_rate=19, amount=24.90),
            ],
            subtotal=1663.50,
            vat_total=173.87,
            total=1837.37,
            payment_terms="Zahlbar innerhalb 14 Tagen ohne Abzug",
        ),
    ),
    (
        "stratus-cloud-SC-118204.pdf",
        Invoice(
            document_type="invoice",
            vendor_name="Stratus Cloud Inc.",
            vendor_tax_id="EU372004851",
            vendor_address="500 Mission Street, San Francisco, CA 94105, USA",
            customer_name="Brightside Studio Ltd",
            invoice_number="SC-118204",
            invoice_date="2026-11-01",
            due_date="2026-11-15",
            currency="USD",
            line_items=[
                LineItem(
                    description="Compute: 4 × standard-8 instances (Oct)",
                    quantity=2976,
                    unit_price=0.084,
                    vat_rate=0,
                    amount=249.98,
                ),
                LineItem(
                    description="Managed Postgres, 200 GB", quantity=1, unit_price=118.00, vat_rate=0, amount=118.00
                ),
                LineItem(
                    description="Object storage, 1.4 TB", quantity=1400, unit_price=0.021, vat_rate=0, amount=29.40
                ),
                LineItem(description="Business support plan", quantity=1, unit_price=100.00, vat_rate=0, amount=100.00),
            ],
            subtotal=497.38,
            vat_total=0.00,
            total=497.38,
            payment_terms="Charged to card on file",
        ),
    ),
    (
        "caffe-roma-receipt.jpg",
        Invoice(
            document_type="receipt",
            vendor_name="Caffè Roma di L. Bianchi",
            vendor_tax_id="IT 04417230585",
            vendor_address="Via dei Coronari 41, 00186 Roma",
            invoice_number="0127-0042",
            invoice_date="2026-10-21",
            currency="EUR",
            line_items=[
                LineItem(description="Cappuccino", quantity=3, vat_rate=10, amount=4.91),
                LineItem(description="Cornetto integrale", quantity=3, vat_rate=10, amount=4.09),
                LineItem(description="Tramezzino", quantity=2, vat_rate=10, amount=7.64),
                LineItem(description="Acqua frizzante 0,5", quantity=1, vat_rate=10, amount=1.36),
            ],
            subtotal=18.00,
            vat_total=1.80,
            total=19.80,
            payment_terms="Paid by card",
            notes="Prices on the receipt include 10% IVA; a handwritten tip of 2,00 is not included in the total.",
        ),
    ),
    (
        "brightclean-BC-3391.pdf",
        Invoice(
            document_type="invoice",
            vendor_name="BrightClean Facility Services",
            vendor_tax_id="GB 901 2277 45",
            vendor_address="Unit 7, Riverside Park, Leeds LS10 1RJ",
            customer_name="Brightside Studio Ltd",
            invoice_number="BC-3391",
            invoice_date="2026-10-31",
            due_date="2026-11-14",
            currency="GBP",
            line_items=[
                LineItem(
                    description="Office cleaning, October (20 visits)",
                    quantity=20,
                    unit_price=52.00,
                    vat_rate=20,
                    amount=1040.00,
                ),
                LineItem(
                    description="Window cleaning, exterior", quantity=1, unit_price=180.00, vat_rate=20, amount=180.00
                ),
                LineItem(description="Washroom supplies", quantity=1, unit_price=70.00, vat_rate=20, amount=70.00),
            ],
            subtotal=1240.00,
            vat_total=248.00,
            total=1488.00,
            payment_terms="Net 14",
        ),
    ),
    (
        "scan-northwind-copy.png",
        Invoice(
            document_type="invoice",
            vendor_name="Northwind IT Supplies Ltd",
            vendor_tax_id="GB 284 6619 03",
            vendor_address="14 Harbour Street, Bristol BS1 4QA, United Kingdom",
            customer_name="Brightside Studio Ltd",
            invoice_number="INV-20417",
            invoice_date="2026-10-06",
            due_date="2026-11-05",
            currency="GBP",
            line_items=[
                LineItem(
                    description='Dell 27" USB-C monitor P2725HE',
                    quantity=3,
                    unit_price=289.00,
                    vat_rate=20,
                    amount=867.00,
                ),
                LineItem(
                    description="Logitech MX Keys S keyboard", quantity=3, unit_price=94.50, vat_rate=20, amount=283.50
                ),
                LineItem(description="Thunderbolt 4 dock", quantity=2, unit_price=219.00, vat_rate=20, amount=438.00),
                LineItem(
                    description="On-site setup (hours)", quantity=2.5, unit_price=65.00, vat_rate=20, amount=162.50
                ),
            ],
            subtotal=1751.00,
            vat_total=350.20,
            total=2101.20,
            payment_terms="Net 30, bank transfer",
            notes=None,
        ),
    ),
]
