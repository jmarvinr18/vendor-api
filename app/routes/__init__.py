from app.routes.reference_data import blp as reference_data_blp
from app.routes.invoices import blp as invoices_blp
from app.routes.invoice_documents import blp as invoice_documents_blp
from app.routes.invoice_comments import blp as invoice_comments_blp
from app.routes.ap_workflow import blp as ap_workflow_blp

blueprints = [
    reference_data_blp,
    invoices_blp,
    invoice_documents_blp,
    invoice_comments_blp,
    ap_workflow_blp,
]
