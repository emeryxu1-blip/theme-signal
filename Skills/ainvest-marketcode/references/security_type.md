# Security Types

# Background

1) The current database has inconsistent security classification with no clear hierarchy — for example, "overseas ETF" is a flat category.

2) Security type is a fundamental data field used across the product: search, stock detail pages, type-specific content display, etc.

# Goal

Define a hierarchical security type system based on international standards (e.g. CFI) to meet overseas business requirements.

# Security Classification

## Understanding Security Classification

Multiple characters represent the classification:

1) The first character indicates the primary category, the second character the sub-category.

2) Characters 3–N represent additional classification dimensions (e.g. voting rights, circulation type). Note: only the first 2 characters are currently defined.

Example: AAPL's security type is `ES`, meaning primary = Equities, sub = Ordinary shares.

| Primary | | Sub | | | |
| --- | --- | --- | --- | --- | --- |
| Code | Meaning | Code | Meaning | DB Type | Client Label |
| E | Equities | S | Ordinary shares | Overseas ordinary shares | Stock |
| P | Preferred shares | Preferred shares | Stock |
| D | Common Depositary Receipts | Depositary receipts (ordinary) | Stock |
| R | Depositary receipts (preferred) | Depositary receipts (preferred) | Stock |
| U | Unit | UNIT | Stock |
| | | | | | |
| C | Collective Investment Vehicles (Funds) | E | Exchange-traded funds (ETFs) | Overseas ETF | Etf |
| | | | | | |
| R | Rights | W | Warrants | Stock warrants | Not needed yet |
| | | S | Subscription Rights | Rights | Not needed yet |
| | | | | | |
| D | Debt Instruments | B | Bonds | Corporate/financial bonds | Not needed yet |
| C | Convertible Bonds | Convertible bonds | Not needed yet |
| | | | | | |
| O | Options | C | Call Options | | Not needed yet |
| P | Put Options | | Not needed yet |
| | | | | | |
| I | Indices | | | | |
| | | | | | |
| P (to be deprecated; crypto spot/futures/options will move under their respective top-level categories) | Crypto | | | | Crypto |
| | | | | | |
| F | Futures | F | Financial futures | | |
| P | Crypto futures with delivery date | | |
| | | | | | |
| S | Swaps | P | Perpetual crypto contracts | | |
| | | | | | |
| T (differs from CFI encoding because I is already used for Indices) | Spot | F | Foreign exchange | | |
| C | Commodities | | |
| P | Crypto | | |
| | | | | | |
| M | Misc. | | | | |

# Code Table Download

security_config_V1.1.csv
URL: https://cdn.ainvest.com/clientconfigs/common_config/security_config_V1.1.csv
Update frequency: Daily
Note: Does not include option codes.
