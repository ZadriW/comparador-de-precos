# Comparador de Preços — Dental Odonto Master

Sistema web para comparar preços de produtos odontológicos entre a **Dental Odonto Master** e a **Dental Cremer**.

## Como funciona

1. Você informa o **SKU** de um produto da Odonto Master
2. O sistema consulta a **API GraphQL Wake/Fbits** da Odonto Master e obtém o nome e preço do produto
3. Usa o nome para buscar na **API SmartHint** da Dental Cremer
4. Exibe os resultados lado a lado com a diferença de preço

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

```bash
# Testar o scraper da Odonto Master
python scrapers/odontomaster.py

# Testar o scraper da Dental Cremer
python scrapers/dentalcremer.py "sonda exploradora"

# Testar o comparador diretamente
python comparator.py 143026
```

### API JSON

```
GET http://localhost:5000/api/compare?sku=143026
```

## Estrutura do projeto

```
Preços/
├── app.py                  # Servidor Flask (interface web)
├── comparator.py           # Lógica central de comparação
├── requirements.txt        # Dependências Python
├── scrapers/
│   ├── __init__.py
│   ├── odontomaster.py     # API GraphQL Wake/Fbits
│   └── dentalcremer.py     # API SmartHint (JSON público)
├── templates/
│   └── index.html          # Interface web (Bootstrap 5)
└── static/                 # Arquivos estáticos (CSS/JS extras)
```

## APIs utilizadas

| Loja | Tipo | Endpoint |
|------|------|----------|
| Odonto Master | GraphQL (Wake/Fbits) | `https://storefront-api.fbits.net/graphql` |
| Dental Cremer | REST JSON (SmartHint) | `https://searches.smarthint.co/v3/Search/GetPrimarySearch` |

## Adicionando novas dentais

Para adicionar um novo site de comparação:

1. Crie um novo arquivo em `scrapers/novo_site.py`
2. Implemente a classe seguindo o padrão de `DentalCremerScraper`
3. Registre o novo scraper em `comparator.py`
4. Atualize o template `templates/index.html` para exibir os novos resultados

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `PORT` | `5000` | Porta do servidor Flask |
| `FLASK_DEBUG` | `1` | Modo debug (0 para produção) |