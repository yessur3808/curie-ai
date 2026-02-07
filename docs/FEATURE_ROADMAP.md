# Feature Roadmap & Enhancement Plan

This document outlines planned features, their implementation approach, required integrations, and potential enhancements for Curie AI Assistant.

## Table of Contents
1. [Navigation & Traffic](#1-navigation--traffic)
2. [Financial Data & Trading](#2-financial-data--trading)
3. [Currency & Unit Conversions](#3-currency--unit-conversions)
4. [Email Integration](#4-email-integration)
5. [Legal & Tax Knowledge](#5-legal--tax-knowledge)
6. [News Analysis](#6-news-analysis)
7. [Health & Nutrition](#7-health--nutrition)
8. [Implementation Priorities](#implementation-priorities)

---

## 1. Navigation & Traffic

### Description
Provide real-time traffic information and route planning between locations.

### Implementation Steps

#### Phase 1: Basic Integration
1. **API Selection & Setup**
   - Primary: Google Maps API (Directions, Distance Matrix, Traffic)
   - Alternatives: MapBox, OpenStreetMap with OSRM
   - Cost consideration: Google Maps has free tier (40,000 requests/month)

2. **Create Utility Module** (`utils/navigation.py`)
   ```python
   - get_directions(origin, destination, mode='driving')
   - get_traffic_status(origin, destination)
   - get_travel_time(origin, destination, departure_time=None)
   - calculate_distance(origin, destination)
   ```

3. **Intent Detection**
   - Add to `agent/core.py` intent classifier
   - Keywords: "directions", "traffic", "how to get to", "route", "travel time"
   - Extract: origin, destination, travel mode

4. **Agent Routing**
   - Add `navigation` action to SUPPORTED_ACTIONS
   - Implement `handle_navigation()` method

#### Phase 2: Enhancements
- **Multi-modal routing**: Walk, bike, public transit, drive
- **Real-time traffic alerts**: Accidents, construction, delays
- **Alternative routes**: Fastest, shortest, avoid tolls
- **Time-based routing**: Departure/arrival time optimization
- **Waypoint support**: Multi-stop routes
- **Save favorite locations**: Home, work, frequently visited
- **Location sharing**: Share current location or ETA
- **Parking information**: Availability and pricing
- **Gas station finder**: Along route with price comparison
- **Integration with calendar**: Auto-suggest departure times

### Dependencies
```
googlemaps==4.10.0  # or alternative
geopy==2.4.1  # Geocoding support
```

### Security Considerations
- API key protection (environment variables)
- Rate limiting to prevent abuse
- User location privacy
- Validate coordinates to prevent injection

---

## 2. Financial Data & Trading

### Description
Real-time financial market data, analysis, and trading information.

### 2A. Cryptocurrency

#### Implementation Steps

**Phase 1: Price Data**
1. **API Integration** (`utils/crypto.py`)
   - Primary: CoinGecko API (free, no auth required)
   - Alternative: CoinMarketCap API, Binance API
   
2. **Core Functions**
   ```python
   - get_crypto_price(symbol, currency='usd')
   - get_crypto_market_data(symbol)  # volume, market cap, etc.
   - get_price_change(symbol, timeframe='24h')
   - get_trending_coins(limit=10)
   - convert_crypto(amount, from_coin, to_coin)
   ```

3. **Intent Detection**
   - Keywords: "crypto price", "bitcoin", "ethereum", "btc", "eth"
   - Parameters: coin symbol, currency

**Phase 2: Enhancements**
- Price alerts and notifications
- Historical price charts (text-based or image)
- Technical indicators (RSI, MACD, moving averages)
- Portfolio tracking
- News aggregation for specific coins
- Fear & Greed index
- Gas fee tracker (Ethereum)
- DeFi protocol metrics

### 2B. Stock Market

#### Implementation Steps

**Phase 1: Basic Data**
1. **API Integration** (`utils/stocks.py`)
   - Primary: Alpha Vantage (free tier: 25 requests/day)
   - Alternative: Yahoo Finance (yfinance), Financial Modeling Prep
   
2. **Core Functions**
   ```python
   - get_stock_quote(symbol)
   - get_stock_fundamentals(symbol)
   - get_market_status()  # Is market open?
   - get_gainers_losers(limit=10)
   - get_earnings_calendar(symbol=None)
   ```

3. **Market Coverage**
   - US markets (NYSE, NASDAQ)
   - International markets (LSE, TSE, HKEX)
   - Indices (S&P 500, DOW, NASDAQ)

**Phase 2: Enhancements**
- Real-time quotes (with delay disclaimer)
- Pre/post-market data
- Options data and analytics
- Analyst ratings and price targets
- Dividend information
- Insider trading activity
- SEC filings integration
- Sector performance analysis
- Correlation analysis between stocks
- Screener functionality

### 2C. Forex (Foreign Exchange)

#### Implementation Steps

**Phase 1: Exchange Rates**
1. **API Integration** (`utils/forex.py`)
   - Primary: Exchangerate.host (free)
   - Alternative: Alpha Vantage, OANDA
   
2. **Core Functions**
   ```python
   - get_exchange_rate(from_currency, to_currency)
   - get_all_rates(base_currency='USD')
   - get_historical_rate(from_currency, to_currency, date)
   - convert_currency(amount, from_currency, to_currency)
   ```

**Phase 2: Enhancements**
- Major currency pairs tracking (EUR/USD, GBP/USD, etc.)
- Cross-currency triangulation
- Central bank interest rates
- Economic calendar
- Currency strength meter
- Volatility indicators
- Support/resistance levels

### 2D. Trading Capabilities

#### Implementation Approach

**CRITICAL LEGAL NOTICE**: Trading functionality requires:
- Securities broker license
- Compliance with SEC, FINRA regulations
- Terms of service from broker APIs
- User agreements and risk disclaimers

**Phase 1: Educational/Paper Trading**
1. **Paper Trading Simulator** (`utils/trading_simulator.py`)
   - Virtual portfolio management
   - Simulated order execution
   - Track performance without real money
   - Educational purposes only

2. **Strategy Analysis**
   ```python
   - analyze_strategy(strategy_name, parameters)
   - backtest_strategy(symbol, start_date, end_date, strategy)
   - calculate_metrics(trades)  # Sharpe, win rate, etc.
   ```

3. **Risk Management Tools**
   - Position sizing calculator
   - Stop-loss/take-profit calculator
   - Risk-reward ratio analysis
   - Portfolio diversification checker

**Phase 2: Trading Algorithms (Educational)**
- Moving average crossover
- RSI-based strategies
- Bollinger Bands strategies
- MACD strategies
- Mean reversion
- Momentum trading
- Pattern recognition (heads & shoulders, etc.)

**IMPORTANT**: Real trading integration requires:
- Partnership with licensed brokers
- Legal review and compliance
- User verification (KYC)
- Risk disclaimers
- Not recommended for initial implementation

### Dependencies
```
# Crypto
pycoingecko==3.1.0
ccxt==4.2.0  # Exchange integration

# Stocks
alpha-vantage==2.3.1
yfinance==0.2.36

# Forex
forex-python==1.8

# Analysis
pandas==2.2.0
numpy==1.26.3
ta-lib==0.4.28  # Technical analysis
```

### Compliance & Disclaimers
- Clear disclaimers: "Not financial advice"
- Educational purpose statements
- Risk warnings on all trading content
- Links to professional advisors
- Terms of service acceptance

---

## 3. Currency & Unit Conversions

### Description
Convert between currencies and various units of measurement.

### Implementation Steps

#### Phase 1: Core Conversions

1. **Currency Conversion** (`utils/conversions.py`)
   - Already covered in Forex section
   - Use live exchange rates
   - Support 150+ currencies

2. **Unit Conversions** (`utils/units.py`)
   ```python
   # Categories to support:
   - Length: mm, cm, m, km, inch, foot, yard, mile
   - Mass: mg, g, kg, ton, ounce, pound
   - Volume: ml, l, gallon, quart, pint, cup
   - Temperature: Celsius, Fahrenheit, Kelvin
   - Speed: km/h, mph, m/s, knots
   - Area: sq m, sq km, sq ft, sq mile, acre, hectare
   - Energy: joule, calorie, kWh, BTU
   - Power: watt, kilowatt, horsepower
   - Pressure: pascal, bar, psi, atm
   - Force: newton, pound-force
   - Angle: degree, radian, gradian
   - Time: second, minute, hour, day, week, month, year
   - Fuel: mpg, l/100km, km/l
   ```

3. **Intent Detection**
   - Keywords: "convert", "how many", "what is X in Y"
   - Extract: value, from_unit, to_unit

4. **Implementation Library**
   - Use `pint` library for comprehensive unit support
   - Or implement custom conversion factors

#### Phase 2: Enhancements
- Compound units (e.g., "5 ft 11 inches to cm")
- Cooking conversions (cups to tablespoons, etc.)
- Shoe size conversions (US to EU)
- Clothing size conversions
- Timezone conversions (already implemented)
- Age calculations
- Date arithmetic
- Percentage calculations
- Ratio and proportion calculations
- Scientific notation support

### Dependencies
```
pint==0.23  # Unit conversions
# Currency already covered in forex section
```

---

## 4. Email Integration

### Description
Send emails programmatically on behalf of the user.

### Implementation Steps

#### Phase 1: Basic Email Sending

1. **Email Service Setup** (`utils/email_service.py`)
   - SMTP integration (Gmail, Outlook, custom SMTP)
   - OAuth2 authentication (recommended)
   - API integration options:
     - SendGrid API
     - Mailgun API
     - AWS SES

2. **Core Functions**
   ```python
   - send_email(to, subject, body, attachments=None)
   - send_bulk_email(recipients, subject, body)
   - schedule_email(to, subject, body, send_time)
   - validate_email_address(email)
   ```

3. **Intent Detection**
   - Keywords: "send email", "email to", "compose email"
   - Extract: recipient, subject, body content
   - Confirmation required before sending

4. **Security Configuration**
   - Store credentials securely (encrypted)
   - User authentication per email account
   - Rate limiting
   - Spam prevention

#### Phase 2: Enhancements
- **Template management**: Predefined email templates
- **Signature support**: Auto-append user signature
- **Rich text/HTML emails**: Formatting support
- **Attachment handling**: Files, images, documents
- **Email threading**: Reply to existing conversations
- **Contact management**: Address book integration
- **Draft saving**: Save and edit drafts
- **Scheduled sending**: Send at specific time
- **Read receipts**: Track if email was opened
- **Follow-up reminders**: Remind if no response
- **Email parsing**: Extract info from received emails
- **Auto-categorization**: Label/folder management
- **Smart replies**: Suggest quick responses
- **Calendar invites**: Send meeting invitations

### Dependencies
```
# Basic SMTP
smtplib  # Built-in Python
email  # Built-in Python

# API-based
sendgrid==6.11.0
mailgun==0.1.1
boto3==1.34.0  # For AWS SES

# OAuth
google-auth==2.27.0
google-auth-oauthlib==1.2.0
```

### Security Considerations
- OAuth2 preferred over passwords
- Encrypted credential storage
- User consent for each email
- Prevent email spoofing
- Validate recipient addresses
- Rate limiting per user
- Audit log of sent emails

---

## 5. Legal & Tax Knowledge

### Description
Provide information about US laws, regulations, and tax rules.

### Implementation Approach

#### Phase 1: Information Retrieval

1. **Data Sources**
   - IRS Publication Database
   - Legal Information Institute (LII)
   - USA.gov legal resources
   - State-specific legal resources
   - Tax code database

2. **Implementation** (`utils/legal_info.py`)
   ```python
   - search_tax_code(query)
   - get_tax_bracket(income, filing_status, year)
   - get_deduction_info(deduction_type)
   - search_legal_definition(term)
   - get_statute_info(statute_number)
   ```

3. **Knowledge Base**
   - Tax filing deadlines
   - Standard deduction amounts
   - Tax brackets by year
   - Common deductions and credits
   - Basic legal definitions
   - Small business tax info

#### Phase 2: Enhancements
- **Tax calculators**: Estimate tax liability
- **Form assistance**: Help filling out forms
- **State-specific info**: State tax laws
- **Legal document templates**: Basic contracts, wills
- **Timeline calculators**: Statute of limitations
- **Compliance checklists**: Business requirements
- **Update notifications**: Tax law changes

### CRITICAL DISCLAIMERS

**Must include on every response:**
```
⚠️ LEGAL DISCLAIMER:
This information is for educational purposes only and does not 
constitute legal or tax advice. Laws vary by jurisdiction and 
change frequently. Always consult with a qualified attorney or 
certified tax professional for advice specific to your situation.
```

### Implementation Guidelines
- Always cite sources
- Include disclaimer on every response
- Date-stamp information
- Link to official resources
- Suggest professional consultation
- Never provide specific advice for user's situation
- Focus on general information and education

### Dependencies
```
# Web scraping for legal info
beautifulsoup4==4.12.3
requests==2.31.0

# PDF parsing for IRS publications
pypdf2==3.0.1
```

---

## 6. News Analysis

### Description
Analyze and summarize latest news from various sources.

### Implementation Steps

#### Phase 1: News Aggregation

1. **API Integration** (`utils/news.py`)
   - Primary: NewsAPI.org (free tier: 100 requests/day)
   - Alternative: Google News RSS, Bing News API
   - RSS feed aggregation
   
2. **Core Functions**
   ```python
   - get_top_headlines(category=None, country='us')
   - search_news(query, from_date=None, to_date=None)
   - get_news_by_topic(topic)
   - get_news_by_source(source_name)
   - summarize_article(url)
   ```

3. **News Categories**
   - General news
   - Business & finance
   - Technology
   - Science
   - Health
   - Sports
   - Entertainment
   - Politics

#### Phase 2: Analysis Features
- **Sentiment analysis**: Positive/negative/neutral
- **Topic extraction**: Key themes and entities
- **Fact-checking**: Cross-reference with multiple sources
- **Bias detection**: Political lean identification
- **Trending topics**: What's popular now
- **Personalization**: Based on user interests
- **Summary generation**: TL;DR of articles
- **Related articles**: Find similar stories
- **Timeline view**: Track story development
- **Source credibility**: Rate news sources

#### Phase 3: Advanced Features
- **Multi-language support**: Translate foreign news
- **Audio summaries**: Text-to-speech of articles
- **Image analysis**: Extract info from news images
- **Video transcription**: Analyze video news
- **Social media integration**: Twitter/Reddit trends
- **Expert opinion aggregation**: Find analyst views
- **Historical context**: Link to past events
- **Impact analysis**: How news affects markets/society

### Already Available
The `find_info` skill in `agent/skills/find_info.py` already provides:
- Web search capabilities
- Multi-source information gathering
- Content scraping and summarization

**Enhancement needed**: Create specialized news wrapper that:
- Uses NewsAPI for structured data
- Implements caching (news updates hourly)
- Adds sentiment and trend analysis
- Provides category-specific queries

### Dependencies
```
newsapi-python==0.2.7
feedparser==6.0.11  # RSS feeds
newspaper3k==0.2.8  # Article extraction
textblob==0.18.0  # Sentiment analysis
spacy==3.7.2  # NLP for entity extraction
```

---

## 7. Health & Nutrition

### Description
Provide health information, nutrition tips, and wellness guidance.

### Implementation Approach

#### Phase 1: Nutrition Information

1. **Nutrition Database** (`utils/nutrition.py`)
   - USDA FoodData Central API
   - Nutritionix API
   - Open Food Facts
   
2. **Core Functions**
   ```python
   - get_nutrition_info(food_item)
   - calculate_calories(ingredients)
   - get_macro_breakdown(food_item)
   - search_healthy_alternatives(food_item)
   - calculate_bmi(height, weight)
   - calculate_daily_needs(age, gender, activity_level)
   ```

3. **Features**
   - Calorie counting
   - Macro nutrients (protein, carbs, fats)
   - Micro nutrients (vitamins, minerals)
   - Meal planning suggestions
   - Recipe nutrition analysis
   - Dietary restriction filters (vegan, gluten-free, etc.)

#### Phase 2: General Health Information

1. **Knowledge Base**
   - Exercise recommendations (WHO guidelines)
   - Sleep hygiene tips
   - Stress management techniques
   - General wellness advice
   - Preventive care reminders

2. **Fitness Tracking**
   - BMI calculator
   - Ideal weight calculator
   - Calorie burn estimator
   - Hydration calculator
   - Exercise routine suggestions

### CRITICAL MEDICAL DISCLAIMERS

**Must include on every health/nutrition response:**
```
⚠️ MEDICAL DISCLAIMER:
This information is for educational and informational purposes only 
and is not intended as health or medical advice. Always consult a 
physician or other qualified health provider regarding any questions 
about a medical condition or health objectives.
```

### STRICT LIMITATIONS - NEVER PROVIDE:
- ❌ Diagnosis of medical conditions
- ❌ Treatment recommendations
- ❌ Medication advice
- ❌ Interpretation of medical tests
- ❌ Emergency medical guidance
- ❌ Advice on discontinuing medical treatment
- ❌ Personalized medical advice

### ACCEPTABLE TO PROVIDE:
- ✅ General nutrition information
- ✅ Public health guidelines (CDC, WHO)
- ✅ Exercise recommendations (general)
- ✅ Healthy eating tips
- ✅ Stress management techniques
- ✅ Sleep hygiene information
- ✅ Preventive care reminders
- ✅ Direct to healthcare professionals when needed

### Implementation Guidelines
- Always include medical disclaimer
- Cite authoritative sources (CDC, WHO, Mayo Clinic)
- Emphasize consulting healthcare providers
- Provide emergency numbers when relevant (911, poison control)
- Never diagnose or prescribe
- Focus on general wellness and prevention
- Link to professional resources

### Dependencies
```
# Nutrition data
python-usda==0.4.7
nutritionix==1.0.1

# Fitness calculations
scipy==1.12.0  # Scientific calculations
```

---

## Implementation Priorities

### Priority 1: High Impact, Low Complexity
1. ✅ **Currency & Unit Conversions** 
   - Easy to implement
   - High utility
   - No legal concerns
   - Dependencies: `pint==0.23`

2. ✅ **News Analysis Enhancement**
   - Builds on existing `find_info` skill
   - High user value
   - Moderate complexity
   - Dependencies: `newsapi-python==0.2.7`, `feedparser==6.0.11`

3. ✅ **Basic Financial Data** (Crypto/Stock prices only)
   - Free APIs available
   - High interest
   - No trading (legal safety)
   - Dependencies: `pycoingecko==3.1.0`, `yfinance==0.2.36`

### Priority 2: Medium Impact, Medium Complexity
4. **Navigation & Traffic**
   - Very useful feature
   - Requires API costs (Google Maps)
   - Dependencies: `googlemaps==4.10.0`

5. **Email Integration**
   - High utility
   - Security considerations
   - User consent required
   - Dependencies: `sendgrid==6.11.0` or SMTP

6. **Nutrition Information**
   - Educational value
   - Requires careful disclaimers
   - Dependencies: `python-usda==0.4.7`

### Priority 3: Complex, Requires Careful Implementation
7. **Legal & Tax Information**
   - High liability concerns
   - Must have strong disclaimers
   - Limited to general information
   - Dependencies: Web scraping tools

8. **Advanced Financial Analysis**
   - Technical indicators
   - Portfolio tracking
   - Educational trading simulators
   - Dependencies: `ta-lib==0.4.28`, `pandas==2.2.0`

### NOT RECOMMENDED (Without Legal Framework)
- ❌ Real trading execution
- ❌ Medical diagnosis or treatment advice
- ❌ Specific legal advice for user situations
- ❌ Tax filing services (without professional review)

---

## Development Workflow

### For Each Feature:

1. **Planning Phase**
   - Create detailed specification
   - Identify API/data sources
   - Review legal/compliance requirements
   - Estimate costs (API usage)

2. **Implementation Phase**
   - Create utility module
   - Add intent detection
   - Update agent routing
   - Write comprehensive tests

3. **Testing Phase**
   - Unit tests for utility functions
   - Integration tests for agent routing
   - API rate limit testing
   - Error handling validation

4. **Documentation Phase**
   - Update README with new features
   - Create usage examples
   - Document configuration
   - Add disclaimers where needed

5. **Deployment Phase**
   - Add environment variables
   - Update requirements.txt
   - Create migration guide
   - Monitor usage and errors

---

## Configuration Management

### Environment Variables Template
```bash
# Navigation
GOOGLE_MAPS_API_KEY=your_key_here

# Financial
ALPHA_VANTAGE_API_KEY=your_key_here
COINGECKO_API_KEY=optional

# News
NEWS_API_KEY=your_key_here

# Email
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your_email
SMTP_PASSWORD=your_app_password
# Or use SendGrid
SENDGRID_API_KEY=your_key_here

# Nutrition
USDA_API_KEY=your_key_here
NUTRITIONIX_APP_ID=your_id
NUTRITIONIX_API_KEY=your_key_here
```

---

## Cost Estimates

### Free Tier Limits
- **Google Maps**: 40,000 requests/month free
- **Alpha Vantage**: 25 requests/day free
- **NewsAPI**: 100 requests/day free
- **CoinGecko**: 10-50 calls/minute free
- **USDA FoodData**: No limit (public API)

### Paid Plans (if needed)
- **Google Maps**: $5/1000 requests after free tier
- **Alpha Vantage**: $50/month for premium
- **NewsAPI**: $449/month for business
- **SendGrid**: Free for 100 emails/day, $15/month for 40k

### Recommendation
Start with free tiers, implement rate limiting and caching to stay within limits. Monitor usage before committing to paid plans.

---

## Conclusion

This roadmap provides a comprehensive plan for extending Curie's capabilities. Implementation should proceed incrementally, starting with high-value, low-risk features. Always prioritize user safety, legal compliance, and clear communication of limitations.

For questions or suggestions, open a GitHub issue or contribute to the discussion.
