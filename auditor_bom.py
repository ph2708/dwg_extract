#!/usr/bin/env python3
"""
Módulo de Auditoria, Cruzamento com Lista de Materiais do Projetista (BOM)
e Geração de Planilhas de Orçamento em Excel (.xlsx) Profissionais
com Segregação por Disciplina, Diâmetro e Detalhamento Multi-Pavimentos
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

    # 1. Tubulações e Eletrodutos por Diâmetro
    pipes = cad_summary.get("tubulacoes_por_diametro", {})
    if pipes:
        for p_name, metros in pipes.items():
            cad_elements.append({
                "origem": "CAD",
                "tipo": "TUBULACAO_ELETRODUTO",
                "descricao": f"Rede: {p_name}",
                "quantidade": metros,
                "unidade": "m"
            })
    else:
        for layer, metros in cad_summary.get("metragens_lineares_metros", {}).items():
            cad_elements.append({
                "origem": "CAD",
                "tipo": "TUBULACAO_ELETRODUTO",
                "descricao": f"Rede ({layer})",
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

    # 3. Elementos Pontuais (Tomadas, Interruptores, Válvulas, Bombas, Luminárias)
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
    sub_fill = PatternFill(start_color="3B629B", end_color="3B629B", fill_type="solid")
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

    # Identifica pavimentos ativos com itens
    det_pav = cad_summary.get("detalhamento_por_pavimento", {})
    active_floors = [f for f, d in det_pav.items() if d.get("tubulacoes_por_diametro") or d.get("elementos_pontuais")]
    has_multi_floors = len(active_floors) > 1

    # Classificador de disciplina e prioridade de ordenação
    def classify_item(cat: str, desc: str) -> tuple[str, int]:
        combined = (cat + " " + desc).upper()
        if any(k in combined for k in ["ELETRODUTO", "CONDUITE", "CONDUTO", "CABO", "LEITO", "ELETROCALHA"]):
            return "Elétrica", 1
        if any(k in combined for k in ["TOMADA"]):
            return "Elétrica", 2
        if any(k in combined for k in ["INTERRUPTOR", "LIGAÇÃO", "LIG-"]):
            return "Elétrica", 3
        if any(k in combined for k in ["ILUMINAÇÃO", "ILUMINACAO", "LUMINÁRIA", "LUMINARIA", "SPOT", "LUM", "ESPETO"]):
            return "Elétrica", 4
        if any(k in combined for k in ["PAINEL", "QUADRO", "QDG", "QGBT", "QD"]):
            return "Elétrica", 5
        if any(k in combined for k in ["CAMPAINHA", "SENSOR"]):
            return "Elétrica", 6
        if any(k in combined for k in ["TUBULAÇÃO", "TUBULACAO", "TUBO", "CANO", "REDE"]):
            return "Hidráulica / Incêndio", 7
        if any(k in combined for k in ["CONEXÃO", "CONEXAO", "CURVA", "TÊ", "TEE", "ACOPLAMENTO", "FLANGE", "JUNTA", "REDUÇÃO"]):
            return "Hidráulica / Incêndio", 8
        if any(k in combined for k in ["VÁLVULA", "VALVULA", "REGISTRO", "RETENÇÃO", "GAVETA"]):
            return "Hidráulica / Incêndio", 9
        if any(k in combined for k in ["BOMBA", "MOTOR", "EQUIPAMENTO", "SPRINKLER", "HIDRANTE"]):
            return "Hidráulica / Equipamentos", 10
        if any(k in combined for k in ["SUPORTE"]):
            return "Suportes / Fixação", 11
        return "Geral", 12

    # ----------------------------------------------------
    # ABA 1: QUANTITATIVOS GERAIS CONSOLIDADOS
    # ----------------------------------------------------
    ws1 = wb.active
    ws1.title = "1. Quantitativos Gerais"
    ws1.views.sheetView[0].showGridLines = True
    
    ws1["A1"] = "ORÇAMENTO E AUDITORIA DE ENGENHARIA - LISTA DE MATERIAIS CONSOLIDADA"
    ws1["A1"].font = Font(name="Calibri", size=14, bold=True, color="1F4E79")
    ws1["A2"] = f"Projeto: {cad_summary.get('arquivo', 'Projeto')} | Escala: {cad_summary.get('auditoria_escala', {}).get('unidade_detectada', 'N/D')}"
    ws1["A2"].font = Font(name="Calibri", size=10, italic=True, color="595959")
    
    headers1 = [
        "Item", "Disciplina", "Categoria", "Descrição Técnica do Material",
        "Diâmetro Nominal", "Quantidade", "Unidade", "Pavimento / Local", "Tag no Projeto"
    ]
    for col_num, h in enumerate(headers1, 1):
        cell = ws1.cell(row=4, column=col_num, value=h)
        cell.fill = header_fill
        cell.font = font_header
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[4].height = 24
    
    curr_row = 5
    item_idx = 1
    consolidated_items = []

    # 1.1 Tubulações e Eletrodutos
    pipes = cad_summary.get("tubulacoes_por_diametro", {})
    for pipe_label, metros in pipes.items():
        parts = pipe_label.split(" - ")
        rede = parts[0]
        diam = parts[1] if len(parts) > 1 else "-"
        
        # Localiza pavimentos onde esse tubo existe
        locais = []
        for fn in active_floors:
            if pipe_label in det_pav[fn].get("tubulacoes_por_diametro", {}):
                locais.append(fn)
        loc_str = ", ".join(locais) if locais else "Área Integral"

        disc, prio = classify_item("Tubulação", rede)
        consolidated_items.append({
            "disciplina": disc,
            "categoria": "Eletroduto / Conduto" if "Elétrica" in disc else "Tubulação",
            "descricao": rede,
            "diametro": diam,
            "quantidade": metros,
            "unidade": "m",
            "local": loc_str,
            "tag": "-",
            "prio": prio,
            "raw_key": pipe_label,
            "is_linear": True
        })

    # 1.2 Conexões, Válvulas, Tomadas, Interruptores e Equipamentos
    for key, qtd in cad_summary.get("elementos_pontuais", {}).items():
        m_cat = re.match(r"^\[(.*?)\]\s*(.*)$", key)
        if m_cat:
            cat = m_cat.group(1).strip()
            desc_clean = m_cat.group(2).strip()
        else:
            cat = "Componente"
            desc_clean = key.strip()

        m_diam = re.search(r"Ø[0-9]{1,2}(?:\.[0-9]+|\s*[1-3]/[2-8])?\"?", desc_clean)
        diam_val = m_diam.group(0) if m_diam else "-"

        # Localiza pavimentos onde esse item existe
        locais = []
        for fn in active_floors:
            if key in det_pav[fn].get("elementos_pontuais", {}):
                locais.append(fn)
        loc_str = ", ".join(locais) if locais else "Área Integral"

        disc, prio = classify_item(cat, desc_clean)
        consolidated_items.append({
            "disciplina": disc,
            "categoria": cat,
            "descricao": desc_clean,
            "diametro": diam_val,
            "quantidade": qtd,
            "unidade": "un",
            "local": loc_str,
            "tag": "-",
            "prio": prio,
            "raw_key": key,
            "is_linear": False
        })

    # Ordena por Prioridade de Engenharia e Quantidade Decrescente
    sorted_items = sorted(consolidated_items, key=lambda x: (x["prio"], -x["quantidade"]))

    for comp in sorted_items:
        ws1.cell(row=curr_row, column=1, value=item_idx).alignment = Alignment(horizontal="center")
        ws1.cell(row=curr_row, column=2, value=comp["disciplina"]).alignment = Alignment(horizontal="center")
        ws1.cell(row=curr_row, column=3, value=comp["categoria"])
        ws1.cell(row=curr_row, column=4, value=comp["descricao"])
        ws1.cell(row=curr_row, column=5, value=comp["diametro"]).alignment = Alignment(horizontal="center")
        
        c_qtd = ws1.cell(row=curr_row, column=6, value=comp["quantidade"])
        if comp["is_linear"]:
            c_qtd.number_format = '#,##0.00'
        else:
            c_qtd.number_format = '#,##0'
        c_qtd.alignment = Alignment(horizontal="right")
        
        ws1.cell(row=curr_row, column=7, value=comp["unidade"]).alignment = Alignment(horizontal="center")
        ws1.cell(row=curr_row, column=8, value=comp["local"])
        ws1.cell(row=curr_row, column=9, value=comp["tag"]).alignment = Alignment(horizontal="center")

        for c in range(1, 10):
            ws1.cell(row=curr_row, column=c).border = thin_border
            if item_idx % 2 == 0:
                ws1.cell(row=curr_row, column=c).fill = zebra_fill
        curr_row += 1
        item_idx += 1

    # ----------------------------------------------------
    # ABA 2: RESUMO POR PAVIMENTO (SE HOUVER MULTI-PAVIMENTOS)
    # ----------------------------------------------------
    sheet_offset = 2
    if has_multi_floors:
        ws_pav = wb.create_sheet(title="2. Resumo por Pavimento")
        ws_pav.views.sheetView[0].showGridLines = True
        
        ws_pav["A1"] = "DISTRIBUIÇÃO DE MATERIAIS POR PAVIMENTO / ANDAR"
        ws_pav["A1"].font = Font(name="Calibri", size=14, bold=True, color="1F4E79")
        ws_pav["A2"] = "Detalhamento analítico de quantitativos segregados por nível para compras e suprimentos."
        ws_pav["A2"].font = Font(name="Calibri", size=10, italic=True, color="595959")
        
        headers_pav = ["Item", "Disciplina", "Categoria", "Descrição do Material", "Diâmetro", "Un"] + active_floors + ["Total Geral"]
        for col_num, h in enumerate(headers_pav, 1):
            cell = ws_pav.cell(row=4, column=col_num, value=h)
            cell.fill = accent_fill
            cell.font = font_header
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws_pav.row_dimensions[4].height = 24
        
        rp = 5
        idx_p = 1
        for comp in sorted_items:
            ws_pav.cell(row=rp, column=1, value=idx_p).alignment = Alignment(horizontal="center")
            ws_pav.cell(row=rp, column=2, value=comp["disciplina"]).alignment = Alignment(horizontal="center")
            ws_pav.cell(row=rp, column=3, value=comp["categoria"])
            ws_pav.cell(row=rp, column=4, value=comp["descricao"])
            ws_pav.cell(row=rp, column=5, value=comp["diametro"]).alignment = Alignment(horizontal="center")
            ws_pav.cell(row=rp, column=6, value=comp["unidade"]).alignment = Alignment(horizontal="center")
            
            col_start = 7
            for f_idx, fn in enumerate(active_floors):
                col_curr = col_start + f_idx
                val_floor = 0.0
                if comp["is_linear"]:
                    val_floor = det_pav[fn].get("tubulacoes_por_diametro", {}).get(comp["raw_key"], 0.0)
                else:
                    val_floor = det_pav[fn].get("elementos_pontuais", {}).get(comp["raw_key"], 0)
                
                c_cell = ws_pav.cell(row=rp, column=col_curr, value=val_floor)
                c_cell.number_format = '#,##0.00' if comp["is_linear"] else '#,##0'
                c_cell.alignment = Alignment(horizontal="right")
                ws_pav.cell(row=rp, column=col_curr).border = thin_border
            
            # Total Geral
            col_tot = col_start + len(active_floors)
            c_tot = ws_pav.cell(row=rp, column=col_tot, value=comp["quantidade"])
            c_tot.font = font_bold
            c_tot.number_format = '#,##0.00' if comp["is_linear"] else '#,##0'
            c_tot.alignment = Alignment(horizontal="right")
            ws_pav.cell(row=rp, column=col_tot).border = thin_border

            for c in range(1, col_tot + 1):
                ws_pav.cell(row=rp, column=c).border = thin_border
                if idx_p % 2 == 0:
                    ws_pav.cell(row=rp, column=c).fill = zebra_fill
            rp += 1
            idx_p += 1

        sheet_offset += 1

    # ----------------------------------------------------
    # ABA DE COTAÇÃO: MATRIZ DE PREÇOS DE FORNECEDORES
    # ----------------------------------------------------
    cotacao_title = f"{sheet_offset}. Matriz de Cotação"
    ws_cot = wb.create_sheet(title=cotacao_title)
    ws_cot.views.sheetView[0].showGridLines = True
    
    ws_cot["A1"] = "MATRIZ DE COTAÇÃO COM PREÇOS DE MERCADO"
    ws_cot["A1"].font = Font(name="Calibri", size=14, bold=True, color="1F4E79")
    ws_cot["A2"] = "Planilha preparada para preenchimento de preços unitários por fornecedor."
    ws_cot["A2"].font = Font(name="Calibri", size=10, italic=True, color="595959")
    
    headers_cot = [
        "Item", "Descrição do Material", "Diâmetro", "Qtd", "Un", 
        "Fornecedor A (Unit)", "Fornecedor A (Total)",
        "Fornecedor B (Unit)", "Fornecedor B (Total)",
        "Melhor Preço Unit", "Economia Est. (%)"
    ]
    for col_num, h in enumerate(headers_cot, 1):
        cell = ws_cot.cell(row=4, column=col_num, value=h)
        cell.fill = accent_fill
        cell.font = font_header
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws_cot.row_dimensions[4].height = 24
    
    r2 = 5
    idx2 = 1
    for comp in sorted_items:
        ws_cot.cell(row=r2, column=1, value=idx2).alignment = Alignment(horizontal="center")
        ws_cot.cell(row=r2, column=2, value=comp["descricao"])
        ws_cot.cell(row=r2, column=3, value=comp["diametro"]).alignment = Alignment(horizontal="center")
        c_qtd = ws_cot.cell(row=r2, column=4, value=comp["quantidade"])
        c_qtd.number_format = '#,##0.00' if comp["is_linear"] else '#,##0'
        c_qtd.alignment = Alignment(horizontal="right")
        ws_cot.cell(row=r2, column=5, value=comp["unidade"]).alignment = Alignment(horizontal="center")
        
        ws_cot.cell(row=r2, column=6, value=0.0).number_format = 'R$ #,##0.00'
        ws_cot.cell(row=r2, column=7, value=f"=D{r2}*F{r2}").number_format = 'R$ #,##0.00'
        ws_cot.cell(row=r2, column=8, value=0.0).number_format = 'R$ #,##0.00'
        ws_cot.cell(row=r2, column=9, value=f"=D{r2}*H{r2}").number_format = 'R$ #,##0.00'
        ws_cot.cell(row=r2, column=10, value=f"=IF(MIN(F{r2},H{r2})>0,MIN(F{r2},H{r2}),MAX(F{r2},H{r2}))").number_format = 'R$ #,##0.00'
        ws_cot.cell(row=r2, column=11, value=f"=IF(MAX(F{r2},H{r2})>0,(MAX(F{r2},H{r2})-J{r2})/MAX(F{r2},H{r2}),0)").number_format = '0.0%'
        
        for c in range(1, 12):
            ws_cot.cell(row=r2, column=c).border = thin_border
        r2 += 1
        idx2 += 1

    ws_cot.cell(row=r2, column=2, value="TOTAL ESTIMADO GERAL").font = font_bold
    ws_cot.cell(row=r2, column=7, value=f"=SUM(G5:G{r2-1})").font = font_bold
    ws_cot.cell(row=r2, column=7).number_format = 'R$ #,##0.00'
    ws_cot.cell(row=r2, column=9, value=f"=SUM(I5:I{r2-1})").font = font_bold
    ws_cot.cell(row=r2, column=9).number_format = 'R$ #,##0.00'
    sheet_offset += 1

    # ----------------------------------------------------
    # ABA DE AUDITORIA CRUZADA (SE HOUVER LISTA DO PROJETISTA)
    # ----------------------------------------------------
    if audit_results:
        aud_title = f"{sheet_offset}. Auditoria Lista Projetista"
        ws_aud = wb.create_sheet(title=aud_title)
        ws_aud.views.sheetView[0].showGridLines = True
        
        ws_aud["A1"] = "RELATÓRIO COMPARATIVO - PROJETO CAD VS LISTA DO PROJETISTA"
        ws_aud["A1"].font = Font(name="Calibri", size=14, bold=True, color="1F4E79")
        ws_aud["A2"] = "Cruzamento automático para identificação de divergências, sobras e omissões."
        ws_aud["A2"].font = Font(name="Calibri", size=10, italic=True, color="595959")
        
        headers_aud = ["Item", "Material / Descrição", "Qtd Projeto", "Qtd Lista Projetista", "Resultado da Auditoria", "Análise Técnica"]
        for col_num, h in enumerate(headers_aud, 1):
            cell = ws_aud.cell(row=4, column=col_num, value=h)
            cell.fill = header_fill
            cell.font = font_header
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws_aud.row_dimensions[4].height = 24
        
        for r_idx, res in enumerate(audit_results, 5):
            ws_aud.cell(row=r_idx, column=1, value=r_idx-4).alignment = Alignment(horizontal="center")
            ws_aud.cell(row=r_idx, column=2, value=res["material"])
            ws_aud.cell(row=r_idx, column=3, value=res["qtd_projeto"]).alignment = Alignment(horizontal="right")
            ws_aud.cell(row=r_idx, column=4, value=res["qtd_lista"]).alignment = Alignment(horizontal="right")
            
            c_status = ws_aud.cell(row=r_idx, column=5, value=res["status"])
            c_status.font = font_bold
            c_status.alignment = Alignment(horizontal="center")
            
            if res["status_tipo"] == "ok":
                c_status.fill = status_ok_fill
            elif res["status_tipo"] == "warning":
                c_status.fill = status_warn_fill
            elif res["status_tipo"] == "danger":
                c_status.fill = status_danger_fill
                
            ws_aud.cell(row=r_idx, column=6, value=res["detalhe"])
            for c in range(1, 7):
                ws_aud.cell(row=r_idx, column=c).border = thin_border

    # Auto-ajuste de largura de colunas para todas as abas
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
