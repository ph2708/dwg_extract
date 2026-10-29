#!/usr/bin/env python3
"""
Extrator e Auditor Universal Multidisciplinar de Projetos CAD (DWG / DXF)
Com Motor de Diagnóstico de Inconsistências e Resolução Semântica de Blocos Anônimos
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
        "ESGOTO", "AGUA", "ÁGUA", "PLUVIAL", "RETENÇÃO", "BOMBA", "CONEXÃO", "CONEXAO"
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
    """Limpa sufixos de flats e cópias como -flat-1, -flat-2, etc."""
    cleaned = re.sub(r"(-flat-\d+)+", "", raw_name)
    return cleaned.strip()

def is_anonymous_block(name: str) -> bool:
    return any(name.startswith(p) for p in ANONYMOUS_PREFIXES)

def extract_project_summary(file_path: str) -> dict:
    path = Path(file_path)
    dxf_path = str(file_path)
    temp_created = False
    conversion_warnings = ""

    if path.suffix.lower() == ".dwg":
        target_dxf = path.with_suffix(".temp.dxf")
        success, conversion_warnings = convert_dwg_to_dxf(str(path), str(target_dxf))
        if not success:
            raise RuntimeError("Falha ao converter o arquivo DWG. O arquivo pode estar corrompido ou em formato incompatível.")
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
            "elementos_pontuais": {},
            "metragens_lineares_metros": {},
            "tags_identificadores": [],
            "especificacoes_tecnicas": [],
            "notas_de_prancha": []
        }

        # ----------------------------------------------------
        # 1. MOTOR DE DIAGNÓSTICO DE INCONSISTÊNCIAS
        # ----------------------------------------------------
        if "ERROR:" in conversion_warnings or "corrupted" in conversion_warnings.lower():
            summary["inconsistencias"].append({
                "tipo": "INTEGRIDADE_CAD",
                "gravidade": "ALERTA",
                "mensagem": "O arquivo DWG contém seções internas instáveis ou corrompidas (aviso do leitor de DWG). Algumas entidades podem ter sido ignoradas."
            })

        insunits = doc.header.get("$INSUNITS", 0)
        if insunits == 0:
            summary["inconsistencias"].append({
                "tipo": "ESCALA_UNIDADES",
                "gravidade": "ATENÇÃO",
                "mensagem": "O projeto está como 'Unitless' (sem unidade definida no cabeçalho). O extrator assumiu escala padrão em milímetros (mm). Valide a proporção das metragens."
            })

        extreme_entities = 0
        for ent in msp:
            if ent.dxftype() == "INSERT":
                loc = ent.dxf.insert
                if abs(loc.x) > 10000000 or abs(loc.y) > 10000000:
                    extreme_entities += 1
        if extreme_entities > 0:
            summary["inconsistencias"].append({
                "tipo": "COORDENADAS_PERDIDAS",
                "gravidade": "ALERTA",
                "mensagem": f"Foram detectados {extreme_entities} blocos/entidades com coordenadas astronômicas (> 10.000 km da origem). Indício de peças 'perdidas' ou cotas descoladas no CAD."
            })

        layer_entity_counts = Counter()
        for ent in msp:
            layer_entity_counts[ent.dxf.layer] += 1

        ent_in_layer_zero = layer_entity_counts.get("0", 0)
        total_ents = sum(layer_entity_counts.values())
        if total_ents > 0 and (ent_in_layer_zero / total_ents) > 0.4:
            summary["inconsistencias"].append({
                "tipo": "DESORGANIZACAO_LAYERS",
                "gravidade": "ATENÇÃO",
                "mensagem": f"Mais de 40% das entidades do projeto ({ent_in_layer_zero} de {total_ents}) estão jogadas na 'Layer 0' sem camada técnica definida."
            })

        # ----------------------------------------------------
        # 2. MAPEAMENTO ESPACIAL DE TEXTOS / TAGS PARA RESOLVER BLOCOS
        # ----------------------------------------------------
        cad_texts = []
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

                upper = txt.upper()
                if re.match(r"^[A-Z]{1,4}[-_]?[0-9]{1,3}[A-Z]?$", txt) or "SOBE PARA" in upper or "DESCE PARA" in upper:
                    tags.add(f"[{ent.dxf.layer}] {txt}")

                if any(w in upper for w in [
                    "VALVULA", "VÁLVULA", "JUNTA", "FLANGE", "RETENÇÃO", "BOMBA", "Ø",
                    "ANSI", "CLASSE", "PSI", "ASTM", "NBR", "SCH", "GPM",
                    "CABO", "ELETRODUTO", "CIRCUITO", "DETECTOR", "TENSÃO", "KVA", "AMPERE", "DISJUNTOR"
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
                "mensagem": f"Classes de pressão mistas identificadas no mesmo projeto ({', '.join(classes_pressao)}). Exige verificação se os contraflanges e conexões de transição foram previstos."
            })

        def resolve_nearest_tag_or_label(x, y, layer, max_dist=2000):
            """Encontra texto ou número de balão técnico mais próximo da inserção do bloco."""
            best_dist = max_dist
            best_label = None
            for tx, ty, tval, tlayer in cad_texts:
                d = ((tx-x)**2 + (ty-y)**2)**0.5
                if d < best_dist:
                    # Preferência por textos de tags curtas ou mesma layer
                    if len(tval) < 25:
                        best_dist = d
                        best_label = tval
            return best_label

        # ----------------------------------------------------
        # 3. CONTAGEM E RESOLUÇÃO INTELIGENTE DE ELEMENTOS PONTUAIS
        # ----------------------------------------------------
        block_counts = Counter()
        for ins in msp.query("INSERT"):
            layer = ins.dxf.layer
            bname = clean_block_name(ins.dxf.name)
            disc = identify_element_discipline(layer + " " + bname)

            if disc != "GERAL":
                summary["disciplinas_detectadas"].add(disc)

                # Se for bloco com nome técnico legível (ex: CURVAS 90, PLANTA BOMBA)
                if not is_anonymous_block(bname):
                    label = f"[{layer}] {bname}"
                else:
                    # Bloco anônimo do ZWCAD/AutoCAD: resolver semanticamente
                    loc = ins.dxf.insert
                    near_tag = resolve_nearest_tag_or_label(loc.x, loc.y, layer)
                    # Normalizar o nome da camada técnica
                    layer_clean = layer.replace("_", " ").title()
                    if near_tag:
                        label = f"[{layer}] {layer_clean} (Item/Tag: {near_tag})"
                    else:
                        label = f"[{layer}] {layer_clean} (Bloco Técnico)"

                block_counts[label] += 1

        # Também contar camadas com entidades dedicadas (ex: SPRINKLERS, HIDRANTE)
        for layer, count in layer_entity_counts.items():
            disc = identify_element_discipline(layer)
            if disc != "GERAL" and count > 0:
                summary["disciplinas_detectadas"].add(disc)
                key = f"[LAYER] {layer}"
                if key not in block_counts and any(k in layer.upper() for k in ["HIDRANTE", "DETECTOR", "SPRINKLER", "QUADRO", "PAINEL", "LUMINARIA", "TOMADA"]):
                    block_counts[key] = count

        summary["elementos_pontuais"] = dict(block_counts)

        # ----------------------------------------------------
        # 4. METRAGEM LINEAR POR LAYER
        # ----------------------------------------------------
        layer_lengths = {}
        for line in msp.query("LINE"):
            length = (line.dxf.start - line.dxf.end).magnitude
            layer_lengths[line.dxf.layer] = layer_lengths.get(line.dxf.layer, 0.0) + length

        for pline in msp.query("LWPOLYLINE"):
            pts = pline.get_points(format="xy")
            if len(pts) > 1:
                plen = sum(((pts[i][0]-pts[i-1][0])**2 + (pts[i][1]-pts[i-1][1])**2)**0.5 for i in range(1, len(pts)))
                layer_lengths[pline.dxf.layer] = layer_lengths.get(pline.dxf.layer, 0.0) + plen

        for layer, total_mm in layer_lengths.items():
            disc = identify_element_discipline(layer)
            if disc != "GERAL" and total_mm > 400:
                summary["disciplinas_detectadas"].add(disc)
                summary["metragens_lineares_metros"][layer] = round(total_mm / 1000.0, 2)

        summary["tags_identificadores"] = sorted(list(tags))[:40]
        summary["especificacoes_tecnicas"] = sorted(list(specs))

        # ----------------------------------------------------
        # 5. NOTAS DE PRANCHA
        # ----------------------------------------------------
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
                            if txt not in notes:
                                notes.append(txt)

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

    p.append("\n[INSTRUÇÃO PARA A IA]:")
    p.append(f"Você é um Engenheiro Sênior Auditor Especialista nas disciplinas: {disc_str}.")
    p.append("Analise minuciosamente os dados reais extraídos diretamente do projeto CAD:")
    p.append("1. Considere os alertas de inconsistência apontados acima na sua análise técnica.")
    p.append("2. Faça a auditoria de quantitativos e aponte possíveis itens ou conexões omitidos.")
    p.append("3. Formule as principais dúvidas e questionamentos para enviar ao projetista.")
    p.append("4. Estruture uma tabela de cotação com descritivo comercial padronizado para fornecedores.")
    p.append("\n" + "-"*40)
    p.append("DADOS EXTRAÍDOS DO PROJETO CAD:")
    p.append("-"*40)

    p.append("\n1. ESPECIFICAÇÕES TÉCNICAS E EQUIPAMENTOS LOCALIZADOS:")
    if data["especificacoes_tecnicas"]:
        for s in data["especificacoes_tecnicas"]:
            p.append(f"  • {s}")
    else:
        p.append("  (Nenhuma especificação em texto isolada detectada)")

    p.append("\n2. ELEMENTOS PONTUAIS & QUANTITATIVOS FÍSICOS (PEÇAS / PONTOS / EQUIPAMENTOS):")
    if data["elementos_pontuais"]:
        for item, qtd in sorted(data["elementos_pontuais"].items(), key=lambda x: x[1], reverse=True)[:35]:
            p.append(f"  • {item}: {qtd} un")
    else:
        p.append("  (Sem contagem direta de blocos pontuais)")

    p.append("\n3. METRAGEM LINEAR DE REDES, TUBOS E CABEAMENTO (POR LAYER):")
    if data["metragens_lineares_metros"]:
        for lyr, met in sorted(data["metragens_lineares_metros"].items(), key=lambda x: x[1], reverse=True):
            p.append(f"  • {lyr}: {met:.2f} metros")
    else:
        p.append("  (Sem metragens lineares contínuas identificadas)")

    if data["tags_identificadores"]:
        p.append("\n4. TAGS, PONTOS E IDENTIFICADORES DO PROJETO:")
        p.append("  " + ", ".join(data["tags_identificadores"][:30]))

    if data["notas_de_prancha"]:
        p.append("\n5. NOTAS GERAIS, CRITÉRIOS DE MONTAGEM E NORMAS TÉCNICAS:")
        for n in data["notas_de_prancha"]:
            p.append(f"  [NOTA]: {n}")

    p.append("\n" + "="*80)
    p.append("[PERGUNTAS RECOMENDADAS PARA COPIAR E COLAR NO SEU CHAT DE IA]:")
    for inc in data["inconsistencias"]:
        if inc["tipo"] == "CONFLITO_PRESSAO":
            p.append("• 'Quais são as peças de transição ou contraflanges necessários para compatibilizar os equipamentos de 300 psi com as linhas de 150 psi?'")
        elif inc["tipo"] == "COORDENADAS_PERDIDAS":
            p.append("• 'Como devo tratar os blocos identificados fora dos limites da prancha na listagem de materiais?'")
    p.append("• 'Com base nas metragens e peças listadas, monte a lista completa de conexões, curvas e suportes recomendados.'")
    p.append("• 'Gere uma tabela estruturada para cotação pronta para envio a fornecedores industriais.'")
    p.append("="*80)

    return "\n".join(p)

def main():
    if len(sys.argv) < 2:
        print("Uso: python cad_to_ai.py <arquivo.dwg_ou_dxf> [--json]")
        sys.exit(1)

    file_path = sys.argv[1]
    as_json = "--json" in sys.argv

    if not os.path.exists(file_path):
        print(f"Erro: Arquivo '{file_path}' não encontrado.", file=sys.stderr)
        sys.exit(1)

    try:
        data = extract_project_summary(file_path)
        if as_json:
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(format_ai_prompt(data))
    except Exception as e:
        print(f"Erro ao processar: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
