from app.routes.reference_data import blp as reference_data_blp
from app.routes.invoices import blp as invoices_blp
from app.routes.invoice_documents import blp as invoice_documents_blp
from app.routes.invoice_comments import blp as invoice_comments_blp
from app.routes.ap_workflow import blp as ap_workflow_blp
from app.routes.document_extractions import blp as document_extractions_blp
from app.routes.ai_chat import blp as ai_chat_blp

blueprints = [
    reference_data_blp,
    invoices_blp,
    invoice_documents_blp,
    invoice_comments_blp,
    ap_workflow_blp,
    document_extractions_blp,
    ai_chat_blp,
]
