# Conversor Universal de Arquivos

Aplicativo web desenvolvido com **Python**, **Streamlit** e **Pandas** para leitura, análise e conversão segura de dados entre diferentes formatos tabulares.

O projeto organiza a utilização em um fluxo simples de três etapas:

1. **Enviar:** selecione um arquivo compatível.
2. **Analisar:** valide a estrutura e visualize os dados.
3. **Converter:** escolha o formato de saída e faça o download.

A aplicação foi construída com foco didático, segurança no processamento de arquivos e controle do uso de memória.

## Funcionalidades

- Upload de arquivos por meio da interface do Streamlit.
- Validação da extensão e, quando possível, da assinatura real do arquivo.
- Detecção automática ou seleção manual de encoding e delimitador.
- Seleção da planilha a ser carregada em arquivos Excel.
- Normalização de estruturas JSON e dicionários Python.
- Prévia dos dados antes da conversão.
- Métricas de linhas, colunas e células.
- Conversão controlada por formulário, evitando processamento a cada rerun.
- Avisos sobre possíveis perdas de tipos durante a conversão.
- Prévia limitada do resultado sem truncar o arquivo baixado.
- Cache com tempo de expiração e quantidade máxima de entradas.
- Interface organizada com cards, badges, Material Symbols e feedback de progresso.
- Mensagens e documentação em português.

## Formatos suportados

| Formato | Entrada | Saída | Observações |
|---|:---:|:---:|---|
| CSV | Sim | Sim | Permite detectar ou escolher encoding e delimitador. |
| JSON | Sim | Sim | Normaliza listas de registros e objetos compatíveis com tabelas. |
| XML | Sim | Sim | Utiliza análise segura, sem DTD ou entidades externas. |
| XLSX | Sim | Sim | Permite escolher a planilha que será carregada. |
| TXT | Sim | Sim | Tratado como texto tabular; tabulação é o padrão. |
| Parquet | Sim | Sim | Metadados são inspecionados antes da materialização do DataFrame. |
| Python dict | Sim | Não | Somente um dicionário literal seguro é aceito. O arquivo nunca é executado. |

## Como utilizar

1. Inicie o aplicativo.
2. Selecione um arquivo de entrada.
3. Confira o nome, formato e tamanho identificados.
4. Escolha as opções de leitura disponíveis para o formato.
5. Clique em **Carregar e analisar**.
6. Confira a prévia e as métricas da tabela.
7. Selecione o formato de saída.
8. Clique em **Converter**.
9. Verifique os avisos e a prévia do resultado.
10. Faça o download do arquivo convertido.

As operações de leitura e conversão acontecem somente depois da confirmação do usuário.

## Entrada segura de Python dict

Arquivos `.py` são aceitos apenas como uma forma didática de representar um dicionário literal. Eles **nunca são executados**.

Exemplo válido:

```python
# -*- coding: utf-8 -*-

usuario = {
    "nome": "Carlos Silva",
    "idade": 28,
    "email": "carlos@email.com",
    "ativo": True,
}
```

A aplicação utiliza:

- `ast.parse()` para construir a árvore sintática;
- validação explícita da estrutura externa da AST;
- `ast.literal_eval()` para extrair somente valores literais.

São rejeitados imports, chamadas de função, classes, lambdas, comprehensions, acesso a atributos, múltiplas instruções e qualquer conteúdo executável.

O arquivo deve conter somente:

- um dicionário literal isolado; ou
- uma atribuição simples de um dicionário a um único nome.

Um exemplo pronto está disponível em [`files/dict.py`](files/dict.py).

## Tratamento por formato

### CSV e TXT

- Suporte a UTF-8, UTF-8 com BOM, Windows-1252 e ISO-8859-1.
- Detecção assistida de encoding com `charset-normalizer`.
- Delimitadores suportados: vírgula, ponto e vírgula, tabulação e barra vertical.
- Rejeição de arquivos vazios, texto livre e linhas com quantidades incompatíveis de campos.

### JSON

- Parse inicial com `json.loads()`.
- Suporte a listas de registros, objetos únicos e estruturas aninhadas coerentes.
- Normalização com `pandas.json_normalize()`.
- Rejeição de JSON inválido, vazio, ambíguo ou incompatível com uma tabela.

### XML

- Parser `lxml` configurado sem rede, DTD ou resolução de entidades.
- Rejeição de XXE, entidades externas, expansão de entidades e estruturas profundas.
- Suporte a namespaces e estruturas tabulares reconhecíveis.

### XLSX

- Inspeção da estrutura ZIP antes da leitura.
- Validação dos arquivos internos obrigatórios do workbook.
- Proteção contra caminhos internos suspeitos e possíveis ZIP bombs.
- Seleção da planilha quando o workbook possui múltiplas opções.

### Parquet

- Validação das assinaturas `PAR1` no início e no final.
- Leitura dos metadados antes da criação do DataFrame.
- Verificação prévia de linhas, colunas e tamanho descompactado estimado.

## Segurança

O projeto implementa diferentes camadas de proteção:

- arquivos Python enviados nunca são executados;
- ausência de `exec()`, `eval()`, `runpy` ou importação dinâmica do upload;
- validação de extensão e conteúdo;
- proteção de XML contra DTD, entidades e acesso externo;
- inspeção preventiva de XLSX como arquivo ZIP;
- inspeção preventiva dos metadados de Parquet;
- limites para upload, estruturas, DataFrame e resultado;
- captura de erros esperados e de falta de memória;
- mensagens controladas sem exposição de stack trace na interface;
- processamento realizado na sessão do aplicativo, sem envio dos arquivos para serviços externos.

## Limites preventivos

| Recurso | Limite |
|---|---:|
| Upload | 25 MB |
| DataFrame em memória | 100 MB |
| Resultado convertido | 50 MB |
| Linhas | 500.000 |
| Colunas | 10.000 |
| Células | 5.000.000 |
| XLSX descompactado | 150 MB |
| Entradas internas no XLSX | 5.000 |
| Parquet descompactado estimado | 150 MB |
| Profundidade de JSON, XML e Python dict | 40 níveis |
| Elementos XML | 1.000.000 |

Esses limites são complementares. O limite de upload não é utilizado como única proteção contra arquivos comprimidos ou estruturas que possam consumir memória excessiva.

## Preservação de tipos

Formatos diferentes possuem capacidades diferentes de armazenamento. Antes da conversão, a aplicação analisa os tipos das colunas e informa possíveis perdas.

Exemplos:

- CSV e TXT não preservam o esquema de tipos.
- JSON pode representar datas e números especiais de maneira diferente.
- XML pode converter determinados valores para texto.
- XLSX não aceita valores de data com timezone sem conversão prévia.
- Parquet pode rejeitar objetos Python arbitrários.

Incompatibilidades reais bloqueiam a conversão. Possíveis perdas que não impedem o processo são apresentadas como avisos.

## Cache e estado da sessão

A leitura utiliza `st.cache_data` com limites definidos:

```python
@st.cache_data(
    ttl="10m",
    max_entries=3,
    show_spinner=False,
)
```

O aplicativo também utiliza `st.session_state` para manter o DataFrame e o resultado entre reruns. Quando um novo arquivo é enviado, o estado anterior é descartado.

O arquivo é identificado pelo hash SHA-256 de seus bytes, e não somente pelo nome.

## Tema da interface

A aparência é configurada nativamente em `.streamlit/config.toml`:

| Elemento | Cor |
|---|---|
| Cor principal | `#047857` |
| Fundo principal | `#F9F9FB` |
| Fundo secundário | `#F1F5F9` |
| Texto | `#0F172A` |
| Bordas | `#CBD5E1` |
| Links | `#047857` |

A interface utiliza componentes nativos do Streamlit, sem CSS ou JavaScript injetado.

## Estrutura do projeto

```text
Conversor_Universal/
├── .streamlit/
│   └── config.toml          # Tema e limite de upload
├── files/
│   ├── dict.csv             # Exemplo de CSV
│   ├── dict.json            # Exemplo de JSON
│   └── dict.py              # Exemplo seguro de Python dict
├── tests/
│   ├── test_app.py          # Testes de interface e contrato visual
│   └── test_conversor_core.py
├── conversor_Universal.py                 # Interface Streamlit
├── conversor_core.py        # Leitura, validação, segurança e conversão
├── pytest.ini               # Configuração do pytest
├── requirements.txt         # Dependências de produção
├── requirements-dev.txt     # Dependências de desenvolvimento
└── README.md
```

A separação entre `conversor_Universal.py` e `conversor_core.py` permite testar a lógica de negócio sem precisar iniciar a interface.

## Requisitos

- Python 3.11 ou superior.
- Ambiente validado com Python 3.14.

Dependências diretas:

- Streamlit 1.61.1;
- Pandas 3.0.5;
- OpenPyXL 3.1.5;
- PyArrow 24.0.0;
- lxml 6.1.3;
- charset-normalizer 3.5.1.

## Instalação

No terminal, entre na pasta do projeto:

```bash
cd "/Users/vini_araujo/Documents/GitHub/Conversor_Universal_App/Conversor_Universal"
```

Crie o ambiente virtual:

```bash
python3 -m venv .venv
```

Ative o ambiente no macOS ou Linux:

```bash
source .venv/bin/activate
```

No Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Instale as dependências:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Executando o aplicativo

Com o ambiente virtual ativado:

```bash
streamlit run conversor_Universal.py
```

O Streamlit normalmente disponibilizará o aplicativo em:

```text
http://localhost:8501
```

Para encerrar o servidor, pressione `Control + C` no terminal em que ele está sendo executado.

## Testes automatizados

Instale as dependências de desenvolvimento:

```bash
python -m pip install -r requirements-dev.txt
```

Execute todos os testes:

```bash
python -m pytest
```

A suíte verifica:

- entradas válidas de todos os formatos;
- conversões para todos os formatos de saída;
- normalização de JSON e Python dict;
- arquivos Python maliciosos;
- XML com DTD, XXE e entidades;
- XLSX suspeito ou corrompido;
- Parquet inválido ou truncado;
- limites de memória e estrutura;
- tema e rodapé;
- ausência de APIs depreciadas;
- carregamento, análise, métricas, conversão e download pelo AppTest.

Na última validação local, **67 testes foram aprovados**.

## Exemplos de arquivos

A pasta [`files/`](files/) contém arquivos pequenos que podem ser usados para experimentar o aplicativo:

- [`dict.csv`](files/dict.csv);
- [`dict.json`](files/dict.json);
- [`dict.py`](files/dict.py).

## Tecnologias utilizadas

- **Python:** linguagem principal do projeto.
- **Streamlit:** interface web e gerenciamento da sessão.
- **Pandas:** normalização, análise e conversão tabular.
- **OpenPyXL:** leitura e escrita de arquivos XLSX.
- **PyArrow:** leitura e escrita de arquivos Parquet.
- **lxml:** leitura e geração de XML.
- **charset-normalizer:** detecção assistida de encoding.
- **pytest:** testes automatizados.

## Autor

**Vinicius Araujo**  
Projeto prático de Engenharia de Software — 2026.
