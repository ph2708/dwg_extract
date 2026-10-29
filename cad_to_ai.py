#!/usr/bin/env python3
"""
Extrator e Auditor Universal Multidisciplinar de Projetos CAD (DWG / DXF)
Com Segregação de Diâmetros, Conexões, Acoplamentos e Válvulas
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
        # Normalizar para padrão ØX"
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

def extract_project_summary(file_path: str) -> dict:
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

        summary = {
            "arquivo": path.name,
            "extensao": path.suffix.lower(),
            "disciplinas_detectadas": set(),
            "inconsistencias": [],
            "tubulacoes_por_diametro": {},     # { "Ø14"": 184.5, "Ø10"": 104.2, ... }
            "conexoes_e_acoplamentos": {},     # { "Curva 90° Ø14"": 16, "Acoplamento Ø10"": 8, ... }
            "elementos_pontuais": {},
            "metragens_lineares_metros": {},
            "tags_identificadores": [],
            "especificacoes_tecnicas": [],
            "notas_de_prancha": [],
            "topologia_redes": [],
            "cotas_principais_metros": []
        }

        # 1. MAPEAMENTO DE TEXTOS E DIÂMETROS ESPACIAIS
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
                    # Extrair diâmetro se houver
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

        # 2. TUBULAÇÕES: SEGREGAÇÃO POR DIÂMETRO (METRAGEM LINEAR)
        layer_lengths = {}
        diam_pipe_lengths = {}
        linear_segments = []

        for line in msp.query("LINE"):
            length = (line.dxf.start - line.dxf.end).magnitude
            layer = line.dxf.layer
            layer_lengths[layer] = layer_lengths.get(layer, 0.0) + length
            disc = identify_element_discipline(layer)

            if disc != "GERAL" and length > 400: # acima de 40cm
                summary["disciplinas_detectadas"].add(disc)
                mx = (line.dxf.start.x + line.dxf.end.x) / 2
                my = (line.dxf.start.y + line.dxf.end.y) / 2
                d_found = find_nearest_diameter(mx, my) or "Ø Geral / A definir"
                
                # Agrupar tubulação por [Layer] + [Diâmetro]
                pipe_key = f"{layer} - {d_found}"
                diam_pipe_lengths[pipe_key] = diam_pipe_lengths.get(pipe_key, 0.0) + length

                if length > 800:
                    linear_segments.append({
                        "layer": layer,
                        "diametro": d_found,
                        "comprimento_m": round(length / 1000.0, 2),
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

                if disc != "GERAL" and plen > 400:
                    summary["disciplinas_detectadas"].add(disc)
                    mx, my = pts[0][0], pts[0][1]
                    d_found = find_nearest_diameter(mx, my) or "Ø Geral / A definir"
                    pipe_key = f"{layer} - {d_found}"
                    diam_pipe_lengths[pipe_key] = diam_pipe_lengths.get(pipe_key, 0.0) + plen

        # Salvar tubulações segregadas por diâmetro (em metros)
        for k, total_mm in diam_pipe_lengths.items():
            summary["tubulacoes_por_diametro"][k] = round(total_mm / 1000.0, 2)

        for l, total_mm in layer_lengths.items():
            disc = identify_element_discipline(l)
            if disc != "GERAL" and total_mm > 400:
                summary["metragens_lineares_metros"][l] = round(total_mm / 1000.0, 2)

        summary["topologia_redes"] = sorted(linear_segments, key=lambda x: x["comprimento_m"], reverse=True)[:35]

        # 3. CONEXÕES, ACOPLAMENTOS E PEÇAS (COM DIÂMETROS)
        conn_counts = Counter()
        general_blocks = Counter()

        for ins in msp.query("INSERT"):
            layer = ins.dxf.layer
            bname = clean_block_name(ins.dxf.name)
            disc = identify_element_discipline(layer + " " + bname)

            if disc != "GERAL":
                summary["disciplinas_detectadas"].add(disc)
                loc = ins.dxf.insert
                
                # 1. Tentar diâmetro explícito no nome do bloco (ex: CURVAS 90 - 14)
                d_val = extract_diameter_from_string(bname)
                # 2. Se não houver, buscar diâmetro próximo no desenho
                if not d_val:
                    d_val = find_nearest_diameter(loc.x, loc.y, max_dist=2500)

                cat = classify_component_category(layer, bname)
                d_suffix = f" {d_val}" if d_val else ""

                if not is_anonymous_block(bname):
                    # Bloco nomeado
                    desc_item = f"{bname}{d_suffix}"
                else:
                    # Bloco anônimo: rotular com sua função e tag
                    near_tag = resolve_nearest_tag(loc.x, loc.y)
                    tag_str = f" (Tag: {near_tag})" if near_tag else ""
                    desc_item = f"{cat}{d_suffix}{tag_str}"

                # Separar conexões e acoplamentos específicos
                if any(k in cat for k in ["Conexão", "Acoplamento", "Flange"]):
                    conn_counts[f"[{layer}] {desc_item}"] += 1
                else:
                    general_blocks[f"[{layer}] {desc_item}"] += 1

        summary["conexoes_e_acoplamentos"] = dict(conn_counts)
        # Combinar tudo em elementos pontuais mantendo ordenação
        combined_pts = dict(conn_counts)
        combined_pts.update(general_blocks)
        summary["elementos_pontuais"] = combined_pts

        # 4. COTAS E NOTAS DE PRANCHA
        dimensions = []
        for d in msp.query("DIMENSION"):
            val = getattr(d.dxf, "actual_measurement", None)
            txt = getattr(d.dxf, "text", "")
            if val and 100 < val < 500000:
                dim_str = f"{val/1000.0:.2f} m"
                if txt: dim_str += f" ({txt})"
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
    p.append("="*80)

    p.append("\n[DIAGNÓSTICO AUTOMÁTICO DE INCONSISTÊNCIAS DO CAD]:")
    if data["inconsistencias"]:
        for inc in data["inconsistencias"]:
            p.append(f"  ⚠️  [{inc['gravidade']} - {inc['tipo']}]: {inc['mensagem']}")
    else:
        p.append("  ✅ Nenhuma anomalia crítica de desenho detectada.")

    p.append("\n1. TUBULAÇÕES E REDES SEGREGADAS POR DIÂMETRO (METRAGEM EXATA):")
    if data.get("tubulacoes_por_diametro"):
        for pipe, met in sorted(data["tubulacoes_por_diametro"].items(), key=lambda x: x[1], reverse=True):
            p.append(f"  • {pipe}: {met:.2f} metros")
    else:
        p.append("  (Sem metragens lineares identificadas)")

    p.append("\n2. CONEXÕES, ACOPLAMENTOS E PEÇAS POR DIÂMETRO (QUANTITATIVO FÍSICO):")
    if data.get("conexoes_e_acoplamentos"):
        for conn, qtd in sorted(data["conexoes_e_acoplamentos"].items(), key=lambda x: x[1], reverse=True):
            p.append(f"  • {conn}: {qtd} un")
    else:
        p.append("  (Sem conexões identificadas)")

    p.append("\n3. DEMAIS EQUIPAMENTOS E ELEMENTOS PONTUAIS:")
    other_items = {k: v for k, v in data["elementos_pontuais"].items() if k not in data.get("conexoes_e_acoplamentos", {})}
    for it, qtd in sorted(other_items.items(), key=lambda x: x[1], reverse=True)[:25]:
        p.append(f"  • {it}: {qtd} un")

    if data["notas_de_prancha"]:
        p.append("\n4. NOTAS GERAIS E NORMAS DE PROJETO:")
        for n in data["notas_de_prancha"]:
            p.append(f"  [NOTA]: {n}")

    p.append("\n" + "="*80)
    p.append("[PERGUNTAS RECOMENDADAS PARA A IA]:")
    p.append("• 'Para cada diâmetro de tubulação listado, qual a quantidade recomendada de juntas de vedação e parafusos estojos?'")
    p.append("• 'Identifique se faltam reduções entre os trechos de diferentes diâmetros indicados.'")
    p.append("="*80)

    return "\n".join(p)

def format_topology_text(data: dict) -> str:
    t = []
    t.append("="*80)
    t.append(f"MAPA INCREMENTAL & TOPOLOGIA COMPLETA DO PROJETO (CAD-AS-CODE)")
    t.append(f"Arquivo: {data['arquivo']}")
    t.append("="*80)
    t.append("\n1. TOPOLOGIA INCREMENTAL DOS SEGMENTOS (COM DIÂMETROS E EXTENSÕES):")
    if data.get("topologia_redes"):
        for i, seg in enumerate(data["topologia_redes"], 1):
            p_ini = f"({seg['inicio'][0]}, {seg['inicio'][1]})"
            p_fim = f"({seg['fim'][0]}, {seg['fim'][1]})"
            diam_txt = f" | Diâmetro: {seg.get('diametro', 'N/D')}"
            t.append(f"  [TRECHO #{i:02d}] Rede: {seg['layer']}{diam_txt} | Extensão: {seg['comprimento_m']} m")
            t.append(f"    Traçado: {p_ini}  ──▶  {p_fim}")
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
