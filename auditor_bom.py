#!/usr/bin/env python3
"""
Módulo de Auditoria, Cruzamento com Lista de Materiais do Projetista (BOM)
e Geração de Planilhas de Orçamento em Excel (.xlsx) com Segregação de Diâmetros
"""

import io
import re
import csv
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from rapidfuzz import fuzz

def parse_bom_file(file_bytes: bytes, filename: str) -> list[dict]:
    fname = filename.lower()
    df = None
    
    try:
        if fname.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(io.BytesIO(file_bytes))
        elif fname.endswith('.csv'):
            try:
                df = pd.read_csv(io.BytesIO(file_bytes), sep=';')
                if len(df.columns) <= 1:
                    df = pd.read_csv(io.BytesIO(file_bytes), sep=',')
            except Exception:
                df = pd.read_csv(io.BytesIO(file_bytes), sep=',')
        elif fname.endswith('.pdf'):
            import pdfplumber
            tables_data = []
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for page in pdf.pages:
                    for table in page.extract_tables():
                        for row in table:
                            clean_row = [str(c).strip() if c is not None else "" for c in row]
                            if any(clean_row):
                                tables_data.append(clean_row)
            if tables_data and len(tables_data) > 1:
                df = pd.DataFrame(tables_data[1:], columns=tables_data[0])
            elif tables_data:
                df = pd.DataFrame(tables_data)
    except Exception as e:
        print(f"Erro ao ler BOM: {e}")
        return []

    if df is None or df.empty:
        return []

    col_desc = None
    col_qtd = None
    col_un = None
    col_item = None

    for col in df.columns:
        c_upper = str(col).strip().upper()
        if any(k in c_upper for k in ["DESCRIÇÃO", "DESCRICAO", "MATERIAL", "DISCRIMINAÇÃO", "ESPECIFICAÇÃO", "NOME"]):
            col_desc = col
        elif any(k in c_upper for k in ["QTD", "QUANT", "QUANTIDADE", "METRAGEM", "QUANT."]):
            col_qtd = col
        elif any(k in c_upper for k in ["UN", "UND", "UNIDADE"]):
            col_un = col
        elif any(k in c_upper for k in ["ITEM", "POS", "POSIÇÃO"]):
            col_item = col

    if col_desc is None and len(df.columns) >= 2:
        col_desc = df.columns[1]
        col_qtd = df.columns[0] if len(df.columns) > 1 else None

    bom_items = []
    for idx, row in df.iterrows():
        desc = str(row[col_desc]).strip() if col_desc is not None and pd.notna(row[col_desc]) else ""
        if not desc or desc.lower() == 'nan' or len(desc) < 2:
            continue
            
        qtd_raw = row[col_qtd] if col_qtd is not None and pd.notna(row[col_qtd]) else 0
        try:
            qtd_str = re.sub(r"[^\d,\.]", "", str(qtd_raw)).replace(',', '.')
            qtd = float(qtd_str) if qtd_str else 1.0
        except Exception:
            qtd = 1.0

        un = str(row[col_un]).strip() if col_un is not None and pd.notna(row[col_un]) else ("m" if "m" in str(qtd_raw).lower() else "un")
        item_id = str(row[col_item]).strip() if col_item is not None and pd.notna(row[col_item]) else str(idx + 1)

        bom_items.append({
            "item": item_id,
            "descricao": desc,
            "quantidade": qtd,
            "unidade": un
        })

    return bom_items

def cross_check_cad_and_bom(cad_summary: dict, bom_items: list[dict]) -> list[dict]:
    cad_elements = []

    # 1. Tubulações segregadas por Diâmetro
    pipes = cad_summary.get("tubulacoes_por_diametro", {})
    if pipes:
        for p_name, metros in pipes.items():
            cad_elements.append({
                "origem": "CAD",
                "tipo": "TUBULACAO",
                "descricao": f"Tubulação: {p_name}",
                "quantidade": metros,
                "unidade": "m"
            })
    else:
        for layer, metros in cad_summary.get("metragens_lineares_metros", {}).items():
            cad_elements.append({
                "origem": "CAD",
                "tipo": "TUBULACAO",
                "descricao": f"Tubulação ({layer})",
                "quantidade": metros,
                "unidade": "m"
            })

    # 2. Conexões e Acoplamentos
    for key, qtd in cad_summary.get("conexoes_e_acoplamentos", {}).items():
        desc_clean = re.sub(r"^\[.*?\]\s*", "", key)
        cad_elements.append({
            "origem": "CAD",
            "tipo": "CONEXAO_ACOPLAMENTO",
            "descricao": desc_clean,
            "quantidade": qtd,
            "unidade": "un"
        })

    # 3. Demais Elementos Pontuais
    conns_set = set(cad_summary.get("conexoes_e_acoplamentos", {}).keys())
    for key, qtd in cad_summary.get("elementos_pontuais", {}).items():
        if key not in conns_set:
            desc_clean = re.sub(r"^\[.*?\]\s*", "", key)
            cad_elements.append({
                "origem": "CAD",
                "tipo": "EQUIPAMENTO_OUTROS",
                "descricao": desc_clean,
                "quantidade": qtd,
                "unidade": "un"
            })

    audit_results = []
    bom_matched_indices = set()

    for c_item in cad_elements:
        c_desc = c_item["descricao"].upper()
        best_match_idx = None
        best_score = 0

        for b_idx, b_item in enumerate(bom_items):
            b_desc = b_item["descricao"].upper()
            score = fuzz.token_set_ratio(c_desc, b_desc)
            if score > best_score and score >= 45:
                best_score = score
                best_match_idx = b_idx

        if best_match_idx is not None:
            bom_matched_indices.add(best_match_idx)
            matched_b = bom_items[best_match_idx]
            
            qtd_cad = c_item["quantidade"]
            qtd_bom = matched_b["quantidade"]
            diff = round(qtd_cad - qtd_bom, 2)

            if abs(diff) < 0.05:
                status = "COMPATÍVEL"
                status_tipo = "ok"
                detalhe = "Quantitativo e diâmetro exatos entre Projeto e Lista"
            elif diff > 0:
                status = f"DIVERGÊNCIA (+{diff} no Projeto)"
                status_tipo = "warning"
                detalhe = f"Projeto indica {qtd_cad} {c_item['unidade']}, lista indica {qtd_bom} {matched_b['unidade']}. Verificar se faltam {diff} na lista."
            else:
                status = f"DIVERGÊNCIA ({diff} na Lista)"
                status_tipo = "warning"
                detalhe = f"Lista do projetista tem {abs(diff)} a mais que o desenho CAD (possível folga ou outra prancha)."

            audit_results.append({
                "material": c_item["descricao"],
                "tipo": c_item["tipo"],
                "qtd_projeto": f"{qtd_cad} {c_item['unidade']}",
                "qtd_lista": f"{qtd_bom} {matched_b['unidade']}",
                "status": status,
                "status_tipo": status_tipo,
                "detalhe": detalhe,
                "score_match": round(best_score, 1)
            })
        else:
            audit_results.append({
                "material": c_item["descricao"],
                "tipo": c_item["tipo"],
                "qtd_projeto": f"{c_item['quantidade']} {c_item['unidade']}",
                "qtd_lista": "Não listado",
                "status": "POSSÍVEL OMISSÃO",
                "status_tipo": "danger",
                "detalhe": "Item identificado no desenho, porém não localizado na lista de materiais fornecida.",
                "score_match": 0
            })

    for b_idx, b_item in enumerate(bom_items):
        if b_idx not in bom_matched_indices:
            audit_results.append({
                "material": b_item["descricao"],
                "tipo": "ITEM_LISTA_EXTRA",
                "qtd_projeto": "Não desenhado",
                "qtd_lista": f"{b_item['quantidade']} {b_item['unidade']}",
                "status": "ITEM ADICIONAL NA LISTA",
                "status_tipo": "info",
                "detalhe": "Consta na lista do projetista, mas não foi identificado neste arquivo CAD.",
                "score_match": 0
            })

    return audit_results

def generate_budget_excel(cad_summary: dict, audit_results: list[dict] = None) -> bytes:
    wb = Workbook()
    
    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    accent_fill = PatternFill(start_color="2F5597", end_color="2F5597", fill_type="solid")
    zebra_fill = PatternFill(start_color="F2F5F9", end_color="F2F5F9", fill_type="solid")
    
    status_ok_fill = PatternFill(start_color="E2F0D9", end_color="E2F0D9", fill_type="solid")
    status_warn_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    status_danger_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
    
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_bold = Font(name="Calibri", size=11, bold=True)
    
    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )
    
    # ----------------------------------------------------
    # ABA 1: QUANTITATIVO SEGREGADO POR DIÂMETRO E CONEXÕES
    # ----------------------------------------------------
    ws1 = wb.active
    ws1.title = "1. Quantitativos por Diâmetro"
    ws1.views.sheetView[0].showGridLines = True
    
    ws1["A1"] = "ORÇAMENTO DE ENGENHARIA - QUANTITATIVOS, DIÂMETROS E CONEXÕES"
    ws1["A1"].font = Font(name="Calibri", size=14, bold=True, color="1F4E79")
    ws1["A2"] = f"Projeto: {cad_summary.get('arquivo', 'Projeto')} | Segregação de tubos, curvas, conexões e acoplamentos"
    ws1["A2"].font = Font(name="Calibri", size=10, italic=True, color="595959")
    
    headers1 = ["Item", "Categoria", "Descrição Técnica do Material", "Diâmetro Nominal", "Quantidade", "Unidade", "Tag no Projeto"]
    for col_num, h in enumerate(headers1, 1):
        cell = ws1.cell(row=4, column=col_num, value=h)
        cell.fill = header_fill
        cell.font = font_header
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[4].height = 24
    
    curr_row = 5
    item_idx = 1
    
    # 1. Tubulações por Diâmetro
    pipes = cad_summary.get("tubulacoes_por_diametro", {})
    for pipe_label, metros in sorted(pipes.items(), key=lambda x: x[1], reverse=True):
        parts = pipe_label.split(" - ")
        rede = parts[0]
        diam = parts[1] if len(parts) > 1 else "-"

        ws1.cell(row=curr_row, column=1, value=item_idx).alignment = Alignment(horizontal="center")
        ws1.cell(row=curr_row, column=2, value="Tubulação")
        ws1.cell(row=curr_row, column=3, value=f"Tubo para {rede}")
        ws1.cell(row=curr_row, column=4, value=diam).alignment = Alignment(horizontal="center")
        c_qtd = ws1.cell(row=curr_row, column=5, value=metros)
        c_qtd.number_format = '#,##0.00'
        c_qtd.alignment = Alignment(horizontal="right")
        ws1.cell(row=curr_row, column=6, value="m").alignment = Alignment(horizontal="center")
        ws1.cell(row=curr_row, column=7, value="-").alignment = Alignment(horizontal="center")
        
        for c in range(1, 8):
            ws1.cell(row=curr_row, column=c).border = thin_border
            if item_idx % 2 == 0: ws1.cell(row=curr_row, column=c).fill = zebra_fill
        curr_row += 1
        item_idx += 1

    # 2. Conexões e Acoplamentos
    for key, qtd in sorted(cad_summary.get("conexoes_e_acoplamentos", {}).items(), key=lambda x: x[1], reverse=True):
        desc_clean = re.sub(r"^\[.*?\]\s*", "", key)
        # Extrair diâmetro se houver
        m_diam = re.search(r"Ø[0-9]{1,2}(?:\.[0-9]+|\s*[1-3]/[2-8])?\"?", desc_clean)
        diam_val = m_diam.group(0) if m_diam else "-"

        tag_match = re.search(r"\(Tag:\s*(.*?)\)", key)
        tag_val = tag_match.group(1) if tag_match else "-"

        ws1.cell(row=curr_row, column=1, value=item_idx).alignment = Alignment(horizontal="center")
        ws1.cell(row=curr_row, column=2, value="Conexão / Acoplamento")
        ws1.cell(row=curr_row, column=3, value=desc_clean)
        ws1.cell(row=curr_row, column=4, value=diam_val).alignment = Alignment(horizontal="center")
        c_qtd = ws1.cell(row=curr_row, column=5, value=qtd)
        c_qtd.number_format = '#,##0'
        c_qtd.alignment = Alignment(horizontal="right")
        ws1.cell(row=curr_row, column=6, value="un").alignment = Alignment(horizontal="center")
        ws1.cell(row=curr_row, column=7, value=tag_val).alignment = Alignment(horizontal="center")

        for c in range(1, 8):
            ws1.cell(row=curr_row, column=c).border = thin_border
            if item_idx % 2 == 0: ws1.cell(row=curr_row, column=c).fill = zebra_fill
        curr_row += 1
        item_idx += 1

    # 3. Demais Elementos (Válvulas / Equipamentos)
    conns_set = set(cad_summary.get("conexoes_e_acoplamentos", {}).keys())
    for key, qtd in sorted(cad_summary.get("elementos_pontuais", {}).items(), key=lambda x: x[1], reverse=True):
        if key not in conns_set:
            desc_clean = re.sub(r"^\[.*?\]\s*", "", key)
            m_diam = re.search(r"Ø[0-9]{1,2}(?:\.[0-9]+|\s*[1-3]/[2-8])?\"?", desc_clean)
            diam_val = m_diam.group(0) if m_diam else "-"

            tag_match = re.search(r"\(Tag:\s*(.*?)\)", key)
            tag_val = tag_match.group(1) if tag_match else "-"

            ws1.cell(row=curr_row, column=1, value=item_idx).alignment = Alignment(horizontal="center")
            ws1.cell(row=curr_row, column=2, value="Válvula / Equipamento")
            ws1.cell(row=curr_row, column=3, value=desc_clean)
            ws1.cell(row=curr_row, column=4, value=diam_val).alignment = Alignment(horizontal="center")
            c_qtd = ws1.cell(row=curr_row, column=5, value=qtd)
            c_qtd.number_format = '#,##0'
            c_qtd.alignment = Alignment(horizontal="right")
            ws1.cell(row=curr_row, column=6, value="un").alignment = Alignment(horizontal="center")
            ws1.cell(row=curr_row, column=7, value=tag_val).alignment = Alignment(horizontal="center")

            for c in range(1, 8):
                ws1.cell(row=curr_row, column=c).border = thin_border
                if item_idx % 2 == 0: ws1.cell(row=curr_row, column=c).fill = zebra_fill
            curr_row += 1
            item_idx += 1

    # ----------------------------------------------------
    # ABA 2: MATRIZ DE COTAÇÃO DE FORNECEDORES
    # ----------------------------------------------------
    ws2 = wb.create_sheet(title="2. Matriz de Cotação")
    ws2.views.sheetView[0].showGridLines = True
    
    ws2["A1"] = "MATRIZ DE COTAÇÃO COM PREÇOS DE MERCADO"
    ws2["A1"].font = Font(name="Calibri", size=14, bold=True, color="1F4E79")
    ws2["A2"] = "Planilha preparada para preenchimento de preços unitários por fornecedor."
    ws2["A2"].font = Font(name="Calibri", size=10, italic=True, color="595959")
    
    headers2 = [
        "Item", "Descrição do Material", "Diâmetro", "Qtd", "Un", 
        "Fornecedor A (Unit)", "Fornecedor A (Total)",
        "Fornecedor B (Unit)", "Fornecedor B (Total)",
        "Melhor Preço Unit", "Economia Est. (%)"
    ]
    for col_num, h in enumerate(headers2, 1):
        cell = ws2.cell(row=4, column=col_num, value=h)
        cell.fill = accent_fill
        cell.font = font_header
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws2.row_dimensions[4].height = 24
    
    r2 = 5
    idx2 = 1
    # Preencher itens da aba 1
    for row_source in range(5, curr_row):
        ws2.cell(row=r2, column=1, value=idx2).alignment = Alignment(horizontal="center")
        ws2.cell(row=r2, column=2, value=ws1.cell(row=row_source, column=3).value)
        ws2.cell(row=r2, column=3, value=ws1.cell(row=row_source, column=4).value).alignment = Alignment(horizontal="center")
        ws2.cell(row=r2, column=4, value=ws1.cell(row=row_source, column=5).value).alignment = Alignment(horizontal="right")
        ws2.cell(row=r2, column=5, value=ws1.cell(row=row_source, column=6).value).alignment = Alignment(horizontal="center")
        
        ws2.cell(row=r2, column=6, value=0.0).number_format = 'R$ #,##0.00'
        ws2.cell(row=r2, column=7, value=f"=D{r2}*F{r2}").number_format = 'R$ #,##0.00'
        ws2.cell(row=r2, column=8, value=0.0).number_format = 'R$ #,##0.00'
        ws2.cell(row=r2, column=9, value=f"=D{r2}*H{r2}").number_format = 'R$ #,##0.00'
        ws2.cell(row=r2, column=10, value=f"=IF(MIN(F{r2},H{r2})>0,MIN(F{r2},H{r2}),MAX(F{r2},H{r2}))").number_format = 'R$ #,##0.00'
        ws2.cell(row=r2, column=11, value=f"=IF(MAX(F{r2},H{r2})>0,(MAX(F{r2},H{r2})-J{r2})/MAX(F{r2},H{r2}),0)").number_format = '0.0%'
        
        for c in range(1, 12): ws2.cell(row=r2, column=c).border = thin_border
        r2 += 1
        idx2 += 1

    ws2.cell(row=r2, column=2, value="TOTAL ESTIMADO GERAL").font = font_bold
    ws2.cell(row=r2, column=7, value=f"=SUM(G5:G{r2-1})").font = font_bold
    ws2.cell(row=r2, column=7).number_format = 'R$ #,##0.00'
    ws2.cell(row=r2, column=9, value=f"=SUM(I5:I{r2-1})").font = font_bold
    ws2.cell(row=r2, column=9).number_format = 'R$ #,##0.00'

    # ----------------------------------------------------
    # ABA 3: AUDITORIA CRUZADA (SE HOUVER BOM)
    # ----------------------------------------------------
    if audit_results:
        ws3 = wb.create_sheet(title="3. Auditoria Lista Projetista")
        ws3.views.sheetView[0].showGridLines = True
        
        ws3["A1"] = "RELATÓRIO COMPARATIVO COM SEGREGAÇÃO DE DIÂMETROS"
        ws3["A1"].font = Font(name="Calibri", size=14, bold=True, color="1F4E79")
        ws3["A2"] = "Cruzamento automático para identificação de divergências e itens ausentes."
        ws3["A2"].font = Font(name="Calibri", size=10, italic=True, color="595959")
        
        headers3 = ["Item", "Material / Descrição", "Qtd Projeto", "Qtd Lista Projetista", "Resultado da Auditoria", "Análise Técnica"]
        for col_num, h in enumerate(headers3, 1):
            cell = ws3.cell(row=4, column=col_num, value=h)
            cell.fill = header_fill
            cell.font = font_header
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws3.row_dimensions[4].height = 24
        
        for r_idx, res in enumerate(audit_results, 5):
            ws3.cell(row=r_idx, column=1, value=r_idx-4).alignment = Alignment(horizontal="center")
            ws3.cell(row=r_idx, column=2, value=res["material"])
            ws3.cell(row=r_idx, column=3, value=res["qtd_projeto"]).alignment = Alignment(horizontal="right")
            ws3.cell(row=r_idx, column=4, value=res["qtd_lista"]).alignment = Alignment(horizontal="right")
            
            c_status = ws3.cell(row=r_idx, column=5, value=res["status"])
            c_status.font = font_bold
            c_status.alignment = Alignment(horizontal="center")
            
            if res["status_tipo"] == "ok": c_status.fill = status_ok_fill
            elif res["status_tipo"] == "warning": c_status.fill = status_warn_fill
            elif res["status_tipo"] == "danger": c_status.fill = status_danger_fill
                
            ws3.cell(row=r_idx, column=6, value=res["detalhe"])
            for c in range(1, 7): ws3.cell(row=r_idx, column=c).border = thin_border

    # Auto-ajuste de largura de colunas
    for sheet in wb.worksheets:
        for col in sheet.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or '')
                if len(val_str) > max_len and len(val_str) < 80:
                    max_len = len(val_str)
            sheet.column_dimensions[col_letter].width = max(max_len + 4, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()
