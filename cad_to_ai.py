#!/usr/bin/env python3
"""
Extrator e Auditor Universal Multidisciplinar de Projetos CAD (DWG / DXF)
Com Limpeza de Ruídos, Desduplicação de Vistas (Planta vs Corte) e Consolidação de Materiais
"""

import os
import sys
import json
import re
import subprocess
from pathlib import Path
from collections import Counter
import ezdxf

# Camadas que NUNCA são tubulações físicas ou materiais de compra
IGNORED_LAYERS = {
    "QUADRO", "LIMITE DE DESENHO", "MARGEM", "SELO", "FORMATO", 
    "INDICAÇÃO DE CORTE", "INDICACAO DE CORTE", "INDICAÇÃO DE SUPORTE",
    "INDICACAO DE SUPORTE", "DEFPOINTS", "VIEWPORT", "HATCH", "EIXO", "EIXOS", 
    "COTAS", "COTAS SHW", "PP_COTAS", "TEXTO", "TEXTOS", "TXT_GERAL", "TXT_EIXOS",
    "IND_EIXOS", "NIVEL", "NIVEIS", "CIVIL", "CONCRETO", "BASE_CONCRETO", "PROJEÇÃO", "PROJECAO",
    "ESCADA", "GRADE", "VALA_TUBULACAO", "VALA", "MEC-PIPE-RACK", "06_HACHURA", "ALVENARIA",
    "AQ-ALVENARIA", "TYCO-ALVENARIA", "AQ-ESQUADRIAS", "CAIXILHOS", "BASE", "0",
    "CALCULO HIDRAULICO", "DETALHE", "INDIC CORTE", "33-FIRE_PUMP_FUEL"
}

# Camadas que representam conexões, válvulas ou equipamentos (NUNCA somar como metragem linear de tubo)
NON_PIPE_COMPONENT_LAYERS = {
    "CONEXÃO", "CONEXAO", "CONEXÃO MECÃNICA", "CONEXAO MECANICA", "WS-MV CONEX",
    "VALVULA", "VÁLVULA", "VALVULA RO", "DIVISAO_DE_VALVULAS",
    "FLANGE", "FLANGES", "JUNTA", "ACOPLAMENTO",
    "TANQUE", "TANQUE DIESEL", "SUPORTE", "INDICAÇÃO DE SUPORTE", "INSTRUMENT"
}

# Palavras que confirmam que a camada é uma rede de tubulação real
TRUE_PIPE_KEYWORDS = [
    "TUBULAÇÃO", "TUBULACAO", "TUBO", "TUBUL", "PIPE", "CANO", "REDE"
]

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

def canonical_pipe_network(layer: str) -> str:
    """Padroniza o nome comercial da tubulação para o relatório e planilha."""
    lu = layer.upper()
    if "COBRE" in lu:
        return "Tubulação de Cobre"
    if "DIESEL" in lu or "OLEO" in lu or "COMBUSTIVEL" in lu:
        return "Tubulação de Óleo Diesel"
    if "SPRINKLER" in lu or "SPK" in lu:
        return "Tubulação de Sprinklers"
    if any(k in lu for k in ["INCÊNDIO", "INCENDIO", "TUBO-INC", "TUBUL INC"]):
        return "Tubulação de Incêndio (Aço Carbono)"
    return f"Tubulação ({layer})"

def canonical_fitting_name(layer: str, name: str, diam: str) -> tuple[str, str, str]:
    """
    Consolida nomes de conexões, suportes, válvulas e equipamentos,
    eliminando variações de vistas (frontal, lateral, traseira) e hashes anônimos.
    Retorna: (categoria_principal, descricao_comercial, diametro_nominal)
    """
    combined = (layer + " " + name).upper()
    d_val = diam or ""

    # 1. Suportes de Tubulação
    if "SUPORTE" in combined or name.lower().startswith("sup"):
        d = d_val or ("Ø14\"" if "14" in combined else ("Ø4\"" if "4" in combined else ("Ø2\"" if "2" in combined else "-")))
        return "Suporte / Fixação", "Suporte Metálico de Tubulação (Tipo Hanger / Abraçadeira)", d

    # 2. Curvas
    if any(k in combined for k in ["CURVAS 90", "CURVA 90", "CURVA 14X90", "COTOVELO 90"]):
        d = d_val or ("Ø14\"" if "14" in combined else ("Ø10\"" if "10" in combined else ("Ø4\"" if "4" in combined else ("Ø2\"" if "2" in combined else ""))))
        d_label = f" {d}" if d else ""
        return "Conexão - Curva", f"Curva 90° Raio Longo{d_label}", d or "-"

    if any(k in combined for k in ["CURVAS 45", "CURVA 45", "COTOVELO 45"]):
        d_label = f" {d_val}" if d_val else ""
        return "Conexão - Curva", f"Curva 45°{d_label}", d_val or "-"

    # 3. Tês
    if any(k in combined for k in ["TE -", "TE ", "TEE", "TÊ"]):
        d = d_val or ("Ø10\"" if "10" in combined else ("Ø2.1/2\"" if "2-1-2" in combined else ("Ø2\"" if "2" in combined else "")))
        d_label = f" {d}" if d else ""
        return "Conexão - Tê", f"Conexão Tê Reto Industrial{d_label}", d or "-"

    # 4. Flanges
    if any(k in combined for k in ["FLANGE", "CONTRAFLANGE", "ADAPTADOR"]):
        d_label = f" {d_val}" if d_val else ""
        return "Flange / Adaptador", f"Flange com Ressalto ANSI B16.5{d_label}", d_val or "-"

    # 5. Juntas de Expansão
    if "JUNTA" in combined:
        d_label = f" {d_val}" if d_val else ""
        return "Junta / Flexível", f"Junta de Expansão Flexível com Tensores{d_label}", d_val or "-"

    # 6. Acoplamentos Ranhurados
    if any(k in combined for k in ["ACOPLAMENTO", "COUPLING", "GROOVED"]):
        d_label = f" {d_val}" if d_val else ""
        return "Acoplamento Ranhurado", f"Acoplamento Ranhurado (Grooved){d_label}", d_val or "-"

    # 7. Reduções
    if any(k in combined for k in ["REDUÇÃO", "REDUCAO", "REDUCER"]):
        d_label = f" {d_val}" if d_val else ""
        return "Conexão - Redução", f"Redução Concêntrica/Excêntrica{d_label}", d_val or "-"

    # 8. Válvulas
    if any(k in combined for k in ["VALVULA", "VÁLVULA", "RETENÇÃO", "RETENCAO", "GAVETA", "BORBOLETA", "ESFERA", "ALÍVIO", "ALIVIO"]):
        if "RETENÇÃO" in combined or "RETENCAO" in combined:
            d = d_val or ("Ø14\"" if "14" in combined else "")
            d_label = f" {d}" if d else ""
            return "Válvula / Registro", f"Válvula de Retenção Dupla Portinhola{d_label}", d or "-"
        elif "ALÍVIO" in combined or "ALIVIO" in combined or "SEGURANÇA" in combined:
            d = d_val or ("Ø14\"" if "14" in combined else ("Ø8\"" if "8" in combined else ""))
            d_label = f" {d}" if d else ""
            return "Válvula / Registro", f"Válvula de Alívio e Segurança Angular 90°{d_label}", d or "-"
        elif "GAVETA" in combined:
            d_label = f" {d_val}" if d_val else ""
            return "Válvula / Registro", f"Válvula Gaveta OS&Y Flangeada{d_label}", d_val or "-"
        elif "BORBOLETA" in combined:
            d_label = f" {d_val}" if d_val else ""
            return "Válvula / Registro", f"Válvula Borboleta com Redutor{d_label}", d_val or "-"
        elif "ESFERA" in combined:
            d_label = f" {d_val}" if d_val else ""
            return "Válvula / Registro", f"Válvula Esfera Industrial{d_label}", d_val or "-"
        d_label = f" {d_val}" if d_val else ""
        return "Válvula / Registro", f"Válvula Industrial{d_label}", d_val or "-"

    # 9. Bombas e Equipamentos
    if any(k in combined for k in ["BOMBA", "PUMP", "MOTOR"]):
        if "JOCKEY" in combined:
            return "Equipamento / Bomba", "Conjunto Moto-Bomba Jockey", "-"
        return "Equipamento / Bomba", "Conjunto Moto-Bomba Principal 5000 GPM", "-"

    if any(k in combined for k in ["SPRINKLER", "SPK"]):
        d_label = f" {d_val}" if d_val else ""
        return "Sprinkler", f"Bico de Sprinkler Industrial{d_label}", d_val or "-"

    if "HIDRANTE" in combined:
        return "Hidrante", "Válvula / Ponto de Hidrante", d_val or "-"

    # 10. Conexões anônimas na layer CONEXÃO
    if any(k in combined for k in ["CONEXÃO", "CONEXAO"]):
        d_label = f" {d_val}" if d_val else ""
        return "Conexão / Acoplamento", f"Conexão Hidráulica Ranhurada / Soldada{d_label}", d_val or "-"

    # Blocos anônimos sem classificação prévia
    if is_anonymous_block(name):
        d_label = f" {d_val}" if d_val else ""
        return "Conexão / Acessório", f"Conexão Industrial ({layer}){d_label}", d_val or "-"

    d_label = f" {d_val}" if d_val else ""
    return "Acessório / Conexão", f"{name}{d_label}", d_val or "-"

def detect_cad_scale_factor(msp) -> tuple[float, str, str, list[dict]]:
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

def detect_drawing_views(msp) -> tuple[bool, dict]:
    """
    Detecta se o arquivo CAD possui múltiplas vistas desenhadas lado a lado (ex: PLANTA e CORTE).
    Quando presente, define o divisor espacial para auditar a PLANTA BAIXA e desduplicar os CORTES.
    """
    views = []
    for ent in msp:
        if ent.dxftype() in ("TEXT", "MTEXT"):
            raw = ent.dxf.text if ent.dxftype() == "TEXT" else ent.text
            loc = getattr(ent.dxf, "insert", None)
            txt = raw.strip().upper()
            if loc and any(txt == k or txt.startswith(k + " ") or txt.startswith(k + "-") for k in [
                "PLANTA", "PLANTA BAIXA", "LAYOUT", "CORTE", "ELEVAÇÃO", "ELEVACAO", "DETALHE", "SEÇÃO"
            ]):
                views.append((loc.x, loc.y, txt))

    has_planta = any("PLANTA" in v[2] or "LAYOUT" in v[2] for v in views)
    has_corte = any("CORTE" in v[2] or "ELEVA" in v[2] or "DETALHE" in v[2] for v in views)

    if has_planta and has_corte:
        planta_v = [v for v in views if "PLANTA" in v[2] or "LAYOUT" in v[2]][0]
        corte_v = [v for v in views if "CORTE" in v[2] or "ELEVA" in v[2] or "DETALHE" in v[2]][0]
        
        p_x, p_y = planta_v[0], planta_v[1]
        c_x, c_y = corte_v[0], corte_v[1]

        if abs(c_x - p_x) >= abs(c_y - p_y):
            split_x = (p_x + c_x) / 2
            is_master_fn = (lambda x, y: x < split_x) if p_x < c_x else (lambda x, y: x >= split_x)
            split_desc = f"Eixo X ({split_x:.0f})"
        else:
            split_y = (p_y + c_y) / 2
            is_master_fn = (lambda x, y: y < split_y) if p_y < c_y else (lambda x, y: y >= split_y)
            split_desc = f"Eixo Y ({split_y:.0f})"

        return True, {
            "possui_planta_e_corte": True,
            "vistas": ["Planta Baixa (Área Master)", "Cortes / Detalhamentos (Vistas Laterais)"],
            "split_desc": split_desc,
            "is_master_fn": is_master_fn,
            "mensagem": "Projeto com PLANTA BAIXA e CORTES identificado. A auditoria utilizou a Planta Baixa como base de quantitativos para evitar duplicações dos componentes desenhados nas vistas de corte."
        }

    # Desenho único sem cortes separados
    return False, {
        "possui_planta_e_corte": False,
        "vistas": ["Área Integral"],
        "is_master_fn": (lambda x, y: True),
        "mensagem": "Desenho auditado em área integral."
    }

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

        has_multi_views, view_info = detect_drawing_views(msp)
        is_master_fn = view_info["is_master_fn"]

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
            "auditoria_vistas": {
                "possui_planta_e_corte": has_multi_views,
                "vistas_detectadas": view_info["vistas"],
                "mensagem": view_info["mensagem"]
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

        # 1. MAPEAMENTO DE TEXTOS E DIÂMETROS
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
                if loc and is_master_fn(loc.x, loc.y):
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

        def find_nearest_diameter(x, y, max_dist=4000) -> str:
            best_dist = max_dist
            best_diam = ""
            for dx, dy, dval in diam_points:
                dist = ((dx-x)**2 + (dy-y)**2)**0.5
                if dist < best_dist:
                    best_dist = dist
                    best_diam = dval
            return best_diam

        # 2. FILTRAGEM RIGOROSA DE TUBULAÇÃO REAL (IGNORA QUADRO, LIMITES, CONEXÕES E CORTES)
        layer_lengths = {}
        diam_pipe_lengths = {}
        linear_segments = []
        unique_segments = set() # Desduplicação de sobreposições

        min_pipe_thresh = 300.0 * (divisor / 1000.0)
        min_seg_thresh = 600.0 * (divisor / 1000.0)

        for line in msp.query("LINE"):
            layer = line.dxf.layer
            layer_u = layer.upper()

            # Filtra camadas ignoradas
            if layer_u in IGNORED_LAYERS or any(k in layer_u for k in NON_PIPE_COMPONENT_LAYERS):
                continue

            # Confirma que é camada de tubulação
            if not any(k in layer_u for k in TRUE_PIPE_KEYWORDS):
                continue

            disc = identify_element_discipline(layer)
            if disc == "GERAL":
                continue

            mx = (line.dxf.start.x + line.dxf.end.x) / 2
            my = (line.dxf.start.y + line.dxf.end.y) / 2

            # Se for projeto com cortes, processa apenas a vista master (Planta Baixa)
            if not is_master_fn(mx, my):
                continue

            # Desduplicação de linhas sobrepostas
            p1 = (round(line.dxf.start.x, 1), round(line.dxf.start.y, 1))
            p2 = (round(line.dxf.end.x, 1), round(line.dxf.end.y, 1))
            seg_key = tuple(sorted([p1, p2]))
            if seg_key in unique_segments:
                continue
            unique_segments.add(seg_key)

            length = (line.dxf.start - line.dxf.end).magnitude
            if length > min_pipe_thresh:
                summary["disciplinas_detectadas"].add(disc)
                layer_lengths[layer] = layer_lengths.get(layer, 0.0) + length
                
                d_found = find_nearest_diameter(mx, my) or "Ø A definir"
                canon_net = canonical_pipe_network(layer)

                pipe_key = f"{canon_net} - {d_found}"
                diam_pipe_lengths[pipe_key] = diam_pipe_lengths.get(pipe_key, 0.0) + length

                if length > min_seg_thresh:
                    linear_segments.append({
                        "layer": layer,
                        "rede": canon_net,
                        "diametro": d_found,
                        "comprimento_m": round(length / divisor, 2),
                        "comprimento_cad_original": round(length, 1),
                        "inicio": p1,
                        "fim": p2
                    })

        for pline in msp.query("LWPOLYLINE"):
            layer = pline.dxf.layer
            layer_u = layer.upper()
            if layer_u in IGNORED_LAYERS or any(k in layer_u for k in NON_PIPE_COMPONENT_LAYERS):
                continue
            if not any(k in layer_u for k in TRUE_PIPE_KEYWORDS):
                continue

            disc = identify_element_discipline(layer)
            if disc == "GERAL":
                continue

            pts = pline.get_points(format="xy")
            if len(pts) > 1:
                mx, my = pts[0][0], pts[0][1]
                if not is_master_fn(mx, my):
                    continue

                plen = sum(((pts[i][0]-pts[i-1][0])**2 + (pts[i][1]-pts[i-1][1])**2)**0.5 for i in range(1, len(pts)))
                if plen > min_pipe_thresh:
                    summary["disciplinas_detectadas"].add(disc)
                    layer_lengths[layer] = layer_lengths.get(layer, 0.0) + plen
                    d_found = find_nearest_diameter(mx, my) or "Ø A definir"
                    canon_net = canonical_pipe_network(layer)
                    pipe_key = f"{canon_net} - {d_found}"
                    diam_pipe_lengths[pipe_key] = diam_pipe_lengths.get(pipe_key, 0.0) + plen

        for k, total_raw in diam_pipe_lengths.items():
            summary["tubulacoes_por_diametro"][k] = round(total_raw / divisor, 2)

        for l, total_raw in layer_lengths.items():
            summary["metragens_lineares_metros"][l] = round(total_raw / divisor, 2)

        summary["topologia_redes"] = sorted(linear_segments, key=lambda x: x["comprimento_m"], reverse=True)[:35]

        # 3. CONSOLIDAÇÃO INTELIGENTE DE CONEXÕES E COMPONENTES (SEM DUPLICATAS DE CORTES)
        conn_consolidated = Counter()
        equipment_consolidated = Counter()

        for ins in msp.query("INSERT"):
            layer = ins.dxf.layer
            layer_u = layer.upper()
            bname = clean_block_name(ins.dxf.name)

            # Se for layer 0, aceita apenas blocos com nomes de equipamentos reais
            if layer_u == "0":
                if is_anonymous_block(bname):
                    continue
                if not any(k in bname.upper() for k in ["BOMBA", "VALV", "VÁLV", "CURVA", "TE", "FLANGE", "ADAPTADOR", "ACOPLAMENTO", "MOTOR", "PAINEL", "SUPORTE"]):
                    continue
            elif layer_u in IGNORED_LAYERS or any(k in layer_u for k in ("CORTE", "SELO", "MARGEM", "FORMATO", "COTAS", "NIVEL", "EIXO")):
                continue

            loc = ins.dxf.insert
            # Desduplica componentes desenhados nas vistas de corte
            if not is_master_fn(loc.x, loc.y):
                continue

            disc = identify_element_discipline(layer + " " + bname)

            if disc != "GERAL":
                summary["disciplinas_detectadas"].add(disc)
                
                d_val = extract_diameter_from_string(bname)
                if not d_val:
                    d_val = find_nearest_diameter(loc.x, loc.y, max_dist=2500)

                cat, desc_item, diam_nom = canonical_fitting_name(layer, bname, d_val)

                item_key = f"[{cat}] {desc_item}"
                if any(k in cat for k in ["Conexão", "Acoplamento", "Flange", "Junta"]):
                    conn_consolidated[item_key] += 1
                else:
                    equipment_consolidated[item_key] += 1

        summary["conexoes_e_acoplamentos"] = dict(conn_consolidated)
        combined_pts = dict(conn_consolidated)
        combined_pts.update(equipment_consolidated)
        summary["elementos_pontuais"] = combined_pts

        # 4. COTAS E NOTAS DE PRANCHA
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
    
    vistas = data.get("auditoria_vistas", {})
    if vistas.get("possui_planta_e_corte"):
        p.append("Desduplicação de Vistas: ATIVADA (Planta Baixa auditada para evitar duplicações dos Cortes)")
    p.append("="*80)

    p.append("\n[AVERIGUAÇÃO DA ESCALA E MEDIDAS DO PROJETO]:")
    p.append(f"  ℹ️  {esc.get('explicacao_tecnica', '')}")
    if esc.get("cotas_amostra_para_conferencia"):
        p.append("  Amostra de Cotas no Arquivo para conferência no CAD (Comando DIST):")
        for s in esc["cotas_amostra_para_conferencia"][:4]:
            val_m = s['valor_cad'] / esc.get('divisor_aplicado', 1000)
            p.append(f"   • Cota Layer [{s['layer']}]: {s['valor_cad']:.1f} unidades CAD = {val_m:.2f} metros reais")

    if vistas.get("possui_planta_e_corte"):
        p.append("\n[DESDUPLICAÇÃO DE VISTAS (PLANTA vs CORTE)]:")
        p.append(f"  ℹ️  {vistas.get('mensagem')}")

    if data["inconsistencias"]:
        p.append("\n[ALERTAS DE INCONSISTÊNCIAS]:")
        for inc in data["inconsistencias"]:
            p.append(f"  ⚠️  [{inc['gravidade']} - {inc['tipo']}]: {inc['mensagem']}")

    p.append("\n1. TUBULAÇÕES REAIS POR DIÂMETRO (METRAGEM AUDITADA):")
    if data.get("tubulacoes_por_diametro"):
        for pipe, met in sorted(data["tubulacoes_por_diametro"].items(), key=lambda x: x[1], reverse=True):
            p.append(f"  • {pipe}: {met:.2f} metros")

    p.append("\n2. CONEXÕES, FLANGES E ACOPLAMENTOS (CONSOLIDADOS SEM DUPLICATAS):")
    if data.get("conexoes_e_acoplamentos"):
        for conn, qtd in sorted(data["conexoes_e_acoplamentos"].items(), key=lambda x: x[1], reverse=True):
            p.append(f"  • {conn}: {qtd} un")

    p.append("\n3. VÁLVULAS, EQUIPAMENTOS E SUPORTES:")
    other_items = {k: v for k, v in data["elementos_pontuais"].items() if k not in data.get("conexoes_e_acoplamentos", {})}
    for it, qtd in sorted(other_items.items(), key=lambda x: x[1], reverse=True):
        p.append(f"  • {it}: {qtd} un")

    if data["notas_de_prancha"]:
        p.append("\n4. NOTAS GERAIS E NORMAS DE PROJETO:")
        for n in data["notas_de_prancha"]:
            p.append(f"  [NOTA]: {n}")

    p.append("\n" + "="*80)
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
            t.append(f"  [TRECHO #{i:02d}] {seg.get('rede', seg['layer'])}{diam_txt}")
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
