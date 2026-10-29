#!/usr/bin/env python3
"""
Extrator e Auditor Universal Multidisciplinar de Projetos CAD (DWG / DXF)
Com Calibração e Detecção Automática de Escalas, Auditoria de Medidas e Segregação de Diâmetros
"""

import os
import sys
import json
import re
import subprocess
from pathlib import Path
from collections import Counter
import ezdxf

DISCIPLINES_KEYWORDS = {
    "HIDRAULICA_TUBULACAO": [
        "TUBO", "TUBULACAO", "TUBULAÇÃO", "CANO", "PIPE", "INCÊNDIO", "INCENDIO",
        "DIESEL", "COBRE", "HIDRANTE", "SPRINKLER", "VALV", "VÁLVULA", "FLANGE",
        "ESGOTO", "AGUA", "ÁGUA", "PLUVIAL", "RETENÇÃO", "BOMBA", "CONEXÃO", "CONEXAO",
        "ACOPLAMENTO", "CURVA", "REDUÇÃO", "REDUCAO", "TE "
    ],
    "ELETRICA_AUTOMACAO_DETECCAO": [
        "ELETRODUTO", "CONDUITE", "CONDULETE", "CABO", "FIACAO", "FIAÇÃO", "CIRCUITO",
        "QUADRO", "QGBT", "DETECÇÃO", "DETECCAO", "DETECTOR", "ACIONADOR", "SIRENE",
        "ILUMINACAO", "ILUMINAÇÃO", "TOMADA", "LAÇO", "LEITO", "ELETROCALHA", "PERFILADO",
        "PAINEL", "INVERSOR", "TRANSFORMADOR", "TERRA", "SPDA"
    ],
    "CLIMATIZACAO_HVAC": [
        "DUTO", "DIFUSOR", "GRELHA", "FANCOIL", "CHILLER", "SPLIT", "EXAUSTAO", "EXAUSTÃO"
    ]
}

ANONYMOUS_PREFIXES = ("Z$W", "zw$", "A$C", "*U", "*D", "*X")

DIAM_REGEX = re.compile(
    r"(?:[Ø∅]|DN\s*)?([0-9]{1,2}(?:\.[0-9]+|\s*[1-3]/[2-8])?)\s*\"|(?:DN\s*([0-9]{2,3}))",
    re.IGNORECASE
)

def convert_dwg_to_dxf(dwg_path: str, dxf_path: str) -> tuple[bool, str]:
    try:
        cmd = ["dwg2dxf", "-y", "-o", dxf_path, dwg_path]
        res = subprocess.run(cmd, capture_output=True, text=True)
        success = os.path.exists(dxf_path) and os.path.getsize(dxf_path) > 0
        stderr = res.stderr or ""
        return success, stderr
    except Exception as e:
        return False, str(e)

def clean_cad_text(text: str) -> str:
    if not text:
        return ""
    replacements = {
        "%%C": "Ø",
        "%%c": "Ø",
        "%%D": "°",
        "%%d": "°",
        "%%P": "±",
        "%%p": "±",
        "\\P": "\n",
        "\\p": "\n",
        "\\X": " - ",
        "\\x": " - ",
    }
    cleaned = text
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r"\\A[0-9];", "", cleaned)
    cleaned = re.sub(r"\{?\\f[^;]*;([^}]*)\}?", r"\1", cleaned)
    cleaned = re.sub(r"\\[A-Za-z0-9\.\,]+;", "", cleaned)
    return cleaned.strip()

def identify_element_discipline(name_or_layer: str) -> str:
    upper = name_or_layer.upper()
    for disc, kws in DISCIPLINES_KEYWORDS.items():
        if any(k in upper for k in kws):
            return disc
    return "GERAL"

def clean_block_name(raw_name: str) -> str:
    cleaned = re.sub(r"(-flat-\d+)+", "", raw_name)
    return cleaned.strip()

def is_anonymous_block(name: str) -> bool:
    return any(name.startswith(p) for p in ANONYMOUS_PREFIXES)

def extract_diameter_from_string(text: str) -> str:
    if not text:
        return ""
    m = DIAM_REGEX.search(text)
    if m:
        val = m.group(0).strip()
        val = val.replace("∅", "Ø")
        if not val.startswith("Ø") and not val.upper().startswith("DN"):
            val = "Ø" + val
        return val
    return ""

def classify_component_category(layer: str, name: str) -> str:
    combined = (layer + " " + name).upper()
    if any(k in combined for k in ["CURVA", "COTOVELO", "ELBOW"]):
        return "Conexão - Curva"
    if any(k in combined for k in ["TE ", "TEE", "TÊ"]):
        return "Conexão - Tê"
    if any(k in combined for k in ["REDUÇÃO", "REDUCAO", "REDUCER"]):
        return "Conexão - Redução"
    if any(k in combined for k in ["ACOPLAMENTO", "COUPLING", "JUNTA MECANICA", "JUNTA MECÂNICA"]):
        return "Acoplamento Ranhurado"
    if any(k in combined for k in ["ADAPTADOR", "FLANGE", "CONTRAFLANGE"]):
        return "Flange / Adaptador"
    if any(k in combined for k in ["VALV", "VÁLVULA", "RETENÇÃO", "RETENCAO", "GAVETA", "ESFERA", "BORBOLETA"]):
        return "Válvula / Registro"
    if any(k in combined for k in ["BOMBA", "PUMP", "MOTOR"]):
        return "Equipamento / Bomba"
    if any(k in combined for k in ["SPRINKLER", "SPK", "BICO"]):
        return "Sprinkler"
    if any(k in combined for k in ["HIDRANTE"]):
        return "Hidrante"
    if any(k in combined for k in ["SUPORTE", "STAND", "PENDURAL"]):
        return "Suporte / Fixação"
    return "Acessório / Conexão"

def detect_cad_scale_factor(msp) -> tuple[float, str, str, list[dict]]:
    """
    Detecta automaticamente o Fator de Escala do Desenho comparando:
    1. Medições reais de cotas (DIMENSION) presentes no arquivo
    2. Comprimento das linhas do desenho
    Retorna: (divisor_para_metros, nome_unidade, explicacao_tecnica, lista_cotas_amostra)
    """
    dimension_samples = []
    for d in msp.query("DIMENSION"):
        val = getattr(d.dxf, "actual_measurement", None)
        txt = getattr(d.dxf, "text", "")
        if val and 10 < val < 5000000:
            dimension_samples.append({
                "valor_cad": round(val, 2),
                "texto_cota": txt or f"{val:.0f}",
                "layer": d.dxf.layer
            })

    line_lengths = []
    for l in msp.query("LINE"):
        mag = (l.dxf.start - l.dxf.end).magnitude
        if mag > 10:
            line_lengths.append(mag)

    vals = [s["valor_cad"] for s in dimension_samples] if dimension_samples else line_lengths
    if not vals:
        return 1000.0, "Milímetros (mm)", "Padrão de engenharia mecânica assumido (1 unidade = 1 mm).", []

    vals_sorted = sorted(vals)
    median_val = vals_sorted[len(vals_sorted) // 2]

    # Heurística de Calibração:
    if median_val >= 300:
        divisor = 1000.0
        unit = "Milímetros (mm)"
        expl = f"Mediana geométrica = {median_val:.1f} unidades. O desenho foi construído em milímetros (1 unidade CAD = 1 mm). Fator de conversão: divide por 1000 para metros."
    elif median_val >= 20:
        divisor = 100.0
        unit = "Centímetros (cm)"
        expl = f"Mediana geométrica = {median_val:.1f} unidades. O desenho está em centímetros. Fator de conversão: divide por 100 para metros."
    else:
        divisor = 1.0
        unit = "Metros (m)"
        expl = f"Mediana geométrica = {median_val:.2f} unidades. O desenho está em metros (1 unidade CAD = 1 metro)."

    return divisor, unit, expl, dimension_samples[:10]

def extract_project_summary(file_path: str, user_scale_override: str = None) -> dict:
    path = Path(file_path)
    dxf_path = str(file_path)
    temp_created = False
    conversion_warnings = ""

    if path.suffix.lower() == ".dwg":
        target_dxf = path.with_suffix(".temp.dxf")
        success, conversion_warnings = convert_dwg_to_dxf(str(path), str(target_dxf))
        if not success:
            raise RuntimeError("Falha ao converter o arquivo DWG.")
        dxf_path = str(target_dxf)
        temp_created = True

    try:
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()

        # Calibração e averiguação de Escala
        divisor_auto, unit_auto, expl_auto, sample_dims = detect_cad_scale_factor(msp)

        divisor = divisor_auto
        unit_name = unit_auto
        if user_scale_override:
            u_clean = user_scale_override.lower()
            if "mm" in u_clean or "mili" in u_clean:
                divisor = 1000.0
                unit_name = "Milímetros (mm) [Manual]"
            elif "cm" in u_clean or "centi" in u_clean:
                divisor = 100.0
                unit_name = "Centímetros (cm) [Manual]"
            elif "metro" in u_clean or u_clean == "m":
                divisor = 1.0
                unit_name = "Metros (m) [Manual]"

        summary = {
            "arquivo": path.name,
            "extensao": path.suffix.lower(),
            "disciplinas_detectadas": set(),
            "inconsistencias": [],
            "auditoria_escala": {
                "unidade_detectada": unit_name,
                "divisor_aplicado": divisor,
                "explicacao_tecnica": expl_auto,
                "cotas_amostra_para_conferencia": sample_dims
            },
            "tubulacoes_por_diametro": {},
            "conexoes_e_acoplamentos": {},
            "elementos_pontuais": {},
            "metragens_lineares_metros": {},
            "tags_identificadores": [],
            "especificacoes_tecnicas": [],
            "notas_de_prancha": [],
            "topologia_redes": [],
            "cotas_principais_metros": []
        }

        # 1. TEXTOS E DIÂMETROS
        cad_texts = []
        diam_points = []
        specs = set()
        tags = set()
        classes_pressao = set()

        for ent in msp:
            if ent.dxftype() in ("TEXT", "MTEXT"):
                raw = ent.dxf.text if ent.dxftype() == "TEXT" else ent.text
                txt = clean_cad_text(raw)
                if not txt:
                    continue
                loc = getattr(ent.dxf, "insert", None)
                if loc:
                    cad_texts.append((loc.x, loc.y, txt, ent.dxf.layer))
                    d_val = extract_diameter_from_string(txt)
                    if d_val:
                        diam_points.append((loc.x, loc.y, d_val))

                upper = txt.upper()
                if re.match(r"^[A-Z]{1,4}[-_]?[0-9]{1,3}[A-Z]?$", txt) or "SOBE PARA" in upper or "DESCE PARA" in upper:
                    tags.add(f"[{ent.dxf.layer}] {txt}")

                if any(w in upper for w in [
                    "VALVULA", "VÁLVULA", "JUNTA", "FLANGE", "RETENÇÃO", "BOMBA", "Ø",
                    "ANSI", "CLASSE", "PSI", "ASTM", "NBR", "SCH", "GPM",
                    "CABO", "ELETRODUTO", "CIRCUITO", "DETECTOR", "TENSÃO", "KVA", "AMPERE"
                ]):
                    specs.add(txt)

                if "150 PSI" in upper or "CLASSE 150" in upper:
                    classes_pressao.add("150 psi")
                if "300 PSI" in upper or "CLASSE 300" in upper:
                    classes_pressao.add("300 psi")

        if len(classes_pressao) > 1:
            summary["inconsistencias"].append({
                "tipo": "CONFLITO_PRESSAO",
                "gravidade": "ALERTA TÉCNICO",
                "mensagem": f"Classes de pressão mistas identificadas no mesmo projeto ({', '.join(classes_pressao)}). Exige verificação de contraflanges e juntas de transição."
            })

        def find_nearest_diameter(x, y, max_dist=5000) -> str:
            best_dist = max_dist
            best_diam = ""
            for dx, dy, dval in diam_points:
                dist = ((dx-x)**2 + (dy-y)**2)**0.5
                if dist < best_dist:
                    best_dist = dist
                    best_diam = dval
            return best_diam

        def resolve_nearest_tag(x, y, max_dist=2000) -> str:
            best_dist = max_dist
            best_label = ""
            for tx, ty, tval, _ in cad_texts:
                d = ((tx-x)**2 + (ty-y)**2)**0.5
                if d < best_dist and len(tval) < 25:
                    best_dist = d
                    best_label = tval
            return best_label

        # 2. METRAGEM LINEAR E SEGREGAÇÃO (CALIBRADO COM DIVISOR DE ESCALA)
        layer_lengths = {}
        diam_pipe_lengths = {}
        linear_segments = []

        min_pipe_thresh = 400.0 * (divisor / 1000.0) # equivalente a 40cm na escala
        min_seg_thresh = 800.0 * (divisor / 1000.0)

        for line in msp.query("LINE"):
            length = (line.dxf.start - line.dxf.end).magnitude
            layer = line.dxf.layer
            layer_lengths[layer] = layer_lengths.get(layer, 0.0) + length
            disc = identify_element_discipline(layer)

            if disc != "GERAL" and length > min_pipe_thresh:
                summary["disciplinas_detectadas"].add(disc)
                mx = (line.dxf.start.x + line.dxf.end.x) / 2
                my = (line.dxf.start.y + line.dxf.end.y) / 2
                d_found = find_nearest_diameter(mx, my) or "Ø Geral / A definir"
                
                pipe_key = f"{layer} - {d_found}"
                diam_pipe_lengths[pipe_key] = diam_pipe_lengths.get(pipe_key, 0.0) + length

                if length > min_seg_thresh:
                    linear_segments.append({
                        "layer": layer,
                        "diametro": d_found,
                        "comprimento_m": round(length / divisor, 2),
                        "comprimento_cad_original": round(length, 1),
                        "inicio": (round(line.dxf.start.x, 1), round(line.dxf.start.y, 1)),
                        "fim": (round(line.dxf.end.x, 1), round(line.dxf.end.y, 1))
                    })

        for pline in msp.query("LWPOLYLINE"):
            pts = pline.get_points(format="xy")
            if len(pts) > 1:
                plen = sum(((pts[i][0]-pts[i-1][0])**2 + (pts[i][1]-pts[i-1][1])**2)**0.5 for i in range(1, len(pts)))
                layer = pline.dxf.layer
                layer_lengths[layer] = layer_lengths.get(layer, 0.0) + plen
                disc = identify_element_discipline(layer)

                if disc != "GERAL" and plen > min_pipe_thresh:
                    summary["disciplinas_detectadas"].add(disc)
                    mx, my = pts[0][0], pts[0][1]
                    d_found = find_nearest_diameter(mx, my) or "Ø Geral / A definir"
                    pipe_key = f"{layer} - {d_found}"
                    diam_pipe_lengths[pipe_key] = diam_pipe_lengths.get(pipe_key, 0.0) + plen

        for k, total_raw in diam_pipe_lengths.items():
            summary["tubulacoes_por_diametro"][k] = round(total_raw / divisor, 2)

        for l, total_raw in layer_lengths.items():
            disc = identify_element_discipline(l)
            if disc != "GERAL" and total_raw > min_pipe_thresh:
                summary["metragens_lineares_metros"][l] = round(total_raw / divisor, 2)

        summary["topologia_redes"] = sorted(linear_segments, key=lambda x: x["comprimento_m"], reverse=True)[:35]

        # 3. CONEXÕES E ACOPLAMENTOS
        conn_counts = Counter()
        general_blocks = Counter()

        for ins in msp.query("INSERT"):
            layer = ins.dxf.layer
            bname = clean_block_name(ins.dxf.name)
            disc = identify_element_discipline(layer + " " + bname)

            if disc != "GERAL":
                summary["disciplinas_detectadas"].add(disc)
                loc = ins.dxf.insert
                
                d_val = extract_diameter_from_string(bname)
                if not d_val:
                    d_val = find_nearest_diameter(loc.x, loc.y, max_dist=2500)

                cat = classify_component_category(layer, bname)
                d_suffix = f" {d_val}" if d_val else ""

                if not is_anonymous_block(bname):
                    desc_item = f"{bname}{d_suffix}"
                else:
                    near_tag = resolve_nearest_tag(loc.x, loc.y)
                    tag_str = f" (Tag: {near_tag})" if near_tag else ""
                    desc_item = f"{cat}{d_suffix}{tag_str}"

                if any(k in cat for k in ["Conexão", "Acoplamento", "Flange"]):
                    conn_counts[f"[{layer}] {desc_item}"] += 1
                else:
                    general_blocks[f"[{layer}] {desc_item}"] += 1

        summary["conexoes_e_acoplamentos"] = dict(conn_counts)
        combined_pts = dict(conn_counts)
        combined_pts.update(general_blocks)
        summary["elementos_pontuais"] = combined_pts

        # 4. COTAS DO DESENHO (CONFERIDAS COM DIVISOR)
        dimensions = []
        for d in msp.query("DIMENSION"):
            val = getattr(d.dxf, "actual_measurement", None)
            txt = getattr(d.dxf, "text", "")
            if val and 10 < val < 5000000:
                dim_str = f"{val/divisor:.2f} m (CAD: {val:.1f})"
                if txt: dim_str += f" [Texto: {txt}]"
                if dim_str not in dimensions: dimensions.append(dim_str)

        summary["cotas_principais_metros"] = dimensions[:25]
        summary["tags_identificadores"] = sorted(list(tags))[:40]
        summary["especificacoes_tecnicas"] = sorted(list(specs))

        notes = []
        for layout in doc.layouts:
            for ent in layout:
                if ent.dxftype() in ("TEXT", "MTEXT"):
                    raw = ent.dxf.text if ent.dxftype() == "TEXT" else ent.text
                    txt = clean_cad_text(raw)
                    if len(txt) > 20:
                        upper = txt.upper()
                        if any(w in upper for w in [
                            "NORMA", "TESTE", "PRESSÃO", "NFPA", "NBR", "NOTA",
                            "PROCEDIMENTO", "REFERENCIA", "MATERIAL", "ESPECIFICAÇÃO"
                        ]):
                            if txt not in notes: notes.append(txt)

        summary["notas_de_prancha"] = notes[:25]
        summary["disciplinas_detectadas"] = list(summary["disciplinas_detectadas"])

        return summary
    finally:
        if temp_created and os.path.exists(dxf_path):
            try:
                os.remove(dxf_path)
            except:
                pass

def format_ai_prompt(data: dict) -> str:
    p = []
    p.append("="*80)
    p.append("AUDITORIA TÉCNICA MULTIDISCIPLINAR DE PROJETO (IA READY)")
    p.append(f"Arquivo: {data['arquivo']}")
    disc_str = ", ".join(data['disciplinas_detectadas']) if data['disciplinas_detectadas'] else "Geral / Engenharia"
    p.append(f"Disciplinas: {disc_str}")
    esc = data.get("auditoria_escala", {})
    p.append(f"Calibração de Escala: {esc.get('unidade_detectada', 'N/D')} (Divisor {esc.get('divisor_aplicado', 1000)})")
    p.append("="*80)

    p.append("\n[AVERIGUAÇÃO DA ESCALA E MEDIDAS DO PROJETO]:")
    p.append(f"  ℹ️  {esc.get('explicacao_tecnica', '')}")
    if esc.get("cotas_amostra_para_conferencia"):
        p.append("  Amostra de Cotas para conferir no AutoCAD (Comando DIST):")
        for s in esc["cotas_amostra_para_conferencia"][:4]:
            val_m = s['valor_cad'] / esc.get('divisor_aplicado', 1000)
            p.append(f"   • Cota na Layer [{s['layer']}]: {s['valor_cad']:.1f} unidades no CAD  =  {val_m:.2f} metros no mundo real")

    if data["inconsistencias"]:
        p.append("\n[ALERTAS DE INCONSISTÊNCIAS]:")
        for inc in data["inconsistencias"]:
            p.append(f"  ⚠️  [{inc['gravidade']} - {inc['tipo']}]: {inc['mensagem']}")

    p.append("\n1. TUBULAÇÕES E REDES SEGREGADAS POR DIÂMETRO (METRAGEM AUDITADA):")
    if data.get("tubulacoes_por_diametro"):
        for pipe, met in sorted(data["tubulacoes_por_diametro"].items(), key=lambda x: x[1], reverse=True):
            p.append(f"  • {pipe}: {met:.2f} metros")

    p.append("\n2. CONEXÕES, ACOPLAMENTOS E PEÇAS POR DIÂMETRO (QUANTITATIVO FÍSICO):")
    if data.get("conexoes_e_acoplamentos"):
        for conn, qtd in sorted(data["conexoes_e_acoplamentos"].items(), key=lambda x: x[1], reverse=True):
            p.append(f"  • {conn}: {qtd} un")

    p.append("\n" + "="*80)
    p.append("[COMO AVERIGUAR QUALQUER TRECHO NO SEU AUTOCAD]:")
    p.append("• Use o comando DIST digitando as coordenadas X,Y indicadas na aba Topologia para auditar o comprimento exato.")
    p.append("="*80)

    return "\n".join(p)

def format_topology_text(data: dict) -> str:
    t = []
    t.append("="*80)
    t.append(f"MAPA TOPOLÓGICO & COORDENADAS REAIS PARA AUDITORIA (CAD-AS-CODE)")
    t.append(f"Arquivo: {data['arquivo']}")
    esc = data.get("auditoria_escala", {})
    t.append(f"Escala Aplicada: {esc.get('unidade_detectada')} (1 metro = {esc.get('divisor_aplicado')} unidades CAD)")
    t.append("="*80)
    t.append("\nTRECHOS AUDITADOS COM COORDENADAS PARA CONFERÊNCIA NO AUTOCAD (COMANDO 'DIST'):")

    if data.get("topologia_redes"):
        for i, seg in enumerate(data["topologia_redes"], 1):
            p_ini = f"({seg['inicio'][0]}, {seg['inicio'][1]})"
            p_fim = f"({seg['fim'][0]}, {seg['fim'][1]})"
            diam_txt = f" | Diâmetro: {seg.get('diametro', 'N/D')}"
            t.append(f"  [TRECHO #{i:02d}] Rede: {seg['layer']}{diam_txt}")
            t.append(f"    Comprimento Auditado: {seg['comprimento_m']} metros  (Geometria CAD: {seg.get('comprimento_cad_original')} unidades)")
            t.append(f"    Coordenadas: Início em {p_ini}  ──▶  Fim em {p_fim}")
    t.append("\n" + "="*80)
    return "\n".join(t)

def main():
    if len(sys.argv) < 2:
        print("Uso: python cad_to_ai.py <arquivo.dwg_ou_dxf> [--json] [--topology]")
        sys.exit(1)

    file_path = sys.argv[1]
    as_json = "--json" in sys.argv
    as_topology = "--topology" in sys.argv

    if not os.path.exists(file_path):
        print(f"Erro: Arquivo '{file_path}' não encontrado.", file=sys.stderr)
        sys.exit(1)

    try:
        data = extract_project_summary(file_path)
        if as_json:
            print(json.dumps(data, indent=2, ensure_ascii=False))
        elif as_topology:
            print(format_topology_text(data))
        else:
            print(format_ai_prompt(data))
    except Exception as e:
        print(f"Erro ao processar: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
