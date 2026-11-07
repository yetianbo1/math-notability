"""Create editable NTB files through the checked official-template writer.

Each PDF page gets its own native page event. The source PDF stays unchanged;
the destination is replaced atomically only after archive validation.
"""


class PdfImportCancelled(Exception):
    pass


class PdfImportError(ValueError):
    pass


def pdf_to_ntb(source, target, progress=lambda percent: None,
               cancelled=lambda: False):
    from import_export.ne_official_pdf import pdf_to_official_template_ntb
    return pdf_to_official_template_ntb(source, target, progress, cancelled)
