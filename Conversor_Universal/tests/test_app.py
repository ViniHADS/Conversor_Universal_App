# -*- coding: utf-8 -*-
"""Testes de contrato visual e fumaça da interface Streamlit."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from streamlit.testing.v1 import AppTest


RAIZ = Path(__file__).resolve().parents[1]
CAMINHO_APP = RAIZ / "conversor_Universal.py"
CAMINHO_TEMA = RAIZ / ".streamlit" / "config.toml"


def executar_app_inicial() -> AppTest:
    """Executa a tela inicial uma única vez para os testes de fumaça."""

    return AppTest.from_file(str(CAMINHO_APP)).run(timeout=20)


def test_app_inicial_carrega_sem_excecao() -> None:
    app = executar_app_inicial()
    assert not app.exception
    assert app.title[0].value == (
        ":material/sync_alt: Conversor Universal de Arquivos"
    )
    assert len(app.file_uploader) == 1
    assert app.file_uploader[0].label == "Arquivo para conversão"
    assert any(
        "Envie um arquivo acima" in item.value for item in app.caption
    )
    assert any(
        "Vinicius Araujo © 2026" in item.value for item in app.caption
    )


def test_sidebar_e_fluxo_inicial_estao_presentes() -> None:
    app = executar_app_inicial()
    assert not app.exception
    assert any("Conversor" in item.value for item in app.sidebar.header)
    assert any("Como funciona" in item.value for item in app.sidebar.subheader)
    assert any("Formatos" in item.value for item in app.sidebar.subheader)

    subtitulos = [item.value for item in app.subheader]
    assert ":material/upload_file: Enviar" in subtitulos
    assert ":material/analytics: Analisar" in subtitulos
    assert ":material/sync_alt: Converter" in subtitulos
    # O AppTest 1.61 ainda não expõe st.badge como coleção pública; o contrato
    # dos badges é complementado pela inspeção estática no teste abaixo.
    fonte = CAMINHO_APP.read_text(encoding="utf-8")
    assert "st.badge(" in fonte
    assert '"concluida": ("Concluída"' in fonte
    assert '"atual": ("Etapa atual"' in fonte
    assert '"aguardando": ("Aguardando"' in fonte


def test_fluxo_com_csv_exibe_metricas_e_conversao() -> None:
    app = executar_app_inicial()
    app.file_uploader[0].upload(
        "dados.csv",
        b"nome,idade\nAna,31\n",
        "text/csv",
    ).run(timeout=20)

    assert not app.exception
    assert app.button[0].label == "Carregar e analisar"
    app.button[0].click().run(timeout=20)

    assert not app.exception
    assert len(app.dataframe) == 1
    assert [(item.label, item.value) for item in app.metric] == [
        ("Linhas", "1"),
        ("Colunas", "2"),
        ("Células", "2"),
    ]
    assert any(item.label == "Converter" for item in app.button)
    assert any(
        item.proto.body == "Arquivo analisado com sucesso."
        for item in app.get("toast")
    )


def test_fluxo_com_csv_converte_e_disponibiliza_download() -> None:
    app = executar_app_inicial()
    app.file_uploader[0].upload(
        "dados.csv",
        b"nome,idade\nAna,31\n",
        "text/csv",
    ).run(timeout=20)
    app.button[0].click().run(timeout=20)
    app.button[1].click().run(timeout=20)

    assert not app.exception
    assert len(app.download_button) == 1
    assert app.download_button[0].label == "Baixar dados.csv"
    assert any(
        "Conversão para .CSV concluída." == item.proto.body
        for item in app.get("toast")
    )


def test_rodape_permanece_literal() -> None:
    fonte = CAMINHO_APP.read_text(encoding="utf-8")
    trecho = '''st.caption(
        "Conversor Universal de Arquivos • "
        "Python + Streamlit + Pandas +"
        "Vinicius Araujo © 2026 • Projeto Prático de Eng. de Software."
    )'''
    assert trecho in fonte


def test_tema_permanece_exatamente_com_a_paleta_aprovada() -> None:
    configuracao = tomllib.loads(CAMINHO_TEMA.read_text(encoding="utf-8"))
    assert configuracao["server"]["maxUploadSize"] == 25
    assert configuracao["theme"] == {
        "primaryColor": "#047857",
        "backgroundColor": "#F9F9FB",
        "secondaryBackgroundColor": "#F1F5F9",
        "textColor": "#0F172A",
        "borderColor": "#CBD5E1",
        "linkColor": "#047857",
    }


def test_interface_usa_componentes_nativos_e_material_symbols() -> None:
    fonte = CAMINHO_APP.read_text(encoding="utf-8")
    assert ":material/sync_alt:" in fonte
    assert ":material/upload_file:" in fonte
    assert ":material/analytics:" in fonte
    assert ":material/check_circle:" in fonte
    assert "st.status(" in fonte
    assert "st.toast(" in fonte
    assert "st.metric(" in fonte
    assert "border=True" in fonte

    padroes_proibidos = (
        "use_" + "container_width",
        "unsafe_" + "allow_html=True",
        "st.divider(",
        "st.info(",
        "st.success(",
        "<style",
    )
    assert not any(padrao in fonte for padrao in padroes_proibidos)


def test_interface_nao_possui_emojis_visuais() -> None:
    fonte = CAMINHO_APP.read_text(encoding="utf-8")
    intervalos_emoji = (
        (0x1F000, 0x1FAFF),
        (0x2600, 0x27BF),
    )
    encontrados = [
        caractere
        for caractere in fonte
        if any(inicio <= ord(caractere) <= fim for inicio, fim in intervalos_emoji)
    ]
    assert encontrados == []


def test_sidebar_nao_executa_operacoes_caras() -> None:
    arvore = ast.parse(CAMINHO_APP.read_text(encoding="utf-8"))
    funcao_sidebar = next(
        no
        for no in arvore.body
        if isinstance(no, ast.FunctionDef) and no.name == "renderizar_sidebar"
    )
    nomes_chamados = {
        no.func.id
        for no in ast.walk(funcao_sidebar)
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
    }
    atributos_chamados = {
        no.func.attr
        for no in ast.walk(funcao_sidebar)
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute)
    }
    proibidas = {
        "ler_arquivo",
        "ler_arquivo_em_cache",
        "converter_dataframe",
        "validar_assinatura",
        "getvalue",
        "read",
        "read_bytes",
    }
    assert nomes_chamados.isdisjoint(proibidas)
    assert atributos_chamados.isdisjoint(proibidas)


def test_sem_api_depreciada_ou_mojibake() -> None:
    # As palavras são montadas em partes para o teste não identificar a própria
    # especificação como ocorrência de código depreciado ou texto corrompido.
    padroes_invalidos = (
        "use_" + "container_width",
        chr(0xF0),
        chr(0xFFFD),
        "saí" + chr(0xAD) + "da",
    )
    caminhos = [
        RAIZ / "conversor_Universal.py",
        RAIZ / "conversor_core.py",
        RAIZ / "files" / "dict.py",
        RAIZ / ".streamlit" / "config.toml",
        RAIZ / "requirements.txt",
        RAIZ / "requirements-dev.txt",
    ]
    for caminho in caminhos:
        texto = caminho.read_text(encoding="utf-8")
        assert not any(padrao in texto for padrao in padroes_invalidos)
