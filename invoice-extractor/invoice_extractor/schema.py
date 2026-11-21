"""The shape of one extracted document. Claude fills this in through structured outputs."""

from typing import Literal

from pydantic import BaseModel, Field

DocumentType = Literal["invoice", "receipt", "credit_note", "other"]


class LineItem(BaseModel):
    description: str = Field(description="Item or service as written on the document")
    quantity: float | None = Field(None, description="Number of units; null if not shown")
    unit_price: float | None = Field(None, description="Price per unit before tax; null if not shown")
    vat_rate: float | None = Field(None, description="Tax rate in percent, e.g. 20 for 20%; null if not shown")
    amount: float = Field(description="Line total before tax as printed (negative on a credit note)")


class Invoice(BaseModel):
    document_type: DocumentType = Field(description="invoice, receipt, credit_note or other")
    vendor_name: str = Field(description="The business that issued the document")
    vendor_tax_id: str | None = Field(None, description="Seller VAT / tax registration number exactly as printed")
    vendor_address: str | None = Field(None, description="Seller address on one line")
    customer_name: str | None = Field(None, description="Who the document is billed to, if shown")
    invoice_number: str | None = Field(None, description="Invoice, receipt or transaction number")
    invoice_date: str | None = Field(None, description="Issue date as YYYY-MM-DD")
    due_date: str | None = Field(None, description="Payment due date as YYYY-MM-DD; null if not shown")
    currency: str = Field(description="ISO 4217 code such as GBP, EUR, USD, inferred from symbols if needed")
    line_items: list[LineItem] = Field(description="Every line item, in order")
    subtotal: float | None = Field(None, description="Total before tax as printed")
    vat_total: float | None = Field(None, description="Total tax (VAT, GST or sales tax) as printed")
    total: float = Field(description="Amount payable as printed, including tax")
    payment_terms: str | None = Field(None, description="e.g. 'Net 30' or 'Paid by card'")
    notes: str | None = Field(
        None, description="Anything a bookkeeper should know: unreadable fields, handwriting, discounts, partial pages"
    )
