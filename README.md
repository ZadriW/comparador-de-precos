# Comparador de Preços — Dental Odonto Master

Sistema web para comparar preços de produtos odontológicos da **Dental Odonto Master** com
**Dental Cremer**, **Dental Speed**, **Dental Shop** e **Surya Dental**.

## Como funciona

1. Você busca um produto da Odonto Master pelo **SKU** ou pelo **nome**
   - texto com cara de SKU (uma palavra com dígito, ex.: `143026`) → compara direto;
     se o SKU não existir, cai na busca por nome
   - nome (ex.: `sonda exploradora`) → lista os produtos da Odonto Master para escolher;
     com um único resultado, a comparação abre direto
   - enquanto você digita (3+ letras), aparecem sugestões com foto, SKU, marca e preço
     (navegáveis com ↑ ↓ Enter Esc)
2. O sistema consulta a **API GraphQL Wake/Fbits** da Odonto Master e obtém nome, marca, imagem, preço,
   **EAN** e os **atributos da variante** (ex.: "TAMANHO DE LUVA: GRANDE"). O nome não traz a variação —
   o SKU 143026 "Sonda Exploradora - ICE" é a Nº47 —, então ela é somada ao nome usado na comparação
3. Gera termos de busca a partir do nome (`matching.build_queries`) e consulta as 4 lojas em paralelo.
   Em cada loja a busca é em cascata: se o 1º termo não trouxer um produto "exato", tenta o próximo
   (EAN, só na Dental Shop → sem marca → com marca → núcleo curto → "1ª palavra + códigos + marca").
   Em nomes longos, códigos de modelo/cor (Z350, A2) têm prioridade nos termos
   - cada **variação** das lojas (tamanho, numeração, cor) vira um item com SKU, preço e estoque próprios:
     `Specifications` na SmartHint (Cremer/Speed) e `items` na VTEX (Shop)
   - **EAN idêntico** = mesmo produto (exato, selo "Mesmo EAN"); EAN válido e **diferente** limita a nota
     a "provável" (outra variante/embalagem ou cadastro divergente)
4. Pontua cada produto encontrado (`matching.score`) e classifica:
   - **Exato** (≥ 85%), **Provável** (≥ 65%) ou **Similar** (≥ 50%)
   - itens da **mesma marca** (ou marca desconhecida) entram na comparação principal
   - itens de **outra marca** aparecem em "Similares de outras marcas", sem delta destacado
5. A **melhor opção** de cada loja é escolhida nesta ordem: confirmado manualmente → mesmo EAN → maior nível
   (exato > provável > similar) → dentro do nível, preço plausível → entre notas praticamente iguais, marca
   confirmada, em estoque e preço mais próximo do da Odonto Master.
   Preços muito distantes (> 2,5× ou < 0,4×) recebem o aviso "Preço muito diferente"
6. A diferença de preço usa o **preço normal** dos dois lados; o preço no **Pix** é exibido como informação extra

### O que a pontuação considera

| Regra | Exemplo |
|-------|---------|
| Palavras da Odonto Master encontradas e palavras extras no concorrente | "Sonda Exploradora" × "Sonda Exploradora Nº 5 Colors" → Provável |
| Números, medidas e quantidades | Nº 5 × Nº 47, 1,2 mm, 20 ml, c/ 100 |
| Códigos de modelo | Z350 × Z250 |
| "com X" × "sem X" | Luva **com** pó × **sem** pó |
| Kit / DuoPack × unidade, combos com "+" ou "grátis" | "Kit Resina + 30 Discos" |
| Pares opostos | infantil × adulto, procedimento × cirúrgica, curta × longa, superior × inferior, látex × nitrilo |
| "não X" / sem modificador | Estéril × **Não** Estéril; "Luva Látex Pó" × "Luva Látex **Sem** Pó" |
| Sinônimos | c/ = com, s/ = sem, "powder free" = sem pó, talco = pó, nitrílica = nitrilo |
| Quantidades e unidades | "c/ 100" = "Cx 100" = "100 un"; 1 L = 1000 ml; 1 kg = 1000 g; Nº05 = Nº 5 |
| Códigos com letra | 18**L** × 18**R** (fórceps) e "3M" não são lidos como litros/metros |
| Tamanho | Luva **G** × **M**; "Grande" = G, "Extra Pequeno" = PP |
| EAN | igual → exato; diferente → no máximo provável |
| Marca (API da loja e sufixo " - Marca"), com aliases | 3M = Solventum, Millennium = Golgran |

Aliases de marca, pares opostos e sinônimos ficam em `matching.py` (`BRAND_ALIASES`, `_OPPOSITES`,
`_SYNONYMS`) e podem ser ampliados. Depois de mudar uma regra, rode os testes e o `evaluate.py` (abaixo).

### Curadoria manual: "É este" / "Não é"

Em cada produto concorrente da comparação há os botões **É este** e **Não é** (em "Todos os produtos
encontrados" só **É este**, porque o par certo pode ter ficado abaixo da nota mínima):

- **É este** — o produto passa a ser sempre a melhor opção daquele SKU naquela loja (selo "Confirmado").
  Se ele não aparecer nos termos normais, o comparador busca pelo nome dele; se mesmo assim sumir,
  a loja mostra o aviso "O produto que você confirmou não apareceu na busca"
- **Não é** — o produto nunca mais é usado como equivalente daquele SKU
- **Desfazer** — apaga as decisões do SKU naquela loja

As decisões ficam em `data/curadoria.json` (versione esse arquivo: ele é o gabarito da avaliação).

### Medindo a taxa de acerto

```bash
python evaluate.py              # roda a busca automática (sem a curadoria) para os SKUs confirmados
python evaluate.py 143026       # só um SKU
python evaluate.py --min 0.9    # código de saída 1 se acertar menos de 90% (útil em CI)
```

Para cada loja mostra **acerto** (a melhor opção automática é a confirmada), **erro**, **sem par** e
**fora** (o confirmado nem apareceu na busca — problema nos termos), com o detalhe de cada falha.
Quanto mais SKUs confirmados, mais confiável a medida.

### Testes

```bash
python -m unittest discover -s tests -v
```

Testes offline das regras de pontuação, termos de busca, EAN, curadoria e escolha da melhor opção.

### Robustez

- Falhas temporárias (conexão, timeout, HTTP 429/5xx) são repetidas automaticamente com espera crescente
- "Loja indisponível" é diferente de "nenhum produto": respostas fora do formato (API mudou, `shcode`
  da SmartHint inválido, página de bloqueio do WAF na Dental Speed) aparecem como erro da loja
- Se a API da Odonto Master falhar, a tela diz isso (HTTP 503) em vez de "SKU não encontrado"
- Cada busca (loja + termo) fica **15 min em cache**; **Atualizar preços** (ou `refresh=1` na URL) refaz
  as buscas

## Pré-requisitos

- Python 3.10 ou superior
- pip

## Instalação

```bash
# 1. Clone ou baixe o projeto
cd "C:\Users\adriano.almeida\Desktop\Adriano\Preços"

# 2. (Opcional) Crie um ambiente virtual
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate # Linux/Mac

# 3. Instale as dependências
pip install -r requirements.txt
```

## Uso

### Interface Web (recomendado)

```bash
python app.py
```

Acesse no navegador: **http://localhost:5000**

### Linha de comando

Os scrapers usam imports relativos, então rode-os como módulos (`python -m`):

```bash
python -m scrapers.odontomaster
python -m scrapers.dentalcremer "sonda exploradora"
python -m scrapers.dentalspeed  "sonda exploradora"
python -m scrapers.dentalshop   "sonda exploradora"
python -m scrapers.suryadental  "sonda exploradora"

# Comparador completo
python comparator.py 143026
```

### API JSON

```
GET  http://localhost:5000/api/compare?sku=143026            (refresh=1 ignora o cache)
GET  http://localhost:5000/api/search?q=sonda%20exploradora&limit=6
POST http://localhost:5000/api/curadoria
     {"om_sku": "143026", "loja": "Dental Cremer", "acao": "confirmar",
      "produto": {"sku": "56260", "url": "https://...", "nome": "Sonda Exploradora Nº 47 - Ice"}}
```

`acao`: `confirmar`, `rejeitar` ou `limpar` (sem `produto`). Com a Odonto Master fora do ar,
`/api/compare` e `/api/search` respondem 503.

`/api/search` devolve os produtos da Odonto Master (nome, SKU, marca, imagem, preço e `compare_url`);
é o endpoint usado pelas sugestões do campo de busca.

Na interface, `/compare?q=<SKU ou nome>` é a URL do campo de busca e `/compare?sku=<SKU>` abre direto
a comparação de um SKU.

Observação: a busca por nome da Odonto Master devolve uma variação por produto. Variações do mesmo
produto têm o mesmo nome, mas são itens diferentes (ex.: SKU 143026 é a sonda Nº47); a comparação usa
a variação de cada SKU.

Cada loja em `stores` traz `matches`, `similares`, `all_produtos`, `best_match`, `termos_usados`, `erro`,
`curadoria` e `confirmado_ausente`. Cada match traz `relevance_score`, `match_level`, `brand_match`,
`ean_match`, `curadoria`, `reasons`, `price_outlier` e `delta`.

## Estrutura do projeto

```
Preços/
├── app.py                  # Servidor Flask (interface web + API JSON)
├── comparator.py           # Orquestração: busca em cascata, cache, ranqueamento, delta de preço
├── matching.py             # Normalização de nomes, pontuação e termos de busca
├── curation.py             # Curadoria manual ("É este" / "Não é")
├── evaluate.py             # Taxa de acerto da busca automática contra a curadoria
├── requirements.txt        # Dependências Python
├── data/
│   └── curadoria.json      # Decisões manuais (criado no primeiro uso)
├── tests/                  # Testes offline (python -m unittest discover -s tests)
├── scrapers/
│   ├── __init__.py
│   ├── base.py             # ScraperError e helpers de nova tentativa (retry)
│   ├── odontomaster.py     # API GraphQL Wake/Fbits
│   ├── dentalcremer.py     # SmartHintScraper genérico + Dental Cremer
│   ├── dentalspeed.py      # SmartHint (fallback: HTML Magento 2)
│   ├── dentalshop.py       # VTEX Intelligent Search
│   └── suryadental.py      # GraphQL Adobe Commerce
├── templates/
│   └── index.html          # Interface web (Bootstrap 5)
└── static/                 # Arquivos estáticos (CSS/JS extras)
```

## APIs utilizadas

| Loja | Tipo | Endpoint | Observações |
|------|------|----------|-------------|
| Odonto Master | GraphQL (Wake/Fbits) | `https://storefront-api.fbits.net/graphql` | marca via `productBrand` |
| Dental Cremer | REST JSON (SmartHint) | `https://searches.smarthint.co/v3/Search/GetPrimarySearch` | `shcode=SH-743969`, Pix e estoque |
| Dental Speed | REST JSON (SmartHint) | mesmo endpoint | `shcode=SH-176187`; fallback HTML `/catalogsearch/result/` (curl-cffi) |
| Dental Shop | REST JSON (VTEX) | `/api/io/_v/api/intelligent-search/product_search/` | `operator=or` (o padrão "and" esvazia buscas) |
| Surya Dental | GraphQL (Adobe Commerce) | `https://www.suryadental.com.br/graphql` | via curl-cffi; detalhes abaixo |

### Surya Dental — particularidades

- URL do produto = `url_key` + `url_suffix` (`.html`); só `url_key` dá 404
- `price_range.regular_price` já vem com a promoção; o preço "de" vem de `table_price[].price`
  quando `promo_price` é o preço final
- `first_price` é o preço no Pix/boleto à vista (3% de desconto)
- estoque em `stock_status`; produtos configuráveis são expandidos por variante

## Adicionando novas dentais

1. Crie `scrapers/novo_site.py` com um método `buscar(termo, tamanho) -> list[DentalCremerProduct]`
   (preencha `fonte`, `marca` e, se houver, `preco_pix`), levantando `ScraperError` em falhas de rede/API.
   Se a loja usar SmartHint, basta instanciar `SmartHintScraper(shcode, base_url, fonte)`
2. Registre a busca em `PriceComparator._fetchers` (`comparator.py`)
3. Adicione cor e ícone da loja em `STORE_META` (`app.py`) — a interface usa esse dicionário em todas as telas

## Interface

A página de resultado é organizada do geral para o detalhe:

1. **Produto Odonto Master** — foto, marca, SKU e preço de referência
2. **Veredito** — frase direta sobre a posição da OM (menor preço, maior preço ou Nº lugar) e
   indicadores do mercado (menor, média e maior preço dos concorrentes, lojas com o produto)
3. **Ranking de preços** — OM e a melhor opção de cada loja, do menor para o maior preço, com a
   diferença em R$ e %. Verde = concorrente mais caro que a OM; vermelho = concorrente mais barato
4. **Detalhes por loja** (abas) — produto escolhido, outras opções equivalentes, similares de outras
   marcas e todos os produtos retornados pela busca

Os cálculos de ranking e veredito ficam em `app.py` (`_build_overview`, `_build_verdict`); o
template `templates/index.html` só apresenta os dados.

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `PORT` | `5000` | Porta do servidor Flask |
| `FLASK_DEBUG` | `1` | Modo debug (use 0 fora da sua máquina: o debugger do Flask permite executar código) |
