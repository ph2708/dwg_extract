#!/usr/bin/env python3
"""
Extrator e Auditor Universal Multidisciplinar de Projetos CAD (DWG / DXF)
Com Limpeza de Ruídos, Detecção Multi-Pavimentos (Térreo, Superior, Ático),
Desduplicação de Vistas (Planta vs Corte) e Extração de Elétrica e Hidráulica
"""

import os
import sys
import json
import re
import math
import subprocess
from pathlib import Path
from collections import Counter
import ezdxf

# Camadas que NUNCA são tubulações físicas, eletrodutos ou materiais de compra
IGNORED_LAYERS = {
    "QUADRO", "LIMITE DE DESENHO", "MARGEM", "SELO", "FORMATO", 
    "INDICAÇÃO DE CORTE", "INDICACAO DE CORTE", "INDICAÇÃO DE SUPORTE",
    "INDICACAO DE SUPORTE", "DEFPOINTS", "VIEWPORT", "HATCH", "EIXO", "EIXOS", 
    "COTAS", "COTAS SHW", "PP_COTAS", "TEXTO", "TEXTOS", "TXT_GERAL", "TXT_EIXOS",
    "IND_EIXOS", "NIVEL", "NIVEIS", "CIVIL", "CONCRETO", "BASE_CONCRETO", "PROJEÇÃO", "PROJECAO",
    "ESCADA", "GRADE", "VALA_TUBULACAO", "VALA", "MEC-PIPE-RACK", "06_HACHURA", "ALVENARIA",
    "AQ-ALVENARIA", "TYCO-ALVENARIA", "AQ-ESQUADRIAS", "CAIXILHOS", "BASE", "0",
    "CALCULO HIDRAULICO", "DETALHE", "INDIC CORTE", "33-FIRE_PUMP_FUEL",
    "E_FIACAO", "FIACAO", "FIAÇÃO", "TEXTO-02", "TEXT-2", "TEXT-1", "TEXT-02", "TEXT-4", "TEXTO-01",
    "AUXILIAR-01", "EL_AUXILIAR-01", "EL-AUXILIAR-01", "AUXILIAR-1", "AUXILIAR-2",
    "AR_MOBILIA", "AR_ESQUADRIA", "AR_ALVENARIA", "31A-PAREDE_01", "34A-PROJEÇÕES", "PROJEÇÕES"
}

# Camadas que representam conexões, válvulas, simbologias ou equipamentos (NUNCA somar como metragem linear de tubo)
NON_PIPE_COMPONENT_LAYERS = {
    "CONEXÃO", "CONEXAO", "CONEXÃO MECÃNICA", "CONEXAO MECANICA", "WS-MV CONEX",
    "VALVULA", "VÁLVULA", "VALVULA RO", "DIVISAO_DE_VALVULAS",
    "FLANGE", "FLANGES", "JUNTA", "ACOPLAMENTO",
    "TANQUE", "TANQUE DIESEL", "SUPORTE", "INDICAÇÃO DE SUPORTE", "INSTRUMENT",
    "E_SIMBOLOGIA", "EL_SIMBOL", "SIMB-ELET", "EL_SIMBOLOGIA", "SIMBOLOGIA-TV"
}

# Palavras que confirmam que a camada é uma rede linear física real (tubo ou eletroduto)
LINEAR_MATERIAL_KEYWORDS = [
    # Hidráulica / Incêndio / Mecânica
    "TUBULAÇÃO", "TUBULACAO", "TUBO", "TUBUL", "PIPE", "CANO", "REDE",
    "INCÊNDIO", "INCENDIO", "ESGOTO", "AGUA", "ÁGUA", "PLUVIAL", "DRENO", "DRENAGEM",
    "SPRINKLER", "SPK", "HIDRANTE", "DIESEL", "COBRE", "GAS", "GÁS",
    # Elétrica / Infraestrutura
    "ELETRODUTO", "CONDUITE", "CONDUTO", "E_ELET", "ELET_", "EL_CABO", "CABO PP",
    "ELETROCALHA", "PERFILADO", "LEITO"
]

DISCIPLINES_KEYWORDS = {
    "HIDRAULICA_TUBULACAO": [
        "TUBO", "TUBULACAO", "TUBULAÇÃO", "CANO", "PIPE", "INCÊNDIO", "INCENDIO",
        "DIESEL", "COBRE", "HIDRANTE", "SPRINKLER", "VALV", "VÁLVULA", "FLANGE",
        "ESGOTO", "AGUA", "ÁGUA", "PLUVIAL", "RETENÇÃO", "BOMBA", "CONEXÃO", "CONEXAO",
        "ACOPLAMENTO", "CURVA", "REDUÇÃO", "REDUCAO", "TE "
    ],
    "ELETRICA_AUTOMACAO_DETECCAO": [
        "ELETRODUTO", "CONDUITE", "CONDULETE", "CONDUTO", "E_ELET", "EL_CABO", "CABO",
        "FIACAO", "FIAÇÃO", "CIRCUITO", "QUADRO", "QGBT", "QDG", "QD", "DETECÇÃO", "DETECCAO",
        "DETECTOR", "ACIONADOR", "SIRENE", "ILUMINACAO", "ILUMINAÇÃO", "LUMINARIA", "LUMINÁRIA",
        "TOMADA", "LAÇO", "LEITO", "ELETROCALHA", "PERFILADO", "PAINEL", "INVERSOR", "TRANSFORMADOR",
        "TERRA", "SPDA", "BARRAMENTO", "E_SIMBOLOGIA", "EL_SIMBOL", "SIMB-ELET", "CAMPAINHA", "INTERRUPTOR"
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
    upper = text.upper()
    # Ignora explicitamente dimensões de caixas elétricas e alturas de montagem
    if any(k in upper for k in ["CAIXA", "CX ", "QUADRADA", "RETANGULAR", "OCTOGONAL", "H =", "H="]):
        return ""

    t = text.replace("%%C", "Ø").replace("%%c", "Ø").replace("∅", "Ø")
    # Limpa multiplicadores como 2x, 1x
    t = re.sub(r"\b\d+\s*[xX]\s*", "", t)
    # Remove caixas de passagem como 4x4, 4x2, 20x20
    t = re.sub(r"\d+\s*[xX]\s*\d+[\"\w]*", "", t)

    # 1. Busca por Ø seguido por diâmetro ou fração (ex: Ø3/4", Ø1.1/4", Ø14")
    m = re.search(r"Ø\s*([0-9]+(?:\.[0-9]+)?(?:\s*[-/]\s*[0-9]+(?:\.[0-9]+)?)?)\s*\"?", t)
    if m:
        num = m.group(1).replace(" ", "")
        return f"Ø{num}\"" if not num.endswith("\"") else f"Ø{num}"

    # 2. Busca frações explícitas com aspas como 3/4", 1.1/4", 2.1/2", 1"
    m2 = re.search(r"\b([0-9]+(?:\.[0-9]+)?(?:\s*/\s*[0-9]+)?)\s*\"", t)
    if m2:
        num = m2.group(1).replace(" ", "")
        return f"Ø{num}\""

    # 3. Formato DN como DN50, DN 100
    m3 = re.search(r"\bDN\s*([0-9]{2,3})\b", t, re.IGNORECASE)
    if m3:
        return f"DN{m3.group(1)}"

    return ""

def canonical_pipe_network(layer: str) -> str:
    """Padroniza o nome comercial da tubulação ou eletroduto para o relatório e planilha."""
    lu = layer.upper()
    
    # 1. Elétrica e Infraestrutura
    if "E_ELET_EMB" in lu or "ELET_EMB" in lu:
        return "Eletroduto Flexível Corrugado / Rígido Embutido (Laje/Parede)"
    if "E_ELET_PISO" in lu or "ELET_PISO" in lu:
        return "Eletroduto de Piso PEAD / PVC Rígido"
    if "ELETRODUTO" in lu or "CONDUITE" in lu:
        if "APARENTE" in lu:
            return "Eletroduto Aparente (Aço Galvanizado)"
        return "Eletroduto PVC Corrugado / Rígido"
    if "BARRAMENTO" in lu:
        return "Barramentos de Cobre de Distribuição"
    if "CABO PP" in lu or "CABO_PP" in lu:
        return "Cabo Multipolar Flexível PP"
    if "ELETROCALHA" in lu or "PERFILADO" in lu or "LEITO" in lu:
        return f"Infraestrutura Elétrica ({layer})"

    # 2. Hidráulica, Incêndio e Mecânica
    if "COBRE" in lu:
        return "Tubulação de Cobre"
    if "DIESEL" in lu or "OLEO" in lu or "COMBUSTIVEL" in lu:
        return "Tubulação de Óleo Diesel"
    if "SPRINKLER" in lu or "SPK" in lu:
        return "Tubulação de Sprinklers"
    if any(k in lu for k in ["INCÊNDIO", "INCENDIO", "TUBO-INC", "TUBUL INC"]):
        return "Tubulação de Incêndio (Aço Carbono)"
    if any(k in lu for k in ["ESGOTO", "SANITARIO", "SANITÁRIO"]):
        return "Tubulação de Esgoto Sanitário (PVC)"
    if any(k in lu for k in ["AGUA", "ÁGUA", "AF", "AQ"]):
        return "Tubulação de Água (PVC / PPR / Cobre)"
    if any(k in lu for k in ["PLUVIAL", "DRENO", "DRENAGEM"]):
        return "Tubulação de Águas Pluviais / Drenagem"
        
    return f"Tubulação ({layer})"

def canonical_fitting_name(layer: str, name: str, diam: str) -> tuple[str, str, str]:
    """
    Consolida nomes de componentes elétricos, conexões, suportes, válvulas e equipamentos.
    Retorna: (categoria_principal, descricao_comercial, diametro_nominal)
    """
    combined = (layer + " " + name).upper()
    d_val = diam or ""

    # ==========================================
    # 1. COMPONENTES ELÉTRICOS (NBR 5410)
    # ==========================================
    # Tomadas
    if any(k in combined for k in ["TOMADA", "TUE"]) or name in ("T1", "T2", "T3", "T4"):
        if name == "T1" or "BAIXA" in combined or "H=30" in combined or "H = 30" in combined:
            return "Elétrica - Tomada", "Tomada Baixa 2P+T 10A/20A (H=30cm)", d_val or "10A / 250V"
        elif name == "T3" or "ALTA" in combined or "H=210" in combined or "H = 210" in combined:
            return "Elétrica - Tomada", "Tomada Alta / Média 2P+T 20A (H=110cm/210cm)", d_val or "20A / 250V"
        elif name == "T2" or "MEDIA" in combined or "MÉDIA" in combined or "H=110" in combined or "H = 110" in combined:
            return "Elétrica - Tomada", "Tomada Média 2P+T 10A (H=110cm)", d_val or "10A / 250V"
        elif "PISO" in combined:
            return "Elétrica - Tomada", "Tomada de Piso 2P+T em Caixa 4x4\"", d_val or "20A / 250V"
        return "Elétrica - Tomada", f"Tomada Universal 2P+T ({name})", d_val or "10A/20A"

    # Interruptores
    if any(k in combined for k in ["INTERRUPTOR", "PARALELO", "INTERMEDIARIO"]) or name in ("a1", "a2", "b1", "b2", "c1", "s1", "LIG-1-1"):
        if "PARALELO" in combined or "THREE" in combined:
            return "Elétrica - Interruptor", "Interruptor Paralelo (Three-Way) H=110cm", "-"
        elif "INTERMEDIARIO" in combined or "FOUR" in combined:
            return "Elétrica - Interruptor", "Interruptor Intermediário (Four-Way) H=110cm", "-"
        elif name == "LIG-1-1":
            return "Elétrica - Interruptor", "Ponto de Comando / Ligação de Iluminação", "-"
        return "Elétrica - Interruptor", "Interruptor Simples de Parede 1 a 3 Teclas (H=110cm)", "-"

    # Iluminação / Luminárias
    if any(k in combined for k in ["LUM", "LUMI", "LUMINARIA", "LUMINÁRIA", "ILUM", "LAMP", "ESPETO", "SPOT", "PLAFONIER", "ARANDELA"]):
        if "ESPETO" in combined or "JARDIM" in combined:
            return "Elétrica - Iluminação", "Luminária Spot / Espeto de Jardim para Iluminação Externa", "-"
        elif "ARANDELA" in combined or "PAREDE" in combined:
            return "Elétrica - Iluminação", "Arandela de Parede / Ponto de Iluminação (H=210cm)", "-"
        elif "SPOT" in combined or "EMBUTIR" in combined:
            return "Elétrica - Iluminação", "Luminária Spot LED de Embutir", "-"
        return "Elétrica - Iluminação", f"Ponto de Iluminação em Teto / Laje ({name})", "-"

    # Quadros de Distribuição
    if any(k in combined for k in ["QUADRO", "QGBT", "QDG", "QD1", "QD2", "QDC", "PAINEL ELETRICO", "PAINEL ELÉTRICO"]):
        return "Elétrica - Painel", "Quadro de Distribuição de Circuitos de Embutir (QDC / QDG)", d_val or "-"

    # Acessórios Elétricos
    if any(k in combined for k in ["CAMPAINHA", "CIGARRA"]):
        return "Elétrica - Acessório", "Ponto de Campainha / Sinalizador Sonoro", "-"
    if any(k in combined for k in ["SENSOR", "FOTOCELULA", "FOTOCÉLULA"]):
        return "Elétrica - Acessório", "Sensor de Presença / Fotocélula para Iluminação", "-"

    # ==========================================
    # 2. COMPONENTES HIDRÁULICOS E INDUSTRIAIS
    # ==========================================
    # Suportes de Tubulação
    if "SUPORTE" in combined or name.lower().startswith("sup"):
        d = d_val or ("Ø14\"" if "14" in combined else ("Ø4\"" if "4" in combined else ("Ø2\"" if "2" in combined else "-")))
        return "Suporte / Fixação", "Suporte Metálico de Tubulação (Tipo Hanger / Abraçadeira)", d

    # Curvas
    if any(k in combined for k in ["CURVAS 90", "CURVA 90", "CURVA 14X90", "COTOVELO 90"]):
        d = d_val or ("Ø14\"" if "14" in combined else ("Ø10\"" if "10" in combined else ("Ø4\"" if "4" in combined else ("Ø2\"" if "2" in combined else ""))))
        d_label = f" {d}" if d else ""
        return "Conexão - Curva", f"Curva 90° Raio Longo{d_label}", d or "-"

    if any(k in combined for k in ["CURVAS 45", "CURVA 45", "COTOVELO 45"]):
        d_label = f" {d_val}" if d_val else ""
        return "Conexão - Curva", f"Curva 45°{d_label}", d_val or "-"

    # Tês
    if any(k in combined for k in ["TE -", "TE ", "TEE", "TÊ"]):
        d = d_val or ("Ø10\"" if "10" in combined else ("Ø2.1/2\"" if "2-1-2" in combined else ("Ø2\"" if "2" in combined else "")))
        d_label = f" {d}" if d else ""
        return "Conexão - Tê", f"Conexão Tê Reto Industrial{d_label}", d or "-"

    # Flanges
    if any(k in combined for k in ["FLANGE", "CONTRAFLANGE", "ADAPTADOR"]):
        d_label = f" {d_val}" if d_val else ""
        return "Flange / Adaptador", f"Flange com Ressalto ANSI B16.5{d_label}", d_val or "-"

    # Juntas de Expansão
    if "JUNTA" in combined:
        d_label = f" {d_val}" if d_val else ""
        return "Junta / Flexível", f"Junta de Expansão Flexível com Tensores{d_label}", d_val or "-"

    # Acoplamentos Ranhurados
    if any(k in combined for k in ["ACOPLAMENTO", "COUPLING", "GROOVED"]):
        d_label = f" {d_val}" if d_val else ""
        return "Acoplamento Ranhurado", f"Acoplamento Ranhurado (Grooved){d_label}", d_val or "-"

    # Reduções
    if any(k in combined for k in ["REDUÇÃO", "REDUCAO", "REDUCER"]):
        d_label = f" {d_val}" if d_val else ""
        return "Conexão - Redução", f"Redução Concêntrica/Excêntrica{d_label}", d_val or "-"

    # Válvulas
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

    # Bombas e Motores
    if any(k in combined for k in ["BOMBA", "PUMP", "MOTOR"]):
        if "JOCKEY" in combined:
            return "Equipamento / Bomba", "Conjunto Moto-Bomba Jockey", "-"
        return "Equipamento / Bomba", "Conjunto Moto-Bomba Principal 5000 GPM", "-"

    if any(k in combined for k in ["SPRINKLER", "SPK"]):
        d_label = f" {d_val}" if d_val else ""
        return "Sprinkler", f"Bico de Sprinkler Industrial{d_label}", d_val or "-"

    if "HIDRANTE" in combined:
        return "Hidrante", "Válvula / Ponto de Hidrante", d_val or "-"

    # Conexões em layers específicas
    if any(k in combined for k in ["CONEXÃO", "CONEXAO"]):
        d_label = f" {d_val}" if d_val else ""
        return "Conexão / Acoplamento", f"Conexão Hidráulica Ranhurada / Soldada{d_label}", d_val or "-"

    if is_anonymous_block(name):
        d_label = f" {d_val}" if d_val else ""
        return "Conexão / Acessório", f"Conexão Industrial ({layer}){d_label}", d_val or "-"

    d_label = f" {d_val}" if d_val else ""
    return "Acessório / Conexão", f"{name}{d_label}", d_val or "-"

def detect_cad_scale_factor(msp) -> tuple[float, str, str, list[dict]]:
    """
    Averigua a escala e unidade do desenho CAD contra as cotas DIMENSION e a geometria real,
    filtrando ruídos de anotações e fiação para não distorcer a escala de projetos residenciais.
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

    if dimension_samples:
        vals = [s["valor_cad"] for s in dimension_samples]
    else:
        # Quando não há DIMENSION, analisa comprimentos excluindo marcações de fios e hachuras
        line_lengths = []
        for l in msp.query("LINE"):
            layer_u = l.dxf.layer.upper()
            if any(k in layer_u for k in ["FIACAO", "FIAÇÃO", "HATCH", "HACHURA", "DEFPOINTS"]):
                continue
            mag = (l.dxf.start - l.dxf.end).magnitude
            if mag > 15:
                line_lengths.append(mag)
        vals = line_lengths

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
        expl = f"Mediana geométrica = {median_val:.1f} unidades. O desenho foi construído em centímetros (1 unidade CAD = 1 cm). Fator de conversão: divide por 100 para metros."
    else:
        divisor = 1.0
        unit = "Metros (m)"
        expl = f"Mediana geométrica = {median_val:.2f} unidades. O desenho foi construído em metros (1 unidade CAD = 1 metro)."

    return divisor, unit, expl, dimension_samples[:10]

def detect_drawing_views(msp) -> tuple[bool, dict]:
    """
    Detecta de forma inteligente todas as vistas do desenho:
    - Multi-Pavimentos Reais (Térreo, Pavimento Superior, Ático, Cobertura, Cimentação)
    - Vistas de Projeção / Cortes para desduplicação (Corte A-A, Fachadas, Elevações)
    - Regiões de Legenda / Simbologia para não contar ícones de exemplo
    """
    raw_views = []
    for ent in msp:
        if ent.dxftype() in ("TEXT", "MTEXT"):
            raw = ent.dxf.text if ent.dxftype() == "TEXT" else ent.text
            loc = getattr(ent.dxf, "insert", None)
            if not loc:
                continue
            txt = raw.strip()
            u = txt.upper()
            if len(txt) > 65 or not txt:
                continue

            # Ignora marcadores de circuitos, notas de fiação ou itens de legenda com hífen
            if txt.startswith("-") or txt.startswith("•") or txt.startswith("*"):
                continue
            if any(w in u for w in ["CHUVEIRO", "TOMADA", "ILUM", "DISJUNTOR", "CIRCUITO", "ELETRODUTO", "INTERRUPTOR", "CABO", "ESCALA", "PROJEÇÃO", "PROJECAO"]):
                continue

            # 1. Projeções / Cortes / Fachadas (Não aditivas - devem ser desduplicadas)
            if any(k in u for k in ["CORTE", "SECTION", "SEÇÃO", "SECAO", "FACHADA", "ELEVATION", "ELEVAÇÃO", "ELEVACAO", "VISTA LATERAL", "VISTA FRONTAL"]):
                raw_views.append({"type": "PROJECTION", "name": txt, "pos": (loc.x, loc.y)})
            
            # 2. Legendas e Diagramas Unifilares (Não contar blocos de demonstração)
            elif any(k in u for k in ["SIMBOLOGIA", "LEGENDA", "QUADRO DE CARGAS", "DIAGRAMA UNIFILAR"]):
                raw_views.append({"type": "LEGEND", "name": txt, "pos": (loc.x, loc.y)})

            # 3. Pavimentos / Plantas Reais (Aditivas - somam no quantitativo e segregam por andar)
            elif "PLANTA" in u or any(k in u for k in ["TÉRREO", "TERREO", "BAJA", "SUPERIOR", "ALTA", "PISO", "ÁTICO", "ATICO", "AZOTEA", "COBERTURA", "PAVIMENTO", "PAVTO", "FIRST FLOOR", "SECOND FLOOR"]):
                floor_name = txt
                if any(k in u for k in ["TÉRREO", "TERREO", "BAJA", "PRIMER PISO", "1º", "1°", "GROUND"]):
                    floor_name = "Pavimento Térreo"
                elif any(k in u for k in ["SUPERIOR", "ALTA", "SEGUNDO PISO", "2º", "2°", "SECOND"]):
                    floor_name = "Pavimento Superior"
                elif any(k in u for k in ["ÁTICO", "ATICO", "AZOTEA", "COBERTURA", "ROOF", "TECHOS"]):
                    floor_name = "Ático / Cobertura"
                elif any(k in u for k in ["CIMENTA", "FUNDA"]):
                    floor_name = "Fundações / Cimentação"
                elif "PLANTA" in u:
                    floor_name = txt

                raw_views.append({"type": "FLOOR", "name": floor_name, "pos": (loc.x, loc.y)})

    # Agrupa múltiplos títulos que apontam para o mesmo pavimento calculando o baricentro
    floor_clusters = {}
    for v in raw_views:
        if v["type"] == "FLOOR":
            floor_clusters.setdefault(v["name"], []).append(v["pos"])

    consolidated_views = []
    for fname, pts in floor_clusters.items():
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        consolidated_views.append({"type": "FLOOR", "name": fname, "pos": (cx, cy)})

    for v in raw_views:
        if v["type"] in ("PROJECTION", "LEGEND"):
            consolidated_views.append(v)

    floors = [v["name"] for v in consolidated_views if v["type"] == "FLOOR"]
    projections = [v["name"] for v in consolidated_views if v["type"] == "PROJECTION"]

    def classify_point(x: float, y: float) -> tuple[str, str]:
        if not consolidated_views:
            return "FLOOR", "Área Integral"
        best_v = min(consolidated_views, key=lambda v: math.hypot(x - v["pos"][0], y - v["pos"][1]))
        return best_v["type"], best_v["name"]

    has_multi = len(floors) > 1 or len(projections) > 0

    if len(floors) > 1 and len(projections) > 0:
        msg = f"Projeto Multi-Pavimentos ({', '.join(floors)}) com Cortes/Fachadas ({', '.join(projections[:3])}). A auditoria quantificou todos os andares reais e desduplicou as vistas de projeção."
    elif len(floors) > 1:
        msg = f"Projeto Multi-Pavimentos identificado ({', '.join(floors)}). Materiais quantificados e segregados por andar sem omissões."
    elif len(projections) > 0:
        msg = f"Projeto com Planta Baixa e Cortes ({', '.join(projections[:3])}). A auditoria utilizou a Planta Baixa como base de quantitativos para evitar duplicações dos cortes."
    else:
        msg = "Desenho auditado em área integral."

    return has_multi, {
        "possui_planta_e_corte": len(projections) > 0 or len(floors) > 1,
        "possui_multi_pavimentos": len(floors) > 1,
        "vistas_detectadas": floors if floors else ["Área Integral"],
        "vistas_projeções_ignoradas": projections,
        "classify_point": classify_point,
        "is_master_fn": (lambda x, y: classify_point(x, y)[0] == "FLOOR"),
        "mensagem": msg
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
        classify_pt = view_info["classify_point"]
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
                "possui_planta_e_corte": view_info["possui_planta_e_corte"],
                "possui_multi_pavimentos": view_info["possui_multi_pavimentos"],
                "vistas_detectadas": view_info["vistas_detectadas"],
                "vistas_projeções_ignoradas": view_info["vistas_projeções_ignoradas"],
                "mensagem": view_info["mensagem"]
            },
            "tubulacoes_por_diametro": {},
            "conexoes_e_acoplamentos": {},
            "elementos_pontuais": {},
            "metragens_lineares_metros": {},
            "detalhamento_por_pavimento": {},
            "tags_identificadores": [],
            "especificacoes_tecnicas": [],
            "notas_de_prancha": [],
            "topologia_redes": [],
            "cotas_principais_metros": []
        }

        # Inicializa recipientes por pavimento
        for vname in view_info["vistas_detectadas"]:
            summary["detalhamento_por_pavimento"][vname] = {
                "tubulacoes_por_diametro": {},
                "elementos_pontuais": {}
            }

        # 1. MAPEAMENTO DE TEXTOS, DIÂMETROS E ESPECIFICAÇÕES
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
                    "CABO", "ELETRODUTO", "CIRCUITO", "DETECTOR", "TENSÃO", "KVA", "AMPERE", "TOMADA", "DISJUNTOR"
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
                dist = math.hypot(dx - x, dy - y)
                if dist < best_dist:
                    best_dist = dist
                    best_diam = dval
            return best_diam

        # 2. QUANTIFICAÇÃO RIGOROSA DE TUBULAÇÃO E ELETRODUTOS
        layer_lengths = {}
        diam_pipe_lengths = {}
        linear_segments = []
        unique_segments = set()

        min_pipe_thresh = 150.0 * (divisor / 1000.0)
        min_seg_thresh = 400.0 * (divisor / 1000.0)

        # Helper para somar trechos lineares e atribuir ao pavimento
        def process_linear_element(layer: str, length: float, mx: float, my: float, p1: tuple, p2: tuple):
            nonlocal summary, layer_lengths, diam_pipe_lengths, linear_segments, unique_segments
            
            layer_u = layer.upper()
            if layer_u in IGNORED_LAYERS or any(k in layer_u for k in NON_PIPE_COMPONENT_LAYERS):
                return
            if not any(k in layer_u for k in LINEAR_MATERIAL_KEYWORDS):
                return

            disc = identify_element_discipline(layer)
            if disc == "GERAL":
                return

            vtype, vname = classify_pt(mx, my)
            if vtype != "FLOOR":
                return  # Ignora cortes, fachadas ou áreas de legenda

            seg_key = tuple(sorted([p1, p2]))
            if seg_key in unique_segments:
                return
            unique_segments.add(seg_key)

            if length > min_pipe_thresh:
                summary["disciplinas_detectadas"].add(disc)
                layer_lengths[layer] = layer_lengths.get(layer, 0.0) + length

                d_found = find_nearest_diameter(mx, my)
                if not d_found:
                    if "ELET" in layer_u or "CONDUIT" in layer_u:
                        d_found = "Ø3/4\" (DN 25mm)"
                    else:
                        d_found = "Ø A definir"

                canon_net = canonical_pipe_network(layer)
                pipe_key = f"{canon_net} - {d_found}"
                diam_pipe_lengths[pipe_key] = diam_pipe_lengths.get(pipe_key, 0.0) + length

                # Atribuição ao Pavimento
                if vname in summary["detalhamento_por_pavimento"]:
                    pav_dict = summary["detalhamento_por_pavimento"][vname]["tubulacoes_por_diametro"]
                    pav_dict[pipe_key] = pav_dict.get(pipe_key, 0.0) + length

                if length > min_seg_thresh:
                    linear_segments.append({
                        "layer": layer,
                        "pavimento": vname,
                        "rede": canon_net,
                        "diametro": d_found,
                        "comprimento_m": round(length / divisor, 2),
                        "comprimento_cad_original": round(length, 1),
                        "inicio": p1,
                        "fim": p2
                    })

        # Processa LINE
        for line in msp.query("LINE"):
            p1 = (round(line.dxf.start.x, 1), round(line.dxf.start.y, 1))
            p2 = (round(line.dxf.end.x, 1), round(line.dxf.end.y, 1))
            mx = (line.dxf.start.x + line.dxf.end.x) / 2
            my = (line.dxf.start.y + line.dxf.end.y) / 2
            length = (line.dxf.start - line.dxf.end).magnitude
            process_linear_element(line.dxf.layer, length, mx, my, p1, p2)

        # Processa ARC (Curvas de eletrodutos elétricos)
        for arc in msp.query("ARC"):
            r = arc.dxf.radius
            sa = arc.dxf.start_angle
            ea = arc.dxf.end_angle
            if ea < sa:
                ea += 360.0
            length = r * math.radians(ea - sa)
            mx = arc.dxf.center.x
            my = arc.dxf.center.y
            p1 = (round(mx - r, 1), round(my, 1))
            p2 = (round(mx + r, 1), round(my, 1))
            process_linear_element(arc.dxf.layer, length, mx, my, p1, p2)

        # Processa LWPOLYLINE e POLYLINE
        for pline in msp.query("LWPOLYLINE"):
            pts = pline.get_points(format="xy")
            if len(pts) > 1:
                mx = sum(p[0] for p in pts) / len(pts)
                my = sum(p[1] for p in pts) / len(pts)
                plen = sum(math.hypot(pts[i][0] - pts[i-1][0], pts[i][1] - pts[i-1][1]) for i in range(1, len(pts)))
                p1 = (round(pts[0][0], 1), round(pts[0][1], 1))
                p2 = (round(pts[-1][0], 1), round(pts[-1][1], 1))
                process_linear_element(pline.dxf.layer, plen, mx, my, p1, p2)

        # Processa SPLINE (Eletrodutos flexíveis e leitos)
        for spline in msp.query("SPLINE"):
            pts = list(spline.fit_points) if len(spline.fit_points) > 1 else list(spline.control_points)
            if len(pts) > 1:
                mx = sum(p[0] for p in pts) / len(pts)
                my = sum(p[1] for p in pts) / len(pts)
                splen = sum(math.hypot(pts[i][0] - pts[i-1][0], pts[i][1] - pts[i-1][1]) for i in range(1, len(pts)))
                p1 = (round(pts[0][0], 1), round(pts[0][1], 1))
                p2 = (round(pts[-1][0], 1), round(pts[-1][1], 1))
                process_linear_element(spline.dxf.layer, splen, mx, my, p1, p2)

        # Consolidação Geral em Metros
        for k, total_raw in diam_pipe_lengths.items():
            summary["tubulacoes_por_diametro"][k] = round(total_raw / divisor, 2)

        for l, total_raw in layer_lengths.items():
            summary["metragens_lineares_metros"][l] = round(total_raw / divisor, 2)

        # Consolidação por Pavimento em Metros
        for vname, pdata in summary["detalhamento_por_pavimento"].items():
            for pk, raw_v in pdata["tubulacoes_por_diametro"].items():
                pdata["tubulacoes_por_diametro"][pk] = round(raw_v / divisor, 2)

        summary["topologia_redes"] = sorted(linear_segments, key=lambda x: x["comprimento_m"], reverse=True)[:40]

        # 3. CONSOLIDAÇÃO INTELIGENTE DE CONEXÕES, TOMADAS, INTERRUPTORES E EQUIPAMENTOS
        conn_consolidated = Counter()
        equipment_consolidated = Counter()

        for ins in msp.query("INSERT"):
            layer = ins.dxf.layer
            layer_u = layer.upper()
            bname = clean_block_name(ins.dxf.name)
            bname_u = bname.upper()

            # Ignora blocos de carimbo, selo, arquitetura e xrefs
            if any(k in bname_u for k in ["PAVTO", "ÁTICO", "ATICO", "ESQUADRIA", "MOBILIA", "FOLHA", "CARIMBO", "PLANTA"]):
                continue

            # Se for bloco anônimo, só aceita se a camada for comprovadamente de conexões industriais
            if is_anonymous_block(bname):
                if not any(k in layer_u for k in ["CONEXÃO", "CONEXAO", "VALV", "VÁLV", "FLANGE", "ACOPLAMENTO"]):
                    continue

            # Se for layer 0, aceita apenas blocos de engenharia reais
            if layer_u == "0":
                if not any(k in bname_u for k in ["BOMBA", "VALV", "VÁLV", "CURVA", "TE", "FLANGE", "ADAPTADOR", "ACOPLAMENTO", "MOTOR", "PAINEL", "SUPORTE", "LUM", "TOMADA", "INTERRUPTOR", "QUADRO"]):
                    continue
            elif layer_u in IGNORED_LAYERS or any(k in layer_u for k in ("CORTE", "SELO", "MARGEM", "FORMATO", "COTAS", "NIVEL", "EIXO")):
                continue

            loc = ins.dxf.insert
            vtype, vname = classify_pt(loc.x, loc.y)
            if vtype != "FLOOR":
                continue  # Desduplica componentes desenhados nas vistas de corte ou legenda

            disc = identify_element_discipline(layer + " " + bname)
            if disc != "GERAL":
                summary["disciplinas_detectadas"].add(disc)

            d_val = extract_diameter_from_string(bname)
            if not d_val:
                d_val = find_nearest_diameter(loc.x, loc.y, max_dist=2500)

            cat, desc_item, diam_nom = canonical_fitting_name(layer, bname, d_val)

            # Evita blocos vazios ou genéricos sem classificação útil
            if cat == "Acessório / Conexão" and is_anonymous_block(bname):
                continue

            item_key = f"[{cat}] {desc_item}"
            if any(k in cat for k in ["Conexão", "Acoplamento", "Flange", "Junta"]):
                conn_consolidated[item_key] += 1
            else:
                equipment_consolidated[item_key] += 1

            # Atribuição ao Pavimento
            if vname in summary["detalhamento_por_pavimento"]:
                p_el = summary["detalhamento_por_pavimento"][vname]["elementos_pontuais"]
                p_el[item_key] = p_el.get(item_key, 0) + 1

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
            if layout.name != "Model":
                for ent in layout:
                    if ent.dxftype() in ("TEXT", "MTEXT"):
                        raw = ent.dxf.text if ent.dxftype() == "TEXT" else ent.text
                        t = clean_cad_text(raw)
                        if t and len(t) > 15 and not any(k in t.upper() for k in ["DWG", "AUTOCAD", "REV"]):
                            notes.append(t)
        summary["notas_de_prancha"] = list(set(notes))[:20]

        summary["disciplinas_detectadas"] = list(summary["disciplinas_detectadas"])
        if not summary["disciplinas_detectadas"]:
            summary["disciplinas_detectadas"] = ["ENGENHARIA_GERAL"]

        return summary

    finally:
        if temp_created and os.path.exists(dxf_path):
            try:
                os.remove(dxf_path)
            except Exception:
                pass

def format_ai_prompt(summary: dict) -> str:
    lines = []
    lines.append(f"# RELATÓRIO TÉCNICO DE AUDITORIA E EXTRAÇÃO CAD")
    lines.append(f"**Arquivo Analisado:** {summary['arquivo']}")
    lines.append(f"**Disciplinas Identificadas:** {', '.join(summary['disciplinas_detectadas'])}")
    lines.append(f"**Auditoria de Escala:** {summary['auditoria_escala']['unidade_detectada']} (Fator de Conversão: divide por {summary['auditoria_escala']['divisor_aplicado']} para metros)")
    lines.append(f"**Detecção de Vistas & Pavimentos:** {summary['auditoria_vistas']['mensagem']}")
    lines.append("")

    # Detalhamento por Pavimento (se houver mais de 1)
    if summary["auditoria_vistas"].get("possui_multi_pavimentos"):
        lines.append("## 🏢 QUANTITATIVOS SEGREGAÇÃO POR PAVIMENTO")
        for pav_name, pav_data in summary.get("detalhamento_por_pavimento", {}).items():
            lines.append(f"### 📍 {pav_name.upper()}")
            tubos = pav_data.get("tubulacoes_por_diametro", {})
            if tubos:
                lines.append("  *Tubulações e Eletrodutos:*")
                for tk, metros in sorted(tubos.items()):
                    lines.append(f"    - {tk}: **{metros:.2f} m**")
            pontos = pav_data.get("elementos_pontuais", {})
            if pontos:
                lines.append("  *Aparelhos, Conexões e Equipamentos:*")
                for pk, qtd in sorted(pontos.items()):
                    lines.append(f"    - {pk}: **{qtd} un**")
            lines.append("")

    # Quantitativos Globais
    lines.append("## 📦 QUANTITATIVO GERAL DE MATERIAIS CONSOLIDADOS")
    lines.append("### 1. Tubulações e Eletrodutos por Diâmetro")
    if summary["tubulacoes_por_diametro"]:
        for item, metros in sorted(summary["tubulacoes_por_diametro"].items(), key=lambda x: x[1], reverse=True):
            lines.append(f"- **{item}**: {metros:.2f} metros")
    else:
        lines.append("*(Nenhuma rede linear física identificada)*")

    lines.append("")
    lines.append("### 2. Conexões, Válvulas, Tomadas e Equipamentos")
    if summary["elementos_pontuais"]:
        for item, qtd in sorted(summary["elementos_pontuais"].items(), key=lambda x: x[1], reverse=True):
            lines.append(f"- **{item}**: {qtd} unidades")
    else:
        lines.append("*(Nenhum componente pontual identificado)*")

    lines.append("")
    lines.append("---")
    lines.append("*Extração processada deterministicamente sem duplicações de cortes ou vistas auxiliares.*")
    return "\n".join(lines)

def format_topology_text(summary: dict) -> str:
    lines = []
    lines.append("# TOPOLOGIA CAD-AS-CODE - MAPEAMENTO DE TRECHOS REAIS")
    lines.append(f"Arquivo: {summary['arquivo']} | Escala: {summary['auditoria_escala']['unidade_detectada']}")
    lines.append("")
    for seg in summary.get("topologia_redes", []):
        pav_str = f" [{seg['pavimento']}]" if "pavimento" in seg else ""
        lines.append(f"- Rede: {seg['rede']}{pav_str} | Diâmetro: {seg['diametro']} | Comp: {seg['comprimento_m']} m (CAD: {seg['comprimento_cad_original']}) | Início: {seg['inicio']} -> Fim: {seg['fim']}")
    return "\n".join(lines)

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
