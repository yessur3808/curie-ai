# agent/skills/conversions.py
"""
Conversions skill: handles currency and unit conversions.
Provides natural language interface for converting between currencies and various units.
"""

import logging
import re
from typing import Optional, Dict, Any
from utils.conversions import convert_currency, format_currency_result, get_popular_currencies
from utils.units import convert_unit, format_unit_result

logger = logging.getLogger(__name__)


def extract_conversion_params(message: str) -> Optional[Dict[str, Any]]:
    """
    Extract conversion parameters from a natural language message.
    
    Examples:
        - "convert 100 USD to EUR"
        - "how many miles in 5 km"
        - "what is 25 celsius in fahrenheit"
        - "50 pounds to kg"
    
    Returns:
        Dictionary with:
        {
            'value': float,
            'from_unit': str,
            'to_unit': str,
            'conversion_type': 'currency' or 'unit'
        }
        Or None if no conversion detected
    """
    message = message.lower().strip()
    
    # Pattern 1: "convert X FROM_UNIT to TO_UNIT"
    pattern1 = r'convert\s+([\d.,]+)\s+([a-z\s/]+?)\s+(?:to|into)\s+([a-z\s/]+?)(?:\s|$|[?.!])'
    match = re.search(pattern1, message)
    if match:
        value_str, from_unit, to_unit = match.groups()
        value = float(value_str.replace(',', ''))
        return {
            'value': value,
            'from_unit': from_unit.strip(),
            'to_unit': to_unit.strip(),
        }
    
    # Pattern 2: "X FROM_UNIT to TO_UNIT"
    pattern2 = r'([\d.,]+)\s+([a-z\s/]+?)\s+(?:to|into|in)\s+([a-z\s/]+?)(?:\s|$|[?.!])'
    match = re.search(pattern2, message)
    if match:
        value_str, from_unit, to_unit = match.groups()
        value = float(value_str.replace(',', ''))
        return {
            'value': value,
            'from_unit': from_unit.strip(),
            'to_unit': to_unit.strip(),
        }
    
    # Pattern 3: "what is X FROM_UNIT in TO_UNIT"
    pattern3 = r'what\s+is\s+([\d.,]+)\s+([a-z\s/]+?)\s+in\s+([a-z\s/]+?)(?:\s|$|[?.!])'
    match = re.search(pattern3, message)
    if match:
        value_str, from_unit, to_unit = match.groups()
        value = float(value_str.replace(',', ''))
        return {
            'value': value,
            'from_unit': from_unit.strip(),
            'to_unit': to_unit.strip(),
        }
    
    # Pattern 4: "how many TO_UNIT in X FROM_UNIT"
    pattern4 = r'how\s+many\s+([a-z\s/]+?)\s+(?:in|are\s+in)\s+([\d.,]+)\s+([a-z\s/]+?)(?:\s|$|[?.!])'
    match = re.search(pattern4, message)
    if match:
        to_unit, value_str, from_unit = match.groups()
        value = float(value_str.replace(',', ''))
        return {
            'value': value,
            'from_unit': from_unit.strip(),
            'to_unit': to_unit.strip(),
        }
    
    return None


def is_currency_code(unit: str) -> bool:
    """Check if a unit string is a currency code."""
    unit = unit.upper().strip()
    # Common currency codes are 3 letters
    if len(unit) == 3 and unit.isalpha():
        # Check against popular currencies
        popular = get_popular_currencies()
        if unit in popular:
            return True
    return False


async def handle_conversion(message: str) -> Optional[str]:
    """
    Handle a conversion request from natural language.
    
    Args:
        message: User message requesting a conversion
    
    Returns:
        Formatted conversion result or None if not a conversion request
    """
    params = extract_conversion_params(message)
    if not params:
        return None
    
    value = params['value']
    from_unit = params['from_unit']
    to_unit = params['to_unit']
    
    # Determine if this is a currency or unit conversion
    from_is_currency = is_currency_code(from_unit)
    to_is_currency = is_currency_code(to_unit)
    
    if from_is_currency and to_is_currency:
        # Currency conversion
        try:
            result = await convert_currency(value, from_unit, to_unit)
            if result:
                return format_currency_result(result)
            else:
                return "Sorry, I couldn't perform that currency conversion. Please check the currency codes and try again."
        except Exception as e:
            logger.error(f"Currency conversion error: {e}")
            return "Sorry, there was an error performing the currency conversion."
    
    elif not from_is_currency and not to_is_currency:
        # Unit conversion
        try:
            result = convert_unit(value, from_unit, to_unit)
            if result:
                return format_unit_result(result)
            else:
                return f"Sorry, I couldn't convert from {from_unit} to {to_unit}. Please check that the units are compatible and try again."
        except Exception as e:
            logger.error(f"Unit conversion error: {e}")
            return "Sorry, there was an error performing the unit conversion."
    
    else:
        # Mixed or ambiguous - could not determine type
        return "Sorry, I couldn't determine if you want a currency or unit conversion. Please be more specific."


def is_conversion_query(message: str) -> bool:
    """
    Check if a message is a conversion query.
    
    Args:
        message: User message
    
    Returns:
        True if message appears to be a conversion request
    """
    message = message.lower()
    
    # Keywords that indicate conversion
    conversion_keywords = [
        'convert',
        'how many',
        'how much',
        'what is',
        'what\'s',
    ]
    
    # Check for conversion keywords
    has_keyword = any(keyword in message for keyword in conversion_keywords)
    
    # Check for unit/currency patterns
    has_to_in = ' to ' in message or ' in ' in message or ' into ' in message
    
    # Check for numbers
    has_number = bool(re.search(r'\d', message))
    
    # Special case: "X unit to unit" pattern without explicit keywords
    # E.g., "50 pounds to kg"
    if not has_keyword and has_to_in and has_number:
        # Check if it matches a simple conversion pattern
        pattern = r'^\s*[\d.,]+\s+[a-z\s/]+\s+(?:to|into)\s+[a-z\s/]+\s*[?.!]?\s*$'
        if re.match(pattern, message):
            return True
    
    return has_keyword and has_to_in and has_number
