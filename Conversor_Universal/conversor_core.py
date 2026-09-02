# -*- coding: utf-8 -*-
"""Núcleo seguro e testável do Conversor Universal de Arquivos.

Este módulo não contém elementos de interface. Ele concentra validação, leitura,
normalização e conversão para que arquivos enviados nunca precisem ser executados
e para que os limites de memória possam ser verificados antes das operações caras.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import math
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
from charset_normalizer import from_bytes
from lxml import etree


# Os limites são conservadores porque XLSX e Parquet podem expandir muito depois
# do upload. A dataclass permite usar valores menores nos testes sem arquivos enormes.
@dataclass(frozen=True)
class Limites:
    """Limites preventivos de tamanho, estrutura e profundidade."""

    upload_bytes: int = 25 * 1024 * 1024
    dataframe_bytes: int = 100 * 1024 * 1024
    resultado_bytes: int = 50 * 1024 * 1024
    max_linhas: int = 500_000
    max_colunas: int = 10_000
    max_celulas: int = 5_000_000
    xlsx_descompactado_bytes: int = 150 * 1024 * 1024
    xlsx_max_entradas: int = 5_000
    xlsx_taxa_compressao: float = 100.0
    parquet_estimado_bytes: int = 150 * 1024 * 1024
    max_profundidade: int = 40
    xml_max_elementos: int = 1_000_000


LIMITES_PADRAO = Limites()
FORMATOS_ENTRADA = ("csv", "json", "xml", "xlsx", "txt", "parquet", "py")
FORMATOS_SAIDA = ("csv", "json", "xml", "xlsx", "txt", "parquet")
MIME_TYPES = {
    "csv": "text/csv",
    "json": "application/json",
    "xml": "application/xml",
    "txt": "text/plain",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "parquet": "application/octet-stream",
}

DELIMITADORES = {
    "Automático": None,
    "Vírgula (,)": ",",
    "Ponto e vírgula (;)": ";",
    "Tabulação": "\t",
    "Barra vertical (|)": "|",
}
CODIFICACOES = {
    "Automático": None,
    "UTF-8": "utf-8",
    "UTF-8 com BOM": "utf-8-sig",
    "Windows-1252": "cp1252",
    "ISO-8859-1": "iso-8859-1",
}


class ErroConversor(ValueError):
    """Erro esperado, seguro e adequado para apresentação ao usuário."""


class LimiteExcedido(ErroConversor):
    """Um arquivo ou estrutura ultrapassou um limite preventivo."""


def calcular_sha256(conteudo: bytes) -> str:
    """Retorna uma identidade estável sem confiar no nome do arquivo."""

    return hashlib.sha256(conteudo).hexdigest()


def obter_extensao(nome_arquivo: str) -> str:
    """Extrai e valida uma extensão de entrada suportada."""

    if not nome_arquivo or "." not in nome_arquivo:
        raise ErroConversor("O arquivo precisa possuir uma extensão suportada.")
    extensao = nome_arquivo.rsplit(".", 1)[1].lower().strip()
    if extensao not in FORMATOS_ENTRADA:
        raise ErroConversor(f"Formato de entrada não suportado: .{extensao or '?'}.")
    return extensao


def validar_tamanho_upload(tamanho: int, limites: Limites = LIMITES_PADRAO) -> None:
    """Interrompe o fluxo antes de copiar uploads vazios ou excessivos."""

    if tamanho <= 0:
        raise ErroConversor("O arquivo está vazio.")
    if tamanho > limites.upload_bytes:
        max_mb = limites.upload_bytes / (1024 * 1024)
        raise LimiteExcedido(f"O arquivo excede o limite de upload de {max_mb:.0f} MB.")


def _profundidade_objeto(valor: Any, atual: int = 0) -> int:
    """Calcula a profundidade de estruturas JSON/dict sem executar conteúdo."""

    if isinstance(valor, dict):
        if not valor:
            return atual + 1
        return max(_profundidade_objeto(item, atual + 1) for item in valor.values())
    if isinstance(valor, (list, tuple)):
        if not valor:
            return atual + 1
        return max(_profundidade_objeto(item, atual + 1) for item in valor)
    return atual


def detectar_codificacao(
    conteudo: bytes,
    escolha: str = "Automático",
) -> tuple[str, float, list[str]]:
    """Detecta uma codificação textual ou valida a escolha manual.

    Retorna o codec, confiança aproximada entre 0 e 1 e avisos informativos.
    """

    if not conteudo:
        raise ErroConversor("O arquivo está vazio.")
    codec_manual = CODIFICACOES.get(escolha, escolha if escolha != "Automático" else None)
    if codec_manual:
        try:
            conteudo.decode(codec_manual, errors="strict")
        except (LookupError, UnicodeDecodeError) as erro:
            raise ErroConversor(
                f"O conteúdo não é válido para a codificação {escolha}."
            ) from erro
        return codec_manual, 1.0, []

    # UTF-8 é priorizado para evitar que detectores escolham codecs legados para
    # arquivos curtos que já estão corretamente padronizados.
    try:
        conteudo.decode("utf-8-sig", errors="strict")
        return "utf-8-sig", 1.0, []
    except UnicodeDecodeError:
        pass

    melhor = from_bytes(conteudo).best()
    if melhor is None or not melhor.encoding:
        raise ErroConversor(
            "Não foi possível detectar a codificação. Escolha-a manualmente."
        )

    codec = melhor.encoding
    try:
        conteudo.decode(codec, errors="strict")
    except (LookupError, UnicodeDecodeError) as erro:
        raise ErroConversor(
            "A codificação detectada não conseguiu decodificar todo o arquivo."
        ) from erro

    coerencia = float(getattr(melhor, "percent_coherence", 0.0) or 0.0) / 100
    avisos: list[str] = []
    if coerencia < 0.5:
        avisos.append(
            f"A codificação {codec} foi detectada com baixa confiança; "
            "confirme-a nas opções de leitura."
        )
    return codec, coerencia, avisos


def detectar_delimitador(texto: str, escolha: str = "Automático") -> str:
    """Detecta ou valida um delimitador tabular entre as opções suportadas."""

    manual = DELIMITADORES.get(escolha, escolha if escolha != "Automático" else None)
    if manual:
        return manual

    amostra = texto[:64_000]
    try:
        return csv.Sniffer().sniff(amostra, delimiters=",;\t|").delimiter
    except csv.Error as erro:
        raise ErroConversor(
            "Não foi possível detectar um delimitador tabular. "
            "Escolha o delimitador manualmente."
        ) from erro


def _validar_estrutura_tabular(texto: str, delimitador: str) -> None:
    """Rejeita texto livre e linhas com quantidades incompatíveis de campos."""

    linhas = [linha for linha in texto.splitlines() if linha.strip()]
    if not linhas:
        raise ErroConversor("O arquivo não contém linhas com dados.")
    try:
        registros = list(csv.reader(linhas, delimiter=delimitador, strict=True))
    except csv.Error as erro:
        raise ErroConversor(f"A estrutura tabular é inválida: {erro}.") from erro
    larguras = {len(registro) for registro in registros}
    if len(larguras) != 1:
        raise ErroConversor("As linhas possuem quantidades diferentes de colunas.")
    if next(iter(larguras), 0) < 2:
        raise ErroConversor(
            "O conteúdo não parece tabular: foi identificada apenas uma coluna."
        )


def ler_texto_tabular(
    conteudo: bytes,
    formato: str,
    codificacao: str = "Automático",
    delimitador: str = "Automático",
) -> tuple[pd.DataFrame, dict[str, Any], list[str]]:
    """Lê CSV/TXT com encoding e delimitador explícitos ou detectados."""

    codec, confianca, avisos = detectar_codificacao(conteudo, codificacao)
    try:
        texto = conteudo.decode(codec, errors="strict")
    except UnicodeDecodeError as erro:
        raise ErroConversor("O arquivo contém caracteres inválidos.") from erro

    escolha_delimitador = delimitador
    if formato == "txt" and delimitador == "Automático":
        escolha_delimitador = "Tabulação"
    separador = detectar_delimitador(texto, escolha_delimitador)
    _validar_estrutura_tabular(texto, separador)

    try:
        df = pd.read_csv(io.StringIO(texto), sep=separador)
    except pd.errors.EmptyDataError as erro:
        raise ErroConversor("O arquivo não possui dados tabulares.") from erro
    except pd.errors.ParserError as erro:
        raise ErroConversor(f"Não foi possível interpretar a tabela: {erro}.") from erro

    info = {
        "codificacao": codec,
        "confianca_codificacao": confianca,
        "delimitador": "TAB" if separador == "\t" else separador,
    }
    return df, info, avisos


def normalizar_objeto_tabular(valor: Any) -> pd.DataFrame:
    """Transforma dict/lista de registros em uma tabela sem decisões ambíguas."""

    if isinstance(valor, list):
        if not valor:
            raise ErroConversor("A lista de registros está vazia.")
        if not all(isinstance(item, dict) for item in valor):
            raise ErroConversor("A lista precisa conter somente dicionários.")
        return pd.json_normalize(valor)

    if not isinstance(valor, dict) or not valor:
        raise ErroConversor("O conteúdo precisa ser um dicionário não vazio.")

    valores = list(valor.values())
    if all(not isinstance(item, (list, tuple, dict)) for item in valores):
        return pd.DataFrame([valor])

    if all(isinstance(item, (list, tuple)) for item in valores):
        tamanhos = {len(item) for item in valores}
        if len(tamanhos) != 1:
            raise ErroConversor("As listas do dicionário possuem tamanhos diferentes.")
        if not tamanhos or next(iter(tamanhos)) == 0:
            raise ErroConversor("As listas do dicionário estão vazias.")
        if all(all(not isinstance(v, dict) for v in item) for item in valores):
            return pd.DataFrame(valor)

    listas_registros = [
        (chave, item)
        for chave, item in valor.items()
        if isinstance(item, list) and item and all(isinstance(v, dict) for v in item)
    ]
    outros_complexos = [
        item
        for chave, item in valor.items()
        if not any(chave == chave_lista for chave_lista, _ in listas_registros)
        and isinstance(item, (list, tuple, dict))
    ]
    if len(listas_registros) == 1 and not outros_complexos:
        _, registros = listas_registros[0]
        df = pd.json_normalize(registros)
        for chave, item in valor.items():
            if chave != listas_registros[0][0]:
                df[chave] = item
        return df

    # Um dicionário aninhado sem listas de registros representa uma entidade única.
    if all(not isinstance(item, (list, tuple)) for item in valores):
        return pd.json_normalize([valor])

    raise ErroConversor(
        "A estrutura possui listas ou objetos incompatíveis com uma tabela inequívoca."
    )


def extrair_dict_python(
    conteudo: bytes,
    limites: Limites = LIMITES_PADRAO,
) -> dict[str, Any]:
    """Extrai um dict literal de uma AST validada, sem executar o arquivo."""

    try:
        texto = conteudo.decode("utf-8", errors="strict")
    except UnicodeDecodeError as erro:
        raise ErroConversor("O arquivo Python precisa estar em UTF-8.") from erro
    if not texto.strip():
        raise ErroConversor("O arquivo Python está vazio.")
    try:
        arvore = ast.parse(texto, mode="exec")
    except SyntaxError as erro:
        raise ErroConversor(f"O arquivo Python possui sintaxe inválida: {erro.msg}.") from erro
    if len(arvore.body) != 1:
        raise ErroConversor("O arquivo Python deve conter exatamente uma instrução.")

    instrucao = arvore.body[0]
    if isinstance(instrucao, ast.Expr):
        no_valor = instrucao.value
    elif isinstance(instrucao, ast.Assign):
        if len(instrucao.targets) != 1 or not isinstance(instrucao.targets[0], ast.Name):
            raise ErroConversor("A atribuição deve usar um único nome simples.")
        no_valor = instrucao.value
    else:
        raise ErroConversor("Somente um dicionário literal ou atribuição simples é aceito.")

    if not isinstance(no_valor, ast.Dict):
        raise ErroConversor("O valor principal do arquivo Python deve ser um dicionário.")
    try:
        valor = ast.literal_eval(no_valor)
    except (ValueError, TypeError, SyntaxError) as erro:
        raise ErroConversor("O dicionário contém uma construção não literal.") from erro
    if not isinstance(valor, dict):
        raise ErroConversor("O conteúdo extraído não é um dicionário.")
    if _profundidade_objeto(valor) > limites.max_profundidade:
        raise LimiteExcedido("O dicionário excede a profundidade máxima permitida.")
    return valor


def ler_json(
    conteudo: bytes,
    limites: Limites = LIMITES_PADRAO,
) -> tuple[pd.DataFrame, dict[str, Any], list[str]]:
    """Analisa JSON estruturalmente antes de normalizá-lo."""

    codec, _, avisos = detectar_codificacao(conteudo, "Automático")
    try:
        valor = json.loads(conteudo.decode(codec, errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as erro:
        raise ErroConversor(f"JSON inválido: {erro}.") from erro
    if _profundidade_objeto(valor) > limites.max_profundidade:
        raise LimiteExcedido("O JSON excede a profundidade máxima permitida.")
    df = normalizar_objeto_tabular(valor)
    return df, {"codificacao": codec}, avisos


def validar_xml_seguro(
    conteudo: bytes,
    limites: Limites = LIMITES_PADRAO,
) -> etree._Element:
    """Faz parse XML sem rede, DTD, entidades ou modo de árvore gigante."""

    if re.search(br"<!\s*(DOCTYPE|ENTITY)\b", conteudo, flags=re.IGNORECASE):
        raise ErroConversor("XML com DTD ou declaração de entidade não é permitido.")
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
        recover=False,
        remove_comments=True,
    )
    try:
        raiz = etree.fromstring(conteudo, parser=parser)
    except (etree.XMLSyntaxError, ValueError) as erro:
        raise ErroConversor(f"XML inválido ou inseguro: {erro}.") from erro

    total = 0
    max_profundidade = 0
    pilha = [(raiz, 1)]
    while pilha:
        elemento, profundidade = pilha.pop()
        total += 1
        max_profundidade = max(max_profundidade, profundidade)
        if total > limites.xml_max_elementos:
            raise LimiteExcedido("O XML possui elementos demais.")
        if max_profundidade > limites.max_profundidade:
            raise LimiteExcedido("O XML excede a profundidade máxima permitida.")
        pilha.extend((filho, profundidade + 1) for filho in elemento)
    return raiz


def _nome_xml(tag: Any) -> str:
    """Remove namespace de uma tag sem alterar o conteúdo textual."""

    return etree.QName(tag).localname if isinstance(tag, str) else str(tag)


def _elemento_xml_para_objeto(elemento: etree._Element) -> Any:
    """Converte uma subárvore XML segura em dict/lista/escalar normalizável."""

    filhos = list(elemento)
    if not filhos:
        return (elemento.text or "").strip()
    resultado: dict[str, Any] = {}
    for filho in filhos:
        chave = _nome_xml(filho.tag)
        valor = _elemento_xml_para_objeto(filho)
        if chave in resultado:
            atual = resultado[chave]
            resultado[chave] = atual + [valor] if isinstance(atual, list) else [atual, valor]
        else:
            resultado[chave] = valor
    return resultado


def ler_xml(
    conteudo: bytes,
    limites: Limites = LIMITES_PADRAO,
) -> tuple[pd.DataFrame, dict[str, Any], list[str]]:
    """Lê XML já validado, sem entregar conteúdo inseguro ao Pandas."""

    raiz = validar_xml_seguro(conteudo, limites)
    filhos = list(raiz)
    if not filhos:
        raise ErroConversor("O XML não contém registros tabulares.")
    if all(list(filho) for filho in filhos):
        registros = [_elemento_xml_para_objeto(filho) for filho in filhos]
        df = pd.json_normalize(registros)
    else:
        registro = _elemento_xml_para_objeto(raiz)
        if not isinstance(registro, dict):
            raise ErroConversor("O XML não possui uma estrutura tabular reconhecível.")
        df = pd.json_normalize([registro])
    return df, {"elemento_raiz": _nome_xml(raiz.tag)}, []


def inspecionar_xlsx(
    conteudo: bytes,
    limites: Limites = LIMITES_PADRAO,
) -> list[str]:
    """Valida assinatura, estrutura e expansão ZIP antes do OpenPyXL."""

    if not conteudo.startswith(b"PK"):
        raise ErroConversor("A extensão .xlsx não corresponde a um arquivo ZIP/XLSX.")
    try:
        with zipfile.ZipFile(io.BytesIO(conteudo)) as arquivo_zip:
            entradas = arquivo_zip.infolist()
            if len(entradas) > limites.xlsx_max_entradas:
                raise LimiteExcedido("O XLSX possui entradas internas demais.")
            nomes = {entrada.filename for entrada in entradas}
            obrigatorios = {"[Content_Types].xml", "xl/workbook.xml"}
            if not obrigatorios.issubset(nomes):
                raise ErroConversor("O ZIP não contém a estrutura obrigatória de um XLSX.")

            total_compactado = 0
            total_aberto = 0
            for entrada in entradas:
                caminho = PurePosixPath(entrada.filename)
                if caminho.is_absolute() or ".." in caminho.parts:
                    raise ErroConversor("O XLSX contém um caminho interno inseguro.")
                total_compactado += max(entrada.compress_size, 1)
                total_aberto += entrada.file_size
                taxa = entrada.file_size / max(entrada.compress_size, 1)
                if entrada.file_size > 1_000_000 and taxa > limites.xlsx_taxa_compressao:
                    raise LimiteExcedido("O XLSX possui taxa de compressão suspeita.")
            if total_aberto > limites.xlsx_descompactado_bytes:
                raise LimiteExcedido("O XLSX excede o limite descompactado.")
            if total_aberto / max(total_compactado, 1) > limites.xlsx_taxa_compressao:
                raise LimiteExcedido("O XLSX pode ser um ZIP bomb.")
    except zipfile.BadZipFile as erro:
        raise ErroConversor("O arquivo XLSX está corrompido.") from erro

    try:
        excel = pd.ExcelFile(io.BytesIO(conteudo), engine="openpyxl")
        planilhas = list(excel.sheet_names)
        excel.close()
    except Exception as erro:
        raise ErroConversor(f"Não foi possível inspecionar o XLSX: {erro}.") from erro
    if not planilhas:
        raise ErroConversor("O arquivo XLSX não possui planilhas válidas.")
    return planilhas


def inspecionar_parquet(
    conteudo: bytes,
    limites: Limites = LIMITES_PADRAO,
) -> dict[str, int]:
    """Valida assinatura e metadados Parquet antes de materializar dados."""

    if len(conteudo) < 8 or not conteudo.startswith(b"PAR1") or not conteudo.endswith(b"PAR1"):
        raise ErroConversor("A extensão .parquet não corresponde a um Parquet válido.")
    try:
        arquivo = pq.ParquetFile(io.BytesIO(conteudo))
        metadados = arquivo.metadata
        tamanho_estimado = sum(
            metadados.row_group(indice).total_byte_size
            for indice in range(metadados.num_row_groups)
        )
    except Exception as erro:
        raise ErroConversor(f"O arquivo Parquet está corrompido: {erro}.") from erro
    if metadados.num_rows > limites.max_linhas:
        raise LimiteExcedido("O Parquet excede o limite de linhas.")
    if metadados.num_columns > limites.max_colunas:
        raise LimiteExcedido("O Parquet excede o limite de colunas.")
    if tamanho_estimado > limites.parquet_estimado_bytes:
        raise LimiteExcedido("O Parquet excede o tamanho descompactado estimado.")
    return {
        "linhas_estimadas": metadados.num_rows,
        "colunas_estimadas": metadados.num_columns,
        "tamanho_estimado": tamanho_estimado,
    }


def validar_assinatura(
    conteudo: bytes,
    nome_arquivo: str,
    formato: str | None = None,
    limites: Limites = LIMITES_PADRAO,
) -> dict[str, Any]:
    """Confirma a extensão e a estrutura real quando o formato permite."""

    validar_tamanho_upload(len(conteudo), limites)
    extensao = obter_extensao(nome_arquivo)
    formato = formato or extensao
    if formato != extensao:
        raise ErroConversor("A extensão declarada não corresponde ao formato informado.")
    if formato == "xlsx":
        return {"planilhas": inspecionar_xlsx(conteudo, limites)}
    if formato == "parquet":
        return inspecionar_parquet(conteudo, limites)
    if formato == "xml":
        raiz = validar_xml_seguro(conteudo, limites)
        return {"elemento_raiz": _nome_xml(raiz.tag)}
    if formato == "json":
        codec, _, _ = detectar_codificacao(conteudo)
        try:
            json.loads(conteudo.decode(codec, errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as erro:
            raise ErroConversor("A extensão .json não corresponde a um JSON válido.") from erro
    elif formato == "py":
        extrair_dict_python(conteudo, limites)
    elif formato in {"csv", "txt"}:
        detectar_codificacao(conteudo)
    return {}


def validar_dataframe(df: pd.DataFrame, limites: Limites = LIMITES_PADRAO) -> int:
    """Valida forma e memória profunda do DataFrame, retornando bytes usados."""

    if df.empty or df.shape[1] == 0:
        raise ErroConversor("A tabela resultante está vazia.")
    linhas, colunas = df.shape
    if linhas > limites.max_linhas:
        raise LimiteExcedido(f"A tabela excede o limite de {limites.max_linhas:,} linhas.")
    if colunas > limites.max_colunas:
        raise LimiteExcedido(f"A tabela excede o limite de {limites.max_colunas:,} colunas.")
    if linhas * colunas > limites.max_celulas:
        raise LimiteExcedido(
            f"A tabela excede o limite de {limites.max_celulas:,} células."
        )
    memoria = int(df.memory_usage(index=True, deep=True).sum())
    if memoria > limites.dataframe_bytes:
        max_mb = limites.dataframe_bytes / (1024 * 1024)
        raise LimiteExcedido(f"O DataFrame excede o limite de {max_mb:.0f} MB em memória.")
    return memoria


def ler_arquivo(
    conteudo: bytes,
    nome_arquivo: str,
    formato: str,
    opcoes: dict[str, Any] | None = None,
    limites: Limites = LIMITES_PADRAO,
) -> tuple[pd.DataFrame, dict[str, Any], list[str]]:
    """Despacha a leitura segura por formato e valida o DataFrame final."""

    opcoes = opcoes or {}
    validar_assinatura(conteudo, nome_arquivo, formato, limites)
    try:
        if formato in {"csv", "txt"}:
            df, info, avisos = ler_texto_tabular(
                conteudo,
                formato,
                opcoes.get("codificacao", "Automático"),
                opcoes.get("delimitador", "Automático"),
            )
        elif formato == "json":
            df, info, avisos = ler_json(conteudo, limites)
        elif formato == "xml":
            df, info, avisos = ler_xml(conteudo, limites)
        elif formato == "xlsx":
            planilhas = inspecionar_xlsx(conteudo, limites)
            planilha = opcoes.get("planilha") or planilhas[0]
            if planilha not in planilhas:
                raise ErroConversor("A planilha selecionada não existe no arquivo.")
            df = pd.read_excel(io.BytesIO(conteudo), sheet_name=planilha, engine="openpyxl")
            info, avisos = {"planilha": planilha, "planilhas": planilhas}, []
        elif formato == "parquet":
            info = inspecionar_parquet(conteudo, limites)
            df = pd.read_parquet(io.BytesIO(conteudo), engine="pyarrow")
            avisos = []
        elif formato == "py":
            valor = extrair_dict_python(conteudo, limites)
            df = normalizar_objeto_tabular(valor)
            info, avisos = {"estrutura": "dicionário Python literal seguro"}, []
        else:
            raise ErroConversor(f"Formato de entrada não suportado: .{formato}.")
    except (ErroConversor, LimiteExcedido):
        raise
    except MemoryError as erro:
        raise LimiteExcedido("A leitura foi interrompida por falta de memória.") from erro
    except Exception as erro:
        raise ErroConversor(f"Não foi possível ler o arquivo: {erro}.") from erro

    memoria = validar_dataframe(df, limites)
    info = {**info, "memoria_bytes": memoria}
    return df, info, avisos


def analisar_tipos(df: pd.DataFrame, formato_saida: str) -> list[str]:
    """Produz avisos de perda de esquema sem bloquear conversões válidas."""

    avisos: list[str] = []
    if formato_saida in {"csv", "txt", "xml"}:
        avisos.append(f"{formato_saida.upper()} não preserva integralmente o esquema de tipos.")
    if formato_saida == "json":
        avisos.append("JSON pode representar datas e números especiais de forma diferente.")

    for coluna in df.columns:
        serie = df[coluna]
        dtype = serie.dtype
        if isinstance(dtype, pd.CategoricalDtype):
            avisos.append(f"A coluna '{coluna}' é categórica e pode perder essa informação.")
        if isinstance(dtype, pd.DatetimeTZDtype):
            if formato_saida == "xlsx":
                raise ErroConversor(
                    f"A coluna '{coluna}' possui timezone, incompatível com XLSX. "
                    "Remova o fuso antes da conversão."
                )
            avisos.append(f"A coluna '{coluna}' possui timezone e pode ser alterada.")
        if pd.api.types.is_object_dtype(dtype):
            amostra = serie.dropna().head(100)
            if any(isinstance(item, (list, dict, tuple, set)) for item in amostra):
                avisos.append(
                    f"A coluna '{coluna}' contém objetos aninhados e pode não ser preservada."
                )
        if pd.api.types.is_float_dtype(dtype):
            valores = pd.to_numeric(serie, errors="coerce")
            if any(math.isinf(valor) for valor in valores.dropna().head(1_000)):
                avisos.append(f"A coluna '{coluna}' contém infinito.")
    return list(dict.fromkeys(avisos))


def _estimar_resultado(df: pd.DataFrame, formato_saida: str) -> int:
    """Estima o custo antes de criar um segundo objeto grande em memória."""

    memoria = int(df.memory_usage(index=True, deep=True).sum())
    fatores = {"csv": 1.5, "txt": 1.5, "json": 2.0, "xml": 2.5, "xlsx": 1.5, "parquet": 0.8}
    return int(memoria * fatores[formato_saida])


def converter_dataframe(
    df: pd.DataFrame,
    formato_saida: str,
    limites: Limites = LIMITES_PADRAO,
) -> tuple[bytes, list[str]]:
    """Converte um DataFrame após validar memória, tipos e tamanho estimado."""

    if formato_saida not in FORMATOS_SAIDA:
        raise ErroConversor(f"Formato de saída não suportado: .{formato_saida}.")
    validar_dataframe(df, limites)
    avisos = analisar_tipos(df, formato_saida)
    estimativa = _estimar_resultado(df, formato_saida)
    if estimativa > limites.resultado_bytes:
        raise LimiteExcedido(
            "A estimativa do arquivo convertido excede o limite de saída."
        )

    try:
        if formato_saida == "csv":
            resultado = df.to_csv(index=False).encode("utf-8")
        elif formato_saida == "json":
            resultado = df.to_json(
                orient="records", indent=4, force_ascii=False, date_format="iso"
            ).encode("utf-8")
        elif formato_saida == "xml":
            resultado = df.to_xml(
                index=False,
                root_name="dados",
                row_name="registro",
                parser="lxml",
                encoding="utf-8",
                xml_declaration=True,
            )
            if isinstance(resultado, str):
                resultado = resultado.encode("utf-8")
        elif formato_saida == "txt":
            resultado = df.to_csv(index=False, sep="\t").encode("utf-8")
        elif formato_saida == "parquet":
            buffer = io.BytesIO()
            df.to_parquet(buffer, index=False, engine="pyarrow")
            resultado = buffer.getvalue()
            buffer.close()
        else:
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Dados")
            resultado = buffer.getvalue()
            buffer.close()
    except MemoryError as erro:
        raise LimiteExcedido("A conversão foi interrompida por falta de memória.") from erro
    except (ErroConversor, LimiteExcedido):
        raise
    except Exception as erro:
        raise ErroConversor(f"Erro durante a conversão para .{formato_saida}: {erro}.") from erro

    if len(resultado) > limites.resultado_bytes:
        max_mb = limites.resultado_bytes / (1024 * 1024)
        raise LimiteExcedido(f"O resultado excede o limite de {max_mb:.0f} MB.")
    return resultado, avisos


def gerar_preview(conteudo: bytes, formato: str, limite: int = 5_000) -> tuple[str, bool]:
    """Gera uma prévia limitada sem alterar o conteúdo destinado ao download."""

    if formato not in {"csv", "json", "xml", "txt"}:
        return "", False
    texto = conteudo.decode("utf-8", errors="replace")
    return texto[:limite], len(texto) > limite
