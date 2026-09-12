import openpyxl

def export_to_xlsx(company, output_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Financial Spreading"
    
    headers = ["Metric", "FY-2", "FY-1", "FY-Current"]
    ws.append(headers)
    ws.append(["Revenue", 0, 0, 0])
    ws.append(["EBITDA", 0, 0, 0])
    ws.append(["TNW", 0, 0, 0])
    ws.append(["DSCR", 0.0, 0.0, 0.0])
    
    wb.save(output_path)
