# utils/conversions.py
"""
Currency conversion utilities using real-time exchange rates.
Uses exchangerate.host API (free, no authentication required).
"""

import logging
import httpx
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Cache exchange rates to minimize API calls
_rate_cache = {}
_cache_timestamp = None
_cache_duration = timedelta(hours=1)


async def get_exchange_rates(base_currency: str = "USD") -> Optional[Dict[str, float]]:
    """
    Fetch current exchange rates from exchangerate.host API.
    
    Args:
        base_currency: Base currency code (e.g., 'USD', 'EUR')
    
    Returns:
        Dictionary of currency codes to exchange rates, or None if failed
    """
    global _rate_cache, _cache_timestamp
    
    # Check if cache is still valid
    now = datetime.now(datetime.UTC) if hasattr(datetime, 'UTC') else datetime.utcnow()
    if _cache_timestamp and _rate_cache and (now - _cache_timestamp) < _cache_duration:
        if base_currency in _rate_cache:
            logger.debug(f"Using cached exchange rates for {base_currency}")
            return _rate_cache[base_currency]
    
    try:
        url = f"https://api.exchangerate.host/latest?base={base_currency.upper()}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.get(url)
            if response.status_code == 200:
                data = response.json()
                if data.get("success", False) and "rates" in data:
                    rates = data["rates"]
                    # Update cache
                    if base_currency not in _rate_cache:
                        _rate_cache[base_currency] = {}
                    _rate_cache[base_currency] = rates
                    _cache_timestamp = now
                    logger.info(f"Fetched {len(rates)} exchange rates for {base_currency}")
                    return rates
                else:
                    logger.error(f"API returned unsuccessful response: {data}")
                    return None
            else:
                logger.error(f"Failed to fetch exchange rates: HTTP {response.status_code}")
                return None
    except httpx.TimeoutException as e:
        logger.error(f"Timeout fetching exchange rates: {e}")
        return None
    except httpx.HTTPError as e:
        logger.error(f"HTTP error fetching exchange rates: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching exchange rates: {e}")
        return None


async def convert_currency(
    amount: float,
    from_currency: str,
    to_currency: str
) -> Optional[Dict[str, Any]]:
    """
    Convert an amount from one currency to another.
    
    Args:
        amount: Amount to convert
        from_currency: Source currency code (e.g., 'USD')
        to_currency: Target currency code (e.g., 'EUR')
    
    Returns:
        Dictionary with conversion details:
        {
            'original_amount': float,
            'original_currency': str,
            'converted_amount': float,
            'converted_currency': str,
            'exchange_rate': float,
            'timestamp': datetime
        }
        Or None if conversion failed
    """
    from_currency = from_currency.upper()
    to_currency = to_currency.upper()
    
    # Handle same currency
    if from_currency == to_currency:
        return {
            'original_amount': amount,
            'original_currency': from_currency,
            'converted_amount': amount,
            'converted_currency': to_currency,
            'exchange_rate': 1.0,
            'timestamp': datetime.now(datetime.UTC) if hasattr(datetime, 'UTC') else datetime.utcnow()
        }
    
    # Fetch exchange rates with from_currency as base
    rates = await get_exchange_rates(from_currency)
    if not rates:
        logger.error(f"Could not fetch exchange rates for {from_currency}")
        return None
    
    # Check if target currency exists in rates
    if to_currency not in rates:
        logger.error(f"Target currency {to_currency} not found in exchange rates")
        return None
    
    exchange_rate = rates[to_currency]
    converted_amount = amount * exchange_rate
    
    return {
        'original_amount': amount,
        'original_currency': from_currency,
        'converted_amount': round(converted_amount, 2),
        'converted_currency': to_currency,
        'exchange_rate': round(exchange_rate, 6),
        'timestamp': datetime.now(datetime.UTC) if hasattr(datetime, 'UTC') else datetime.utcnow()
    }


def get_popular_currencies() -> list:
    """Return a list of popular currency codes."""
    return [
        'USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'NZD',
        'CNY', 'INR', 'BRL', 'ZAR', 'MXN', 'SGD', 'HKD', 'KRW',
        'SEK', 'NOK', 'DKK', 'RUB', 'TRY', 'THB', 'IDR', 'MYR'
    ]


def format_currency_result(conversion: Dict[str, Any]) -> str:
    """
    Format a currency conversion result for display.
    
    Args:
        conversion: Result from convert_currency()
    
    Returns:
        Formatted string for display
    """
    original = conversion['original_amount']
    from_cur = conversion['original_currency']
    converted = conversion['converted_amount']
    to_cur = conversion['converted_currency']
    rate = conversion['exchange_rate']
    
    result = f"💱 Currency Conversion:\n"
    result += f"{original:,.2f} {from_cur} = {converted:,.2f} {to_cur}\n"
    result += f"Exchange rate: 1 {from_cur} = {rate:.6f} {to_cur}"
    
    return result
