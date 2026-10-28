# Comparador de Preços — Dental Odonto Master

Sistema web para comparar preços de produtos odontológicos da **Dental Odonto Master** com
**Dental Cremer**, **Dental Speed**, **Dental Shop**, **Surya Dental**, **Dental Paiva** e
**Dental MedSul**.

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
3. Gera termos de busca a partir do nome (`matching.build_queries`) e consulta as 6 lojas em paralelo.
   Em cada loja a busca é em cascata: se o 1º termo não trouxer um produto "exato", tenta o próximo
   (EAN, só na Dental Shop → sem marca → com marca → núcleo curto → "1ª palavra + códigos + marca").
   Em nomes longos, códigos de modelo/cor (Z350, A2) têm prioridade nos termos
   - cada **variação** das lojas (tamanho, numeração, cor) vira um item com SKU, preço e estoque próprios:
     `Specifications` na SmartHint (Cremer/Speed/Paiva) e `items` na VTEX (Shop)
   - **EAN idêntico** = mesmo produto (exato, selo "Mesmo EAN"); EAN válido e **diferente** limita a nota
     a "provável" (outra variante/embalagem ou cadastro divergente)
   - **Embalagem pela descrição**: o título nem sempre diferencia kit de unidade (na Dental Cremer o kit com
     2 seringas + primer e a seringa avulsa do Transbond XT têm o mesmo nome). O sistema lê o que vem na
     embalagem — seção "Valor Referente" da Odonto Master, `Description` da SmartHint (Cremer/Speed/Paiva),
     início da `description` da VTEX (Shop), `short_description` da Surya e o resumo do card da MedSul — e compara kit × unidade,
     quantidade (1 × 2 seringas) e conteúdo (2,5 g × 5 g). A descrição aparece abaixo do nome de cada
     produto, e o selo "Mesma embalagem" marca quando ela confere. Quando a Odonto Master informa o
     conteúdo, entra um termo extra no fim da cascata ("Adesivo Ortodôntico Transbond XT 4g")
4. Pontua cada produto encontrado (`matching.score`) e classifica:
   - **Exato** (≥ 85%), **Provável** (≥ 65%) ou **Similar** (≥ 50%)
   - itens da **mesma marca** (ou marca desconhecida) entram na comparação principal
   - itens de **outra marca** aparecem em "Similares de outras marcas", sem delta destacado
5. A **melhor opção** de cada loja é escolhida nesta ordem: mesmo EAN → maior nível
   (exato > provável > similar) → dentro do nível, preço plausível → entre notas praticamente iguais, mesma
   embalagem, marca confirmada, em estoque e preço mais próximo do da Odonto Master.
   Preços muito distantes (> 1,6× ou < 1/1,6 ≈ 0,62×) recebem o aviso "Preço muito diferente".
   Se a melhor opção tiver esse aviso e não for exata nem o mesmo EAN, um produto do **mesmo tipo**
   (mesma primeira palavra), sem conflito no nome (com × sem, tamanho, modelo, medidas) e com preço
   plausível assume o lugar, mesmo com nota a partir de 25%, com o selo "Escolhido pelo preço".
   Se a loja não tiver **nenhuma** correspondência, um produto do mesmo tipo e da mesma marca, com nota a
   partir de 35%, com 75% das palavras do nome da Odonto Master e preço a até ±15% do dela, é confirmado
   pelo preço (mesmo selo)
6. A diferença de preço usa o **preço normal** dos dois lados; o preço no **Pix** é exibido como informação extra

### O que a pontuação considera

| Regra | Exemplo |
|-------|---------|
| Palavras da Odonto Master encontradas e palavras extras no concorrente | "Sonda Exploradora" × "Sonda Exploradora Nº 5 Colors" → Provável |
| Números, medidas e quantidades | Nº 5 × Nº 47, 1,2 mm, 20 ml, c/ 100. Número a mais no concorrente pesa pouco ("Nº25 21mm" × "1ª Série Nº25 21mm"); números divergentes dos dois lados são outro produto (1:50.000 × 1:100.000) |
| Códigos de modelo | Z350 × Z250 |
| "com X" × "sem X" | Luva **com** pó × **sem** pó |
| Kit / DuoPack × unidade, combos com "+" ou "grátis" | "Kit Resina + 30 Discos" |
| Pares opostos | infantil × adulto, procedimento × cirúrgica, curta × longa, superior × inferior, látex × nitrilo |
| "não X" / sem modificador | Estéril × **Não** Estéril; "Luva Látex Pó" × "Luva Látex **Sem** Pó" |
| Sinônimos | c/ = com, s/ = sem, "powder free" = sem pó, talco = pó, nitrílica = nitrilo; no nome do produto, cordless/wireless = sem fio e cores em inglês = português, sem gênero (Black = Preto = Preta) |
| Descritivos genéricos | "Led" e "Bivolt" sem par do outro lado não contam como palavra ausente/extra |
| Erro de digitação | letra dobrada ("Radii-Call" = "Radii Cal"); palavras de 5+ letras quase iguais |
| Acessório **para** o produto | "Baterias **para** Fotopolimerizador Valo" × "Fotopolimerizador Valo" → conflito |
| Quantidades e unidades | "c/ 100" = "Cx 100" = "100 un"; 1 L = 1000 ml; 1 kg = 1000 g; Nº05 = Nº 5 |
| Códigos com letra | 18**L** × 18**R** (fórceps) e "3M" não são lidos como litros/metros |
| Tamanho | Luva **G** × **M**; "Grande" = G, "Extra Pequeno" = PP |
| EAN | igual → exato; diferente → no máximo provável |
| Embalagem pela descrição | "Kit com 2 Seringas… + 1 Primer" × "Embalagem com 1 seringa de 4g"; 30 × 60 unidades; 2,5 g × 5 g. Itens listados com "+", ";", vírgula ou "Acompanha" ("1 unidade. Acompanha 4 baterias, 1 fonte") são kit. Acessórios ("+ 1 ponteira", "+ mandril") não tornam o item kit |
| Marca (API da loja e sufixo " - Marca"), com aliases | 3M = Solventum, Millennium = Golgran |

Aliases de marca, pares opostos e sinônimos ficam em `matching.py` (`BRAND_ALIASES`, `_OPPOSITES`,
`_SYNONYMS`, `_NAME_SYNONYMS`, `_WEAK_WORDS`) e podem ser ampliados, assim como as unidades e acessórios de embalagem (`_CONTAINER_UNITS`,
`_ITEM_UNITS`, `_UNIT_ALIASES`, `_ACCESSORIES`). Depois de mudar uma regra, rode os testes (abaixo).

### Testes

```bash
python -m unittest discover -s tests -v
```

Testes offline das regras de pontuação, termos de busca, EAN e escolha da melhor opção.

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

#### Atalho na área de trabalho

O botão **Instalar app**, no canto superior direito, instala o comparador como
aplicativo no Chrome e no Edge: o navegador cria o ícone na área de trabalho e no
menu Iniciar, e o comparador passa a abrir em janela própria. O botão só aparece
quando o navegador permite instalar (página em HTTPS ou `localhost`, app ainda não
instalado).

Em navegadores que não instalam sites (Firefox, Safari), o botão vira **Criar
atalho** e baixa o arquivo `Comparador de Preços.url` — basta movê-lo para a área
de trabalho.

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
```

Com a Odonto Master fora do ar, `/api/compare` e `/api/search` respondem 503.

`/api/search` devolve os produtos da Odonto Master (nome, SKU, marca, imagem, preço e `compare_url`);
é o endpoint usado pelas sugestões do campo de busca.

Na interface, `/compare?q=<SKU ou nome>` é a URL do campo de busca e `/compare?sku=<SKU>` abre direto
a comparação de um SKU.

Observação: a busca por nome da Odonto Master devolve uma variação por produto. Variações do mesmo
produto têm o mesmo nome, mas são itens diferentes (ex.: SKU 143026 é a sonda Nº47); a comparação usa
a variação de cada SKU.

Cada loja em `stores` traz `matches`, `similares`, `all_produtos`, `best_match`, `termos_usados` e `erro`.
Cada match traz `relevance_score`, `match_level`, `brand_match`, `ean_match`, `reasons`, `price_outlier`
e `delta`.

## Estrutura do projeto

```
Preços/
├── app.py                  # Servidor Flask (interface web + API JSON)
├── comparator.py           # Orquestração: busca em cascata, cache, ranqueamento, delta de preço
├── matching.py             # Normalização de nomes, pontuação e termos de busca
├── requirements.txt        # Dependências Python
├── tests/                  # Testes offline (python -m unittest discover -s tests)
├── scrapers/
│   ├── __init__.py
│   ├── base.py             # ScraperError e helpers de nova tentativa (retry)
│   ├── odontomaster.py     # API GraphQL Wake/Fbits
│   ├── dentalcremer.py     # SmartHintScraper genérico + Dental Cremer
│   ├── dentalspeed.py      # SmartHint (fallback: HTML Magento 2)
│   ├── dentalshop.py       # VTEX Intelligent Search
│   ├── suryadental.py      # GraphQL Adobe Commerce
│   ├── dentalpaiva.py      # SmartHint (feed Wbuy)
│   └── dentalmedsul.py     # HTML da busca do Magento 1 + dataLayer
├── templates/
│   └── index.html          # Interface web (Bootstrap 5)
├── pwa/
│   ├── sw.js               # Service worker (sem cache; só habilita a instalação como app)
│   └── icons/              # Ícones do app / atalho na área de trabalho
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
| Dental Paiva | REST JSON (SmartHint) | mesmo endpoint da Cremer | `shcode=SH-428351`; detalhes abaixo |
| Dental MedSul | HTML (Magento 1) | `https://www.dentalmedsul.com.br/catalogsearch/result/` | via curl-cffi; detalhes abaixo |

### Surya Dental — particularidades

- URL do produto = `url_key` + `url_suffix` (`.html`); só `url_key` dá 404
- `price_range.regular_price` já vem com a promoção; o preço "de" vem de `table_price[].price`
  quando `promo_price` é o preço final
- `first_price` é o preço no Pix/boleto à vista (3% de desconto)
- estoque em `stock_status`; produtos configuráveis são expandidos por variante

### Dental Paiva — particularidades

- Loja na plataforma Wbuy; a busca do site é a SmartHint. A busca HTML e as páginas de produto
  passam por um anti-bot (`/anti-bot-check`), por isso só a API é usada
- `Sku` vem "N/A": o código do produto está em `Mpn`
- o preço no Pix (5% de desconto) vem em `BankSlipPrice`
- todo item traz a variação "Gênero: unisex", ignorada (não é variação do produto)
- catálogo focado em endodontia e equipamentos; a busca da SmartHint é ampla e traz itens sem
  relação, que a pontuação descarta

### Dental MedSul — particularidades

- Magento 1 (Yepcomm), sem API de catálogo. A página de busca traz um `dataLayer` com
  `categoryProducts` (id, nome, SKU e preço normal); link, imagem, resumo da embalagem, preço "de",
  preço à vista (~3% menor, exibido como Pix) e estoque vêm do card `li[data-product-id]`
- produto esgotado: card sem preço e com "Me avise quando chegar"
- com um único resultado, o Magento redireciona para a página do produto (`pageType`
  `catalog/product/view`); o scraper lê essa página também
- configuráveis (luvas P/M/G) aparecem uma vez, sem o tamanho; os tamanhos só estão na página do
  produto e, nos casos vistos, têm o mesmo preço
- a busca exige todas as palavras do termo: "Luva Procedimento Látex Pó" não acha "Luva de Látex
  com Pó - Medix"
- sem EAN; a marca é o sufixo " - Marca" do nome

## Adicionando novas dentais

1. Crie `scrapers/novo_site.py` com um método `buscar(termo, tamanho) -> list[DentalCremerProduct]`
   (preencha `fonte`, `marca` e, se houver, `preco_pix`), levantando `ScraperError` em falhas de rede/API.
   Se a loja usar SmartHint, basta instanciar `SmartHintScraper(shcode, base_url, fonte)`
   (o shcode aparece no HTML da loja como `SH-xxxxxx`; veja `scrapers/dentalpaiva.py`)
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
