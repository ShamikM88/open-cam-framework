import docx

def export_to_docx(markdown_text, output_path):
    doc = docx.Document()
    for line in markdown_text.split("\n"):
        if line.startswith("# "):
            doc.add_heading(line.replace("# ", ""), level=1)
        elif line.startswith("## "):
            doc.add_heading(line.replace("## ", ""), level=2)
        elif line.startswith("- "):
            doc.add_paragraph(line.replace("- ", ""), style='List Bullet')
        else:
            if line.strip():
                doc.add_paragraph(line)
    doc.save(output_path)
