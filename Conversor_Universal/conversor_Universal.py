# -*- coding: utf-8 -*-
"""Interface Streamlit do Conversor Universal de Arquivos.

A interface coordena upload, formulários, estado e mensagens. Validação,
leitura e conversão ficam em conversor_core para serem testadas sem abrir
o aplicativo e sem executar arquivos enviados.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
import streamlit as st

from conversor_core import (
    CODIFICACOES,
    DELIMITADORES,
    FORMATOS_ENTRADA,
    FORMATOS_SAIDA,
    LIMITES_PADRAO,
    MIME_TYPES,
    ErroConversor,
    LimiteExcedido,
    calcular_sha256,
    converter_dataframe,
    gerar_preview,
    inspecionar_xlsx,
    ler_arquivo,
    obter_extensao,
    validar_assinatura,
    validar_tamanho_upload,
)


# Esta precisa ser a primeira chamada Streamlit para configurar a página.
st.set_page_config(
    page_title="Conversor Universal de Arquivos",
    page_icon=":material/sync_alt:",
    layout="wide",
)


# TTL e quantidade máxima impedem crescimento indefinido do cache global.
@st.cache_data(ttl="10m", max_entries=3, show_spinner=False)
def ler_arquivo_em_cache(
    conteudo: bytes,
    nome_arquivo: str,
    formato: str,
    opcoes_serializadas: tuple[tuple[str, Any], ...],
) -> tuple[pd.DataFrame, dict[str, Any], list[str]]:
    """Lê um arquivo usando opções imutáveis como chave do cache."""

    return ler_arquivo(conteudo, nome_arquivo, formato, dict(opcoes_serializadas))


def inicializar_estado() -> None:
    """Inicializa em um único local todo o estado privado da sessão."""

    valores = {
        "arquivo_hash": None,
        "arquivo_nome": None,
        "formato_entrada": None,
        "dados": None,
        "info_leitura": {},
        "avisos_leitura": [],
        "opcoes_leitura": {},
        "resultado": None,
        "formato_saida": None,
        "nome_saida": None,
        "avisos_conversao": [],
    }
    for chave, valor in valores.items():
        st.session_state.setdefault(chave, valor)


def limpar_estado_arquivo() -> None:
    """Descarta DataFrame, bytes e metadados do upload anterior."""

    st.session_state.dados = None
    st.session_state.info_leitura = {}
    st.session_state.avisos_leitura = []
    st.session_state.opcoes_leitura = {}
    st.session_state.resultado = None
    st.session_state.formato_saida = None
    st.session_state.nome_saida = None
    st.session_state.avisos_conversao = []


def formatar_inteiro(valor: int) -> str:
    """Apresenta inteiros com separador de milhares adequado ao português."""

    return f"{valor:,}".replace(",", ".")


def formatar_tamanho(tamanho_bytes: int) -> str:
    """Formata bytes em KB ou MB sem modificar o conteúdo mantido na sessão."""

    if tamanho_bytes >= 1024 * 1024:
        return f"{tamanho_bytes / (1024 * 1024):.2f} MB"
    return f"{tamanho_bytes / 1024:.2f} KB"


def nome_formato(formato: str) -> str:
    """Converte a extensão interna em um nome curto para a interface."""

    return "Python dict" if formato == "py" else formato.upper()


def renderizar_badges_formatos() -> None:
    """Mostra formatos de entrada e saída como badges nativos e responsivos."""

    st.caption("Formatos de entrada", text_alignment="center")
    with st.container(
        horizontal=True,
        horizontal_alignment="center",
        vertical_alignment="center",
    ):
        for formato in FORMATOS_ENTRADA:
            st.badge(nome_formato(formato), color="primary")

    st.caption("Formatos de saída", text_alignment="center")
    with st.container(
        horizontal=True,
        horizontal_alignment="center",
        vertical_alignment="center",
    ):
        for formato in FORMATOS_SAIDA:
            st.badge(nome_formato(formato), color="gray")


def renderizar_cabecalho() -> None:
    """Apresenta título, propósito e formatos sem concentrar detalhes técnicos."""

    with st.container(horizontal_alignment="center", gap="small"):
        st.title(
            ":material/sync_alt: Conversor Universal de Arquivos",
            text_alignment="center",
        )
        st.markdown(
            "Converta dados com segurança entre formatos tabulares usando "
            "**Python, Streamlit e Pandas**.",
            text_alignment="center",
        )
        renderizar_badges_formatos()


def determinar_estado_fluxo(arquivo_presente: bool) -> tuple[str, str, str]:
    """Deriva o estado visual das etapas sem iniciar leitura ou conversão."""

    if not arquivo_presente:
        return "atual", "aguardando", "aguardando"
    if st.session_state.resultado is not None:
        return "concluida", "concluida", "concluida"
    if st.session_state.dados is not None:
        return "concluida", "concluida", "atual"
    return "concluida", "atual", "aguardando"


def renderizar_fluxo_etapas(arquivo_presente: bool) -> None:
    """Renderiza três cards de orientação usando somente o estado da sessão."""

    estados = determinar_estado_fluxo(arquivo_presente)
    configuracao_estados = {
        "concluida": ("Concluída", ":material/check_circle:", "green"),
        "atual": ("Etapa atual", ":material/play_circle:", "primary"),
        "aguardando": ("Aguardando", ":material/schedule:", "gray"),
    }
    etapas = (
        (
            ":material/upload_file:",
            "Enviar",
            "Selecione um arquivo compatível.",
        ),
        (
            ":material/analytics:",
            "Analisar",
            "Valide e prepare os dados.",
        ),
        (
            ":material/sync_alt:",
            "Converter",
            "Escolha o formato e baixe o resultado.",
        ),
    )

    colunas = st.columns(3, gap="small", vertical_alignment="top", border=True)
    for indice, (coluna, etapa, estado) in enumerate(
        zip(colunas, etapas, estados, strict=True), start=1
    ):
        icone, titulo, descricao = etapa
        rotulo_estado, icone_estado, cor_estado = configuracao_estados[estado]
        with coluna:
            st.caption(f"Etapa {indice}")
            st.subheader(f"{icone} {titulo}")
            st.caption(descricao)
            st.badge(rotulo_estado, icon=icone_estado, color=cor_estado)


def renderizar_sidebar() -> None:
    """Exibe somente informações globais, sem ler ou converter o upload."""

    limites = LIMITES_PADRAO
    with st.sidebar:
        st.header(":material/sync_alt: Conversor")
        st.caption(
            "Converta arquivos tabulares com validações de segurança e limites "
            "preventivos de memória."
        )

        st.subheader(":material/route: Como funciona")
        st.caption(":material/upload_file: 1. Envie um arquivo compatível.")
        st.caption(":material/analytics: 2. Carregue e analise os dados.")
        st.caption(":material/sync_alt: 3. Converta e faça o download.")

        st.subheader(":material/folder_open: Formatos")
        st.caption("Entrada")
        with st.container(horizontal=True, gap="small"):
            for formato in FORMATOS_ENTRADA:
                st.badge(nome_formato(formato), color="primary")
        st.caption("Saída")
        with st.container(horizontal=True, gap="small"):
            for formato in FORMATOS_SAIDA:
                st.badge(nome_formato(formato), color="gray")

        with st.expander(
            "Limites de segurança",
            icon=":material/memory:",
            expanded=False,
        ):
            st.caption(
                f"Upload: {limites.upload_bytes // (1024 * 1024)} MB"
            )
            st.caption(
                f"DataFrame: {limites.dataframe_bytes // (1024 * 1024)} MB"
            )
            st.caption(
                f"Resultado: {limites.resultado_bytes // (1024 * 1024)} MB"
            )
            st.caption(f"Linhas: {formatar_inteiro(limites.max_linhas)}")
            st.caption(f"Colunas: {formatar_inteiro(limites.max_colunas)}")
            st.caption(f"Células: {formatar_inteiro(limites.max_celulas)}")
            st.caption(
                "XLSX descompactado: "
                f"{limites.xlsx_descompactado_bytes // (1024 * 1024)} MB"
            )
            st.caption(
                "Parquet estimado: "
                f"{limites.parquet_estimado_bytes // (1024 * 1024)} MB"
            )
            st.caption(
                "Profundidade máxima de JSON, Python dict e XML: "
                f"{limites.max_profundidade} níveis"
            )

        with st.expander(
            "Privacidade e arquivos Python",
            icon=":material/shield:",
            expanded=False,
        ):
            st.caption(
                "Os arquivos são processados na sessão do aplicativo e não são "
                "enviados a serviços externos."
            )
            st.caption(
                "Arquivos Python nunca são executados. Somente um dicionário "
                "literal seguro pode ser extraído por meio da AST."
            )


def renderizar_metadados_arquivo(
    nome_arquivo: str,
    formato: str,
    tamanho_bytes: int,
    info: dict[str, Any],
) -> None:
    """Agrupa metadados básicos e decisões de leitura em um card compacto."""

    with st.container(border=True):
        st.markdown(":material/description: **Arquivo selecionado**")
        colunas = st.columns(3, gap="small")
        colunas[0].caption("Nome")
        colunas[0].markdown(f"**{nome_arquivo}**")
        colunas[1].caption("Formato")
        colunas[1].markdown(f"**{nome_formato(formato)}**")
        colunas[2].caption("Tamanho")
        colunas[2].markdown(f"**{formatar_tamanho(tamanho_bytes)}**")

        detalhes: list[str] = []
        if info.get("codificacao"):
            detalhes.append(
                f":material/text_fields: Codificação: **{info['codificacao']}**"
            )
        if info.get("delimitador"):
            detalhes.append(
                f":material/vertical_split: Delimitador: **{info['delimitador']}**"
            )
        if info.get("planilha"):
            detalhes.append(
                f":material/table_view: Planilha: **{info['planilha']}**"
            )
        if info.get("estrutura"):
            detalhes.append(
                f":material/account_tree: Estrutura: **{info['estrutura']}**"
            )
        if detalhes:
            with st.container(horizontal=True, gap="small"):
                for detalhe in detalhes:
                    st.caption(detalhe)


def renderizar_metricas(df: pd.DataFrame) -> None:
    """Apresenta dimensões da tabela em três cards de métricas responsivos."""

    linhas, colunas = df.shape
    celulas = linhas * colunas
    with st.container(horizontal=True, gap="small"):
        st.metric(
            "Linhas",
            formatar_inteiro(linhas),
            icon=":material/table_rows:",
            border=True,
        )
        st.metric(
            "Colunas",
            formatar_inteiro(colunas),
            icon=":material/view_column:",
            border=True,
        )
        st.metric(
            "Células",
            formatar_inteiro(celulas),
            icon=":material/grid_view:",
            border=True,
        )


def mostrar_rodape() -> None:
    """Mantém o rodapé original literalmente."""

    st.caption(
        "Conversor Universal de Arquivos • "
        "Python + Streamlit + Pandas • "
        "Vinicius Araujo © 2026 • Projeto Prático de Eng. de Software."
    )


def mostrar_preview_convertido(conteudo: bytes, formato: str) -> None:
    """Exibe amostra limitada sem alterar os bytes do download."""

    if formato in {"csv", "json", "xml", "txt"}:
        texto, truncado = gerar_preview(conteudo, formato)
        linguagem = formato if formato in {"json", "xml"} else "text"
        st.code(texto, language=linguagem)
        if truncado:
            st.caption(
                "A prévia foi limitada a 5.000 caracteres; o download contém "
                "o arquivo completo."
            )
        return

    tamanho_kb = len(conteudo) / 1024
    formato_exibicao = "Excel" if formato == "xlsx" else "Parquet"
    with st.container(border=True):
        st.caption(
            ":material/description: "
            f"Arquivo {formato_exibicao} binário pronto para download. "
            f"Tamanho: {tamanho_kb:.2f} KB."
        )


inicializar_estado()
renderizar_sidebar()
renderizar_cabecalho()

# O placeholder permite mostrar os cards antes do uploader e calcular o estado
# correto somente depois que o widget e as ações desta execução forem processados.
fluxo_etapas = st.empty()

st.subheader(":material/upload_file: Enviar arquivo")
st.caption(
    "Selecione um arquivo de até 25 MB. A leitura somente ocorrerá após sua "
    "confirmação."
)

# Criar o DataFrame depende da confirmação do formulário de leitura.
arquivo = st.file_uploader(
    "Arquivo para conversão",
    type=list(FORMATOS_ENTRADA),
    help="Formatos aceitos: CSV, JSON, XML, XLSX, TXT, Parquet e Python dict.",
)

if arquivo is None:
    with fluxo_etapas.container():
        renderizar_fluxo_etapas(arquivo_presente=False)
    st.caption(":material/upload_file: Envie um arquivo acima para começar.")
    mostrar_rodape()
    st.stop()

try:
    # Valida antes de getvalue para evitar uma cópia antecipada de upload excessivo.
    validar_tamanho_upload(arquivo.size)
    nome_original = arquivo.name
    extensao_entrada = obter_extensao(nome_original)
    conteudo = arquivo.getvalue()
    validar_tamanho_upload(len(conteudo))
    hash_atual = calcular_sha256(conteudo)

    if hash_atual != st.session_state.arquivo_hash:
        limpar_estado_arquivo()
        st.session_state.arquivo_hash = hash_atual
        st.session_state.arquivo_nome = nome_original
        st.session_state.formato_entrada = extensao_entrada

    # CSV/TXT dependem das escolhas de encoding e delimitador do formulário.
    metadados_iniciais: dict[str, Any] = {}
    if extensao_entrada not in {"csv", "txt"}:
        metadados_iniciais = validar_assinatura(
            conteudo, nome_original, extensao_entrada
        )
except (ErroConversor, LimiteExcedido) as erro:
    with fluxo_etapas.container():
        renderizar_fluxo_etapas(arquivo_presente=False)
    st.error(str(erro), icon=":material/error:")
    mostrar_rodape()
    st.stop()
except MemoryError:
    with fluxo_etapas.container():
        renderizar_fluxo_etapas(arquivo_presente=False)
    st.error(
        "O upload foi interrompido por falta de memória.",
        icon=":material/error:",
    )
    mostrar_rodape()
    st.stop()

# O placeholder mantém os metadados acima do formulário, inclusive quando novas
# decisões de leitura ficam disponíveis após o envio do formulário nesta execução.
metadados_arquivo = st.empty()

# Widgets no formulário não disparam leitura até “Carregar e analisar”.
with st.form("formulario_leitura", border=True):
    st.subheader(":material/tune: Opções de leitura")
    opcoes: dict[str, Any] = {}
    if extensao_entrada in {"csv", "txt"}:
        opcoes["codificacao"] = st.selectbox(
            "Codificação",
            list(CODIFICACOES),
            help="Use Automático ou escolha quando a detecção falhar.",
        )
        indice_delimitador = 3 if extensao_entrada == "txt" else 0
        opcoes["delimitador"] = st.selectbox(
            "Delimitador",
            list(DELIMITADORES),
            index=indice_delimitador,
        )
    elif extensao_entrada == "xlsx":
        planilhas = metadados_iniciais.get("planilhas") or inspecionar_xlsx(conteudo)
        opcoes["planilha"] = st.selectbox("Planilha", planilhas)
    else:
        st.caption("Este formato não exige opções adicionais de leitura.")

    carregar = st.form_submit_button(
        "Carregar e analisar",
        type="primary",
        icon=":material/analytics:",
    )

if carregar:
    status_leitura = st.status(
        "Validando e analisando o arquivo...",
        expanded=True,
        state="running",
    )
    try:
        status_leitura.write(
            ":material/fact_check: Validando assinatura, segurança e opções de leitura."
        )
        df_lido, info, avisos = ler_arquivo_em_cache(
            conteudo,
            nome_original,
            extensao_entrada,
            tuple(sorted(opcoes.items())),
        )
        status_leitura.write(
            ":material/memory: Dados lidos e limites do DataFrame validados."
        )
        st.session_state.dados = df_lido
        st.session_state.info_leitura = info
        st.session_state.avisos_leitura = avisos
        st.session_state.opcoes_leitura = opcoes
        st.session_state.resultado = None
        st.session_state.formato_saida = None
        st.session_state.nome_saida = None
        st.session_state.avisos_conversao = []
        status_leitura.write(
            ":material/visibility: Prévia e métricas preparadas para exibição."
        )
        status_leitura.update(
            label="Arquivo analisado com sucesso.",
            state="complete",
            expanded=False,
        )
        st.toast(
            "Arquivo analisado com sucesso.",
            icon=":material/check_circle:",
        )
    except (ErroConversor, LimiteExcedido) as erro:
        status_leitura.update(
            label="Não foi possível analisar o arquivo.",
            state="error",
            expanded=False,
        )
        st.error(str(erro), icon=":material/error:")
    except MemoryError:
        status_leitura.update(
            label="A análise foi interrompida por falta de memória.",
            state="error",
            expanded=False,
        )
        st.error(
            "A leitura foi interrompida por falta de memória.",
            icon=":material/error:",
        )

with metadados_arquivo.container():
    renderizar_metadados_arquivo(
        nome_original,
        extensao_entrada,
        len(conteudo),
        st.session_state.info_leitura,
    )


df = st.session_state.dados
if df is not None:
    for aviso in st.session_state.avisos_leitura:
        st.warning(aviso, icon=":material/warning:")

    st.subheader(":material/visibility: Prévia dos dados")
    st.dataframe(df, width="stretch", height=300, key="preview_dados")
    renderizar_metricas(df)

    with st.form("formulario_conversao", border=True):
        st.subheader(":material/sync_alt: Converter e baixar")
        formato_saida_escolhido = st.selectbox(
            "Formato de saída",
            list(FORMATOS_SAIDA),
            format_func=lambda formato: f".{formato.upper()}",
        )
        converter = st.form_submit_button(
            "Converter",
            type="primary",
            icon=":material/sync_alt:",
        )

    if converter:
        status_conversao = st.status(
            f"Convertendo para .{formato_saida_escolhido.upper()}...",
            expanded=True,
            state="running",
        )
        try:
            status_conversao.write(
                ":material/data_check: Analisando tipos e compatibilidade do formato."
            )
            resultado, avisos = converter_dataframe(
                df, formato_saida_escolhido
            )
            status_conversao.write(
                ":material/fact_check: Resultado gerado e limite de saída validado."
            )
            nome_sem_extensao = os.path.splitext(nome_original)[0]
            st.session_state.resultado = resultado
            st.session_state.formato_saida = formato_saida_escolhido
            st.session_state.nome_saida = (
                f"{nome_sem_extensao}.{formato_saida_escolhido}"
            )
            st.session_state.avisos_conversao = avisos
            status_conversao.write(
                ":material/download: Prévia e download preparados."
            )
            status_conversao.update(
                label=(
                    f"Conversão para .{formato_saida_escolhido.upper()} concluída."
                ),
                state="complete",
                expanded=False,
            )
            st.toast(
                f"Conversão para .{formato_saida_escolhido.upper()} concluída.",
                icon=":material/check_circle:",
            )
        except (ErroConversor, LimiteExcedido) as erro:
            status_conversao.update(
                label="Não foi possível concluir a conversão.",
                state="error",
                expanded=False,
            )
            st.error(str(erro), icon=":material/error:")
        except MemoryError:
            status_conversao.update(
                label="A conversão foi interrompida por falta de memória.",
                state="error",
                expanded=False,
            )
            st.error(
                "A conversão foi interrompida por falta de memória.",
                icon=":material/error:",
            )

    if st.session_state.resultado is not None:
        formato_saida = st.session_state.formato_saida
        for aviso in st.session_state.avisos_conversao:
            st.warning(aviso, icon=":material/warning:")

        st.subheader(
            f":material/output: Prévia do arquivo .{formato_saida.upper()}"
        )
        mostrar_preview_convertido(st.session_state.resultado, formato_saida)

        st.subheader(":material/download: Baixar arquivo")
        st.download_button(
            label=f"Baixar {st.session_state.nome_saida}",
            data=st.session_state.resultado,
            file_name=st.session_state.nome_saida,
            mime=MIME_TYPES[formato_saida],
            width="stretch",
            icon=":material/download:",
            on_click="ignore",
        )

# O card de fluxo é preenchido por último, mas aparece no placeholder reservado
# antes do uploader e reflete todas as ações concluídas nesta mesma execução.
with fluxo_etapas.container():
    renderizar_fluxo_etapas(arquivo_presente=True)

mostrar_rodape()
