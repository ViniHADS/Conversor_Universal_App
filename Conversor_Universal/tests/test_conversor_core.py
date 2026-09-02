# -*- coding: utf-8 -*-
"""Testes do núcleo: formatos, segurança, limites e conversões."""

from __future__ import annotations

import ast
import io
import json
from pathlib import Path

import pandas as pd
import pytest

from conversor_core import (
    FORMATOS_SAIDA,
    MIME_TYPES,
    ErroConversor,
    LimiteExcedido,
    Limites,
    calcular_sha256,
    converter_dataframe,
    detectar_codificacao,
    extrair_dict_python,
    gerar_preview,
    inspecionar_parquet,
    inspecionar_xlsx,
    ler_arquivo,
    normalizar_objeto_tabular,
    validar_assinatura,
    validar_dataframe,
    validar_tamanho_upload,
    validar_xml_seguro,
)


@pytest.fixture
def tabela() -> pd.DataFrame:
    """Tabela pequena compartilhada pelos testes de conversão."""

    return pd.DataFrame(
        {
            "nome": ["Ana", "Carlos"],
            "idade": [31, 28],
            "ativo": [True, False],
        }
    )


@pytest.fixture
def xlsx_multiplas_planilhas() -> bytes:
    """Workbook real em memória, sem depender do sistema de arquivos."""

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name="Primeira", index=False)
        pd.DataFrame({"b": [2]}).to_excel(writer, sheet_name="Segunda", index=False)
    return buffer.getvalue()


@pytest.fixture
def parquet_valido(tabela: pd.DataFrame) -> bytes:
    """Parquet real pequeno usado na inspeção e leitura."""

    buffer = io.BytesIO()
    tabela.to_parquet(buffer, index=False, engine="pyarrow")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("conteudo", "nome", "opcoes", "colunas"),
    [
        (b"nome,idade\nAna,31\n", "dados.csv", {}, ["nome", "idade"]),
        (
            b"\xef\xbb\xbfnome;idade\nAna;31\n",
            "dados.csv",
            {"delimitador": "Ponto e vírgula (;)"},
            ["nome", "idade"],
        ),
        (
            "nome;cidade\nJoão;São Paulo\n".encode("cp1252"),
            "dados.csv",
            {
                "codificacao": "Windows-1252",
                "delimitador": "Ponto e vírgula (;)",
            },
            ["nome", "cidade"],
        ),
        (
            b"nome\tidade\nAna\t31\n",
            "dados.txt",
            {"delimitador": "Tabulação"},
            ["nome", "idade"],
        ),
        (
            b"nome|idade\nAna|31\n",
            "dados.txt",
            {"delimitador": "Barra vertical (|)"},
            ["nome", "idade"],
        ),
    ],
)
def test_le_textos_tabulares(
    conteudo: bytes,
    nome: str,
    opcoes: dict[str, str],
    colunas: list[str],
) -> None:
    formato = nome.rsplit(".", 1)[1]
    df, info, _ = ler_arquivo(conteudo, nome, formato, opcoes)
    assert df.columns.tolist() == colunas
    assert info["delimitador"] in {",", ";", "TAB", "|"}


@pytest.mark.parametrize(
    ("valor", "linhas"),
    [
        ({"nome": "Ana", "idade": 31}, 1),
        ({"nome": ["Ana", "Carlos"], "idade": [31, 28]}, 2),
        ([{"nome": "Ana"}, {"nome": "Carlos"}], 2),
        ({"usuarios": [{"nome": "Ana"}, {"nome": "Carlos"}]}, 2),
        ({"usuario": {"nome": "Ana", "endereco": {"cidade": "Recife"}}}, 1),
    ],
)
def test_normaliza_dict_e_lista(valor: object, linhas: int) -> None:
    df = normalizar_objeto_tabular(valor)
    assert len(df) == linhas
    assert not df.empty


def test_rejeita_listas_de_tamanhos_diferentes() -> None:
    with pytest.raises(ErroConversor, match="tamanhos diferentes"):
        normalizar_objeto_tabular({"a": [1, 2], "b": [3]})


@pytest.mark.parametrize(
    "codigo",
    [
        b'{"nome": "Ana", "idade": 31}',
        b'dados = {"nome": "Ana", "idade": 31}',
    ],
)
def test_extrai_dict_python_seguro(codigo: bytes) -> None:
    assert extrair_dict_python(codigo)["nome"] == "Ana"


@pytest.mark.parametrize(
    "codigo",
    [
        b"import os\ndados = {}",
        b"print('ataque')",
        b"def ataque():\n    return {}",
        b"class Ataque:\n    pass",
        b"dados = dict(nome='Ana')",
        b"dados = {x: x for x in range(3)}",
        b"dados = objeto.valor",
        b"dados = {}\nopen('/tmp/efeito', 'w')",
        b"dados = []",
        b"",
    ],
)
def test_rejeita_python_executavel(codigo: bytes) -> None:
    with pytest.raises(ErroConversor):
        extrair_dict_python(codigo)


def test_python_invalido_nao_cria_efeito_colateral(tmp_path: Path) -> None:
    alvo = tmp_path / "nao_deve_existir.txt"
    codigo = f"dados = {{}}\nopen({str(alvo)!r}, 'w').write('ataque')".encode()
    with pytest.raises(ErroConversor):
        extrair_dict_python(codigo)
    assert not alvo.exists()


@pytest.mark.parametrize(
    "conteudo",
    [
        b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><foo>&xxe;</foo>',
        b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "https://example.com">]><foo>&xxe;</foo>',
        b"<!DOCTYPE lolz [<!ENTITY lol \"lol\"><!ENTITY lol2 \"&lol;&lol;\">]><lolz>&lol2;</lolz>",
    ],
)
def test_rejeita_xml_com_dtd_ou_entidade(conteudo: bytes) -> None:
    with pytest.raises(ErroConversor, match="DTD|entidade"):
        validar_xml_seguro(conteudo)


def test_rejeita_xml_profundo() -> None:
    conteudo = ("<a>" * 45 + "valor" + "</a>" * 45).encode()
    with pytest.raises(LimiteExcedido, match="profundidade"):
        validar_xml_seguro(conteudo, Limites(max_profundidade=20))


def test_le_xml_valido_com_namespace() -> None:
    xml = b"""<?xml version="1.0"?>
    <dados xmlns="urn:teste">
      <registro><nome>Ana</nome><idade>31</idade></registro>
      <registro><nome>Carlos</nome><idade>28</idade></registro>
    </dados>"""
    df, info, _ = ler_arquivo(xml, "dados.xml", "xml")
    assert df["nome"].tolist() == ["Ana", "Carlos"]
    assert info["elemento_raiz"] == "dados"


@pytest.mark.parametrize(
    ("conteudo", "nome"),
    [
        (b"nao e zip", "falso.xlsx"),
        (b"PAR1dados invalidos", "falso.parquet"),
        (b"{json quebrado", "falso.json"),
        (b"texto comum", "falso.xml"),
        (b"print('x')", "falso.py"),
    ],
)
def test_rejeita_assinatura_incompativel(conteudo: bytes, nome: str) -> None:
    with pytest.raises(ErroConversor):
        validar_assinatura(conteudo, nome)


def test_inspeciona_xlsx_e_escolhe_planilha(xlsx_multiplas_planilhas: bytes) -> None:
    assert inspecionar_xlsx(xlsx_multiplas_planilhas) == ["Primeira", "Segunda"]
    df, info, _ = ler_arquivo(
        xlsx_multiplas_planilhas,
        "dados.xlsx",
        "xlsx",
        {"planilha": "Segunda"},
    )
    assert df.columns.tolist() == ["b"]
    assert info["planilha"] == "Segunda"


def test_rejeita_xlsx_com_taxa_suspeita() -> None:
    buffer = io.BytesIO()
    import zipfile

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as arquivo:
        arquivo.writestr("[Content_Types].xml", "x")
        arquivo.writestr("xl/workbook.xml", "x")
        arquivo.writestr("xl/worksheets/sheet1.xml", "0" * 2_000_000)
    with pytest.raises(LimiteExcedido, match="compressão|ZIP bomb"):
        inspecionar_xlsx(buffer.getvalue(), Limites(xlsx_taxa_compressao=10))


def test_inspeciona_e_le_parquet(parquet_valido: bytes) -> None:
    info = inspecionar_parquet(parquet_valido)
    assert info["linhas_estimadas"] == 2
    df, _, _ = ler_arquivo(parquet_valido, "dados.parquet", "parquet")
    assert df["nome"].tolist() == ["Ana", "Carlos"]


def test_le_json_simples_e_aninhado() -> None:
    simples, _, _ = ler_arquivo(
        json.dumps([{"nome": "Ana"}, {"nome": "Carlos"}]).encode(),
        "dados.json",
        "json",
    )
    aninhado, _, _ = ler_arquivo(
        json.dumps({"usuario": {"nome": "Ana", "endereco": {"cidade": "Recife"}}}).encode(),
        "dados.json",
        "json",
    )
    assert len(simples) == 2
    assert aninhado.loc[0, "usuario.endereco.cidade"] == "Recife"


@pytest.mark.parametrize(
    "conteudo",
    [b"", b"{}", b"[]", b"{json quebrado"],
)
def test_rejeita_json_vazio_ou_invalido(conteudo: bytes) -> None:
    with pytest.raises(ErroConversor):
        ler_arquivo(conteudo, "dados.json", "json")


@pytest.mark.parametrize(
    ("conteudo", "nome", "opcoes"),
    [
        (b"", "dados.csv", {}),
        (b"texto sem tabela", "dados.txt", {"delimitador": "Tabulação"}),
        (b"a,b\n1,2,3\n", "dados.csv", {}),
        (b"\xff\xfe\x00", "dados.csv", {"codificacao": "UTF-8"}),
    ],
)
def test_rejeita_texto_vazio_irregular_ou_invalido(
    conteudo: bytes,
    nome: str,
    opcoes: dict[str, str],
) -> None:
    with pytest.raises(ErroConversor):
        ler_arquivo(conteudo, nome, nome.rsplit(".", 1)[1], opcoes)


def test_detecta_cp1252_com_aviso_ou_confianca() -> None:
    codec, confianca, avisos = detectar_codificacao("ação útil".encode("cp1252"))
    assert codec
    assert 0 <= confianca <= 1
    assert isinstance(avisos, list)


@pytest.mark.parametrize("formato", FORMATOS_SAIDA)
def test_converte_para_todos_formatos(tabela: pd.DataFrame, formato: str) -> None:
    conteudo, avisos = converter_dataframe(tabela, formato)
    assert conteudo
    assert MIME_TYPES[formato]
    assert isinstance(avisos, list)

    if formato == "csv":
        reaberto = pd.read_csv(io.BytesIO(conteudo))
    elif formato == "json":
        reaberto = pd.read_json(io.BytesIO(conteudo))
    elif formato == "xml":
        validar_xml_seguro(conteudo)
        reaberto = pd.read_xml(io.BytesIO(conteudo), parser="lxml")
    elif formato == "txt":
        reaberto = pd.read_csv(io.BytesIO(conteudo), sep="\t")
    elif formato == "xlsx":
        reaberto = pd.read_excel(io.BytesIO(conteudo), engine="openpyxl")
    else:
        reaberto = pd.read_parquet(io.BytesIO(conteudo), engine="pyarrow")
    assert reaberto.shape == tabela.shape
    assert reaberto.columns.tolist() == tabela.columns.tolist()


def test_avisa_perda_de_tipos_e_bloqueia_timezone() -> None:
    df = pd.DataFrame(
        {
            "quando": pd.to_datetime(["2026-01-01"], utc=True),
            "categoria": pd.Series(["A"], dtype="category"),
        }
    )
    _, avisos = converter_dataframe(df, "csv")
    assert any("esquema" in aviso for aviso in avisos)
    assert any("timezone" in aviso for aviso in avisos)
    with pytest.raises(ErroConversor, match="timezone"):
        converter_dataframe(df, "xlsx")


def test_limites_sao_injetaveis(tabela: pd.DataFrame) -> None:
    with pytest.raises(LimiteExcedido, match="upload"):
        validar_tamanho_upload(4, Limites(upload_bytes=3))
    with pytest.raises(LimiteExcedido, match="linhas"):
        validar_dataframe(tabela, Limites(max_linhas=1))
    with pytest.raises(LimiteExcedido, match="células"):
        validar_dataframe(tabela, Limites(max_celulas=2))
    with pytest.raises(LimiteExcedido, match="estimativa"):
        converter_dataframe(tabela, "json", Limites(resultado_bytes=1))


def test_hash_e_preview_nao_alteram_conteudo() -> None:
    conteudo = b"a,b\n1,2\n" * 1_000
    assert calcular_sha256(conteudo) == calcular_sha256(conteudo)
    preview, truncado = gerar_preview(conteudo, "csv", limite=20)
    assert len(preview) == 20
    assert truncado
    assert len(conteudo) > len(preview)


def test_aplicacao_nao_chama_funcoes_proibidas() -> None:
    """Diferencia strings de ataque nos testes de chamadas reais na aplicação."""

    raiz = Path(__file__).resolve().parents[1]
    proibidas = {"exec", "eval", "compile", "runpy", "subprocess", "__import__"}
    for nome in ("conversor_Universal.py", "conversor_core.py"):
        arvore = ast.parse((raiz / nome).read_text(encoding="utf-8"))
        chamadas = {
            no.func.id
            for no in ast.walk(arvore)
            if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
        }
        assert chamadas.isdisjoint(proibidas)
